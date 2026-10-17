# job-hunter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Превратить vacancy-hunter в `job-hunter` — один проект, где Claude в разговоре ведёт весь поиск работы: настройка → сбор и оценка → черновики откликов → донастройка; личное — в `me/`, вне git.

**Architecture:** Код остаётся в пакете `hunter` (команды `hunter` и `vacancies`). Одна SQLite-база `me/job-hunter.db` с одной цепочкой миграций в `hunter/corpus/store.py`: корпус, состояние прохода, кеш оценок и воронка. Сверху — слой Claude в репозитории: `CLAUDE.md`, проектные skills по этапам и хук начала разговора, который печатает напоминания.

**Tech Stack:** Python ≥3.12, sqlite3, httpx, pyyaml, openpyxl, pytest, uv; Claude Code (skills, hooks, `claude -p`).

**Spec:** `docs/superpowers/specs/2026-10-17-job-hunter-design.md`

## Global Constraints

- Весь текст для человека (вывод команд, ошибки, README, skills, CLAUDE.md) — по-русски; комментарии в коде — по-русски, в стиле существующих.
- Новых зависимостей не добавлять.
- Ничего из `me/` не попадает в git: `me/` целиком в `.gitignore`. Никаких личных файлов в корне проекта.
- Отклики никогда не отправляются автоматически; агент не делает ничего перед работодателем без явного решения владельца.
- Вердикты (`vacancies mark`) — только со слов владельца.
- Код выхода 3 (блокировка hh) — стоп, не повторять; повтор только по команде владельца и не раньше чем через час.
- Телеграм выключен по умолчанию; без `.env` проход работает полностью.
- xlsx — только по запросу, не после каждого прохода.
- Старые `~/projects/personal/job-search/*` и старые базы не изменяются (только чтение).
- Переименование и смена видимости репозитория на GitHub — только по явному «да» владельца в тот момент.
- После каждой задачи `uv run pytest` — зелёный.

## Review Focus

1. **Запуск не из папки проекта** (Claude или владелец в другой cwd): `hunter` и `vacancies` всё равно берут `me/` проекта, а не cwd. → Task 2, `test_defaults_point_into_project_me`.
2. **Свежий клон без `me/`**: `hunter` — код 2 и понятный текст «нет me/config.yaml — пройдите настройку», не traceback; `vacancies todo` — сообщение о первом запуске, код 0. → Task 2 и Task 4.
3. **Проход не затирает богатые данные `vacancies collect`** (card_json, responses_count) бедной карточкой из выдачи. → Task 3, `test_pass_does_not_overwrite_collected_card`.
4. **Неверный ввод в воронку**: вакансии нет в базе, неизвестный статус, кривая дата `--due` — понятная ошибка, код 2, база не тронута. → Task 4.
5. **Донастройка без сети**: `vacancies rescore` не делает ни одного запроса к hh, а при недоступном Claude печатает причину и выходит без traceback. → Task 8.

---

### Task 1: Единая схема базы

Одна цепочка миграций для всего. `Store` (состояние прохода) работает на той же базе и том же соединении, что и `Corpus`; тексты вакансий живут только в `vacancies.description`.

**Files:**
- Modify: `hunter/corpus/store.py` (новый шаг миграции, `note_seen`, статусы воронки)
- Modify: `hunter/store.py` (без своей схемы; тексты через `vacancies`)
- Modify: `hunter/fulltext.py` (сохранять `Detail` целиком)
- Modify: `hunter/corpus/extract.py` (`vacancy_card_row`)
- Modify: `hunter/corpus/GUIDE.md` (новые таблицы)
- Test: `tests/test_store.py`, `tests/test_corpus_store.py`, `tests/test_fulltext.py`, `tests/test_corpus_guide.py`

**Interfaces:**
- Produces:
  - `_MIGRATIONS[1]` (схема v2): таблицы `seen(key TEXT PK, first_seen_at, sent_at)`, `meta(key PK, value)`, `scores(key, criteria_hash, score, why, flags, scored_at, PK(key, criteria_hash))` — как в нынешнем `hunter/store.py`; `applications(vacancy_id INTEGER PK REFERENCES vacancies(id), status TEXT NOT NULL CHECK (status IN (...STATUSES)), note TEXT, test_task TEXT, test_due TEXT, updated_at TEXT NOT NULL)`; `status_history(id INTEGER PK AUTOINCREMENT, vacancy_id INTEGER NOT NULL, status TEXT NOT NULL, at TEXT NOT NULL)`; представление `latest_scores(vacancy_id INTEGER, score, why, flags, scored_at)` — последняя по `scored_at` оценка на вакансию, `vacancy_id` = число после `:` в `scores.key`. Таблицы `texts` больше нет.
  - `hunter.corpus.store.STATUSES = ("selected", "drafted", "test", "applied", "invited", "rejected", "skipped", "closed")` и `STATUS_LABELS: dict[str, str]` = выбрана, черновик готов, тестовое, откликнулся, приглашение, отказ, пропущена, закрыта.
  - `Corpus.note_seen(row: dict) -> bool` — вставить вакансию, если её нет (True), иначе обновить только `last_seen_at` (False).
  - `hunter.corpus.extract.vacancy_card_row(v: Vacancy) -> dict` — все ключи `_CARD_COLUMNS` плюс `id=int(v.vacancy_id)`; `url = VACANCY_URL.format(id=...)`, `published_at = v.published_at.isoformat()`, `gross` 1/0/None, `work_formats` JSON-список, нет данных — None.
  - `Store(path)` — открывает соединение, выполняет `_migrate(conn)` корпуса; атрибут `Store.corpus: Corpus` на том же соединении. Методы `unseen`, `mark_seen`, `is_empty`, `get_score`, `save_score`, `get_meta`, `set_meta` — без изменения сигнатур.
  - `Store.get_text(key: str) -> FullText | None` — из `vacancies` по `int(key.split(":", 1)[1])`, только если `description IS NOT NULL`.
  - `Store.save_text` удаляется; вместо него `store.corpus.save_detail(detail)`.
  - `Store.purge_older_than(days) -> int` — чистит только `seen` и `scores`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_store.py
def test_store_and_corpus_share_one_database(tmp_path):
    db = tmp_path / "job-hunter.db"
    with Store(db) as store:
        store.corpus.note_seen(vacancy_card_row(make_vacancy(vacancy_id="7")))
    with Corpus.open(db) as corpus:
        assert corpus.exists(7)

def test_text_is_read_from_the_corpus(tmp_path):
    with Store(tmp_path / "db") as store:
        store.corpus.save_detail(make_detail(vacancy_id=7, description="Пишем статьи", key_skills=["SEO"]))
        assert store.get_text("hh:7") == FullText("Пишем статьи", ("SEO",))
        assert store.get_text("zarplata:7") == FullText("Пишем статьи", ("SEO",))
        assert store.get_text("hh:8") is None

def test_purge_keeps_corpus_texts(tmp_path): ...
    # seen и scores старше 90 дней удалены, vacancies.description на месте

# tests/test_corpus_store.py
def test_note_seen_inserts_only_once_and_keeps_existing_columns(tmp_path):
    # upsert_card с responses_count=36 и card_json, затем note_seen бедной строкой
    # → False, responses_count == 36, card_json не NULL, last_seen_at обновился

def test_latest_scores_view_takes_newest_score_per_vacancy(tmp_path):
    # две оценки 'hh:7' с разными criteria_hash → в представлении одна, новее

def test_unknown_status_is_rejected_by_the_schema(tmp_path):
    # INSERT INTO applications ... status='foo' → sqlite3.IntegrityError
```

`make_detail` добавить в `tests/factories.py` — `Detail` с заданными полями и `row` из `vacancy_card_row(make_vacancy(vacancy_id=str(vacancy_id)))`.
`tests/test_corpus_guide.py::test_every_column_is_documented` — расширить список таблиц: `seen`, `scores`, `applications`, `status_history` и представление `latest_scores`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_store.py tests/test_corpus_store.py tests/test_corpus_guide.py -q`
Expected: FAIL (`AttributeError: 'Store' object has no attribute 'corpus'`, нет `note_seen`, нет таблиц).

- [ ] **Step 3: Implement**

Миграцию добавлять в конец `_MIGRATIONS` (старые шаги не менять). `Store.__init__`: `sqlite3.connect(..., timeout=30)` → `_migrate(conn)` → `self.corpus = Corpus(conn)`; `Corpus.__init__` ставит `row_factory = sqlite3.Row` — `Store` читает строки по индексу, это совместимо. `fetch_texts`: вместо `store.save_text(...)` — `store.corpus.save_detail(detail)`, в словарь кладётся `FullText(detail.description, tuple(detail.key_skills))`. Обновить существующие тесты, которые зовут `save_text` или читают таблицу `texts`. В GUIDE.md — разделы схемы для новых таблиц и `latest_scores`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: все тесты PASS.

- [ ] **Step 5: Commit**

```bash
git add hunter tests
git commit -m "feat(db): one schema for corpus, pass state, scores and funnel"
```

---

### Task 2: Личная папка `me/`, Телеграм по желанию, сводка в консоль

**Files:**
- Create: `hunter/paths.py`
- Modify: `hunter/config.py` (поле `telegram`)
- Modify: `hunter/run.py` (пути по умолчанию, отправка только при `telegram: true`, сводка в stdout)
- Modify: `hunter/digest.py` (`render_console`)
- Modify: `hunter/corpus/cli.py` (`--db` по умолчанию)
- Modify: `.gitignore`
- Test: `tests/test_paths.py`, `tests/test_config.py`, `tests/test_run.py`, `tests/test_digest.py`, `tests/test_corpus_cli.py`

**Interfaces:**
- Produces:
  - `hunter.paths`: `ROOT: Path` (корень проекта, `Path(__file__).resolve().parent.parent`), `ME = ROOT / "me"`, функции без аргументов, читающие `ME` в момент вызова (чтобы тесты могли подменить `paths.ME` через monkeypatch): `db() -> Path` (`ME/"job-hunter.db"`), `config() -> Path` (`ME/"config.yaml"`), `env() -> Path` (`ME/".env"`), `profile() -> Path` (`ME/"profile.md"`), `logs() -> Path`, `debug() -> Path`, `exports() -> Path`, `applications() -> Path`.
  - `Config.telegram: bool = False`; ключ верхнего уровня `telegram` в YAML, только `true`/`false`, иначе `ConfigError`.
  - `hunter.digest.render_console(groups, *, scores, below: int, threshold: int, warnings: tuple[str, ...]) -> str` — обычный текст без HTML; строка вакансии: `<оценка> · <название> — <компания> · <зарплата> · https://hh.ru/vacancy/<id>`; оценки как в Телеграме (`8`, `6*`, `?`); в шапке — число новых и сколько ниже порога; предупреждения — строками с `⚠️`.
  - `hunter` без флагов: `--config`, `--env`, `--db`, `--log-dir`, `--debug-dir` по умолчанию из `hunter.paths`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_paths.py
def test_defaults_point_into_project_me(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert paths.db() == paths.ROOT / "me" / "job-hunter.db"
    assert paths.config().is_absolute()

# tests/test_config.py
def test_telegram_is_off_by_default(tmp_path): ...       # нет ключа → Config.telegram is False
def test_telegram_must_be_boolean(tmp_path): ...         # telegram: "yes" → ConfigError

# tests/test_run.py
def test_pass_without_telegram_prints_summary_and_marks_seen(tmp_path, capsys):
    # main(["--config", cfg_without_telegram, "--db", ..., "--env", tmp_path/"нет.env"])
    # → 0, в stdout строка "https://hh.ru/vacancy/<id>", Telegram не вызывался,
    # повторный запуск вакансию не печатает
def test_missing_config_points_to_setup(tmp_path, capsys, clean_root_logger):
    # --config на несуществующий файл → EXIT_BAD_CONFIG, в логе/stderr "пройдите настройку"
def test_telegram_on_without_env_is_a_config_error(tmp_path, clean_root_logger): ...

# tests/test_digest.py
def test_console_summary_has_scores_links_and_no_html(): ...

# tests/test_corpus_cli.py
def test_db_defaults_to_project_me(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "ME", tmp_path / "me")
    # run("init") → (tmp_path / "me" / "job-hunter.db").exists()
```

Существующие тесты `test_corpus_cli.py`, которые полагаются на `./vacancies.db` в cwd, перевести на подмену `paths.ME`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_paths.py tests/test_config.py tests/test_run.py tests/test_digest.py tests/test_corpus_cli.py -q`
Expected: FAIL (`ModuleNotFoundError: hunter.paths` и др.).

- [ ] **Step 3: Implement**

В `run.main`: `send` печатает `render_console(...)`, если `config.telegram` ложно (и при `--dry-run`, как сейчас печатает дайджест), иначе `send_messages`; `load_secrets` вызывается только при `config.telegram and not args.dry_run`. Ошибка «файл конфигурации не найден» дополняется: «нет me/config.yaml — пройдите настройку (скажите Claude «давай настроим»)». В `.gitignore` добавить `me/`, `.claude/worktrees/`, `.claude/settings.local.json`; старые строки для корневых `config.yaml`, `state.db`, `exports/`, `logs/`, `debug/` оставить — защищают от случайного коммита старых файлов.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: все PASS.

- [ ] **Step 5: Commit**

```bash
git add hunter tests .gitignore
git commit -m "feat: personal me/ folder, telegram opt-in, console summary"
```

---

### Task 3: Проход пишет в корпус, xlsx по запросу

**Files:**
- Modify: `hunter/run.py` (запись найденного в `vacancies` и `hits`; убрать автоматическую выгрузку, `--export-dir`, `EXIT_EXPORT_FAILED`)
- Modify: `hunter/export.py` (выгрузка из базы)
- Modify: `hunter/corpus/cli.py` (подкоманда `export`)
- Modify: `hunter/corpus/GUIDE.md`
- Test: `tests/test_run.py`, `tests/test_export.py`, `tests/test_corpus_cli.py`

**Interfaces:**
- Consumes: `Corpus.note_seen`, `vacancy_card_row`, `Store.corpus`, `latest_scores`, `STATUS_LABELS` (Task 1); `paths.exports()` (Task 2).
- Produces:
  - В `run_pass` (не в `dry_run`): каждая новая вакансия из `fresh` → `store.corpus.note_seen(vacancy_card_row(v))` и `store.corpus.add_hit(id, role=<имя запроса>, query=<текст запроса>)`.
  - `hunter.export.write_xlsx(rows: list[sqlite3.Row], path: Path) -> None` — колонки: Ссылка, Название, Компания, Оценка, Почему, Флаги, Роль, Статус, Заметка, Найдена. Статус — `STATUS_LABELS[status]` или «новая», если в `applications` строки нет. Выпадающий список статусов из `STATUS_LABELS` + «новая».
  - `hunter.export.export_rows(corpus: Corpus, days: int | None) -> list[sqlite3.Row]` — вакансии с `first_seen_at` за последние `days` дней (None — все), по убыванию `first_seen_at`, с LEFT JOIN `latest_scores`, `applications`, первым `hits.role`.
  - `vacancies export [--days N] [--out PATH]` — по умолчанию `paths.exports()/vacancies-ГГГГ-ММ-ДД_ЧЧММСС.xlsx`, печатает путь и число строк; пустая выборка — сообщение, файла нет, код 0.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_run.py
def test_new_vacancies_land_in_the_corpus_with_their_query(tmp_path):
    # run_pass с одной вакансией 'hh:7' по запросу "Python backend"
    # → corpus.exists(7), corpus.hits(7)[0]["role"] == "Python backend"
def test_pass_does_not_overwrite_collected_card(tmp_path):
    # до прохода upsert_card(id=7, responses_count=36, card_json='{...}')
    # → после прохода responses_count == 36 и card_json не NULL
def test_dry_run_writes_nothing_to_the_corpus(tmp_path): ...
def test_pass_never_writes_xlsx(tmp_path): ...  # в tmp_path нет *.xlsx после прохода

# tests/test_export.py
def test_export_has_score_status_and_role(tmp_path):
    # вакансия с оценкой 8 и статусом drafted → строка с 8, «черновик готов», ролью
def test_vacancy_without_application_is_new(tmp_path): ...   # статус «новая»
def test_days_limits_rows(tmp_path): ...

# tests/test_corpus_cli.py
def test_export_with_nothing_to_export_makes_no_file(tmp_path, monkeypatch, capsys): ...
```

Удалить тесты автоматической выгрузки (`test_new_vacancies_are_exported_to_xlsx`, `test_no_file_*`, `test_failed_export_*`, `test_main_exports_into_the_given_directory`, проверки xlsx в `test_below_threshold_goes_to_xlsx_not_telegram` и `test_all_below_threshold_sends_header_and_writes_xlsx` — оставить их часть про Телеграм).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_run.py tests/test_export.py tests/test_corpus_cli.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

Имя → текст запроса взять из `config.queries` (группы после `dedupe` идут по имени). Код выхода 5 убрать из `run.py`; в README таблицу кодов поправит Task 10. GUIDE.md — раздел `vacancies export`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: все PASS.

- [ ] **Step 5: Commit**

```bash
git add hunter tests
git commit -m "feat: pass records vacancies in the corpus, xlsx export on request"
```

---

### Task 4: Воронка и напоминания

**Files:**
- Create: `hunter/corpus/funnel.py`
- Modify: `hunter/corpus/store.py` (`set_status`, `application`)
- Modify: `hunter/corpus/cli.py` (подкоманды `status`, `funnel`, `todo`)
- Modify: `hunter/corpus/GUIDE.md`
- Test: `tests/test_corpus_funnel.py`, `tests/test_corpus_cli.py`

**Interfaces:**
- Consumes: `STATUSES`, `STATUS_LABELS`, таблицы `applications`, `status_history` (Task 1); `paths.profile()`, `paths.db()` (Task 2).
- Produces:
  - `Corpus.set_status(vacancy_id: int, status: str, note: str | None = None, *, test_task: str | None = None, test_due: date | None = None) -> None` — upsert в `applications` + строка в `status_history`; `note`/`test_*` = None не затирают прежние значения. Нет вакансии → `CorpusError("Вакансии N нет в базе. Сначала: vacancies fetch N")`; неизвестный статус → `CorpusError` со списком допустимых.
  - `hunter.corpus.funnel.reminders(corpus: Corpus, today: date) -> list[str]` — пороги: `TEST_SOON_DAYS = 2` (тестовое со сроком ≤ сегодня+2 или просроченное), `DRAFT_STALE_DAYS = 3` (`drafted` дольше 3 дней), `NO_ANSWER_DAYS = 7` (`applied` дольше 7 дней без смены статуса). Отсчёт — по последней записи `status_history`. Строка: `<что> · <название> — <компания> (id N)`.
  - `vacancies status ID|URL STATUS ["заметка"] [--test "что просят"] [--due ГГГГ-ММ-ДД]` — коды как у `mark`; кривая `--due` → код 2.
  - `vacancies funnel` — таблица вакансий с записью в `applications`, кроме `skipped`/`closed`, по порядку `STATUSES`: id, название, компания, статус (по-русски), срок тестового, заметка.
  - `vacancies todo` — нет `paths.profile()` → одна строка «Первый запуск: me/profile.md нет. Начни настройку (skill setup).», код 0; нет базы → то же поведение, без ошибки; иначе — `reminders(...)`, пустой список — ничего не печатает. Всегда код 0 (его зовёт хук начала разговора).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_corpus_funnel.py
def test_status_change_is_recorded_in_history(tmp_path): ...
def test_note_survives_a_later_status_without_note(tmp_path): ...
def test_status_for_unknown_vacancy_is_an_error(tmp_path): ...
def test_unknown_status_is_an_error(tmp_path): ...
def test_test_due_tomorrow_is_reminded(tmp_path): ...       # today=2026-10-17, due=2026-10-18
def test_test_due_in_five_days_is_not_reminded(tmp_path): ...
def test_overdue_test_is_reminded(tmp_path): ...
def test_applied_eight_days_ago_is_reminded_six_is_not(tmp_path): ...
def test_drafted_four_days_ago_is_reminded(tmp_path): ...

# tests/test_corpus_cli.py
def test_todo_on_fresh_clone_suggests_setup(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(paths, "ME", tmp_path / "me")
    assert run("todo") == 0
    assert "Первый запуск" in capsys.readouterr().out
def test_todo_prints_nothing_when_nothing_is_due(tmp_path, monkeypatch, capsys): ...
def test_bad_due_date_is_a_usage_error_and_db_is_untouched(tmp_path, monkeypatch): ...
def test_funnel_hides_skipped_and_closed(tmp_path, monkeypatch, capsys): ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_corpus_funnel.py tests/test_corpus_cli.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement** — по Interfaces; время в `status_history.at` — ISO UTC через `Corpus._clock`, `today` для `reminders` — локальная дата в CLI. GUIDE.md — три подкоманды и пример SQL для статистики по воронке.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: все PASS.

- [ ] **Step 5: Commit**

```bash
git add hunter tests
git commit -m "feat(funnel): statuses, history and reminders"
```

---

### Task 5: Проверка оценки на вердиктах владельца + замер нынешнего промпта

**Files:**
- Create: `hunter/evaluate.py`
- Modify: `hunter/corpus/cli.py` (подкоманда `eval`)
- Modify: `hunter/corpus/GUIDE.md`
- Test: `tests/test_evaluate.py`

**Interfaces:**
- Consumes: `score_vacancies`, `load_criteria`, `Criteria` (`hunter/scoring.py`), `Store` (Task 1), `load_config`, `paths.config()`.
- Produces:
  - `hunter.evaluate.vacancy_from_row(row: Mapping) -> Vacancy` — строка `vacancies` → `Vacancy(source="hh", ...)`; `work_formats` из JSON, `published_at` из ISO (None → эпоха UTC), `is_ad=False`, `salary_mode=None`.
  - `hunter.evaluate.load_verdicts(path: Path) -> list[tuple[Vacancy, FullText, str]]` — открывает базу **напрямую** через `sqlite3` в `mode=ro` (не через `Corpus.open_readonly`: у старой базы схема v1), берёт вакансии с вердиктом и `description IS NOT NULL`.
  - `hunter.evaluate.Report` (dataclass): `total`, `mean_by_verdict: dict[str, float]`, `agreement: float` (доля, где `yes` ≥ порога, а `no` < порога; `maybe` не считается), `misses: list[tuple[int, str, str, int, str]]` (id, название, вердикт, оценка, why), `unscored: int`.
  - `hunter.evaluate.evaluate(items, *, ask, criteria, threshold: int) -> Report` — оценивает через `score_vacancies` с `Store(":memory:")`, ничего не пишет в оцениваемую базу.
  - `vacancies eval --from PATH [--config PATH]` — печатает отчёт: всего, средние по вердиктам, согласие с порогом, список промахов. Критерии и порог — из конфига (`--config`, по умолчанию `paths.config()`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_evaluate.py
def test_agreement_counts_yes_above_and_no_below_threshold(tmp_path):
    # ask-заглушка: yes-вакансии 8, одна no-вакансия 7, вторая 3; порог 5
    # → agreement == 2/3, misses содержит вакансию no с оценкой 7
def test_maybe_is_excluded_from_agreement(tmp_path): ...
def test_source_database_is_not_modified(tmp_path):
    # хеш файла старой базы до и после evaluate совпадает
def test_old_schema_database_is_readable(tmp_path):
    # база, созданная только миграцией v1 (PRAGMA user_version = 1) → load_verdicts работает
def test_vacancies_without_text_are_skipped(tmp_path): ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_evaluate.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement** — по Interfaces.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: все PASS.

- [ ] **Step 5: Commit**

```bash
git add hunter tests
git commit -m "feat: evaluate scoring against the owner's verdicts"
```

- [ ] **Step 6: Замерить нынешний промпт (тратит подписку Claude, ~9 вызовов)**

Run (из папки, где выполняется план; пути к личным файлам — абсолютные, потому что в worktree их нет):
`mkdir -p ~/projects/personal/vacancy-hunter/me/eval && uv run vacancies eval --from ~/projects/personal/job-search/vacancies.db --config ~/projects/personal/vacancy-hunter/config.yaml | tee ~/projects/personal/vacancy-hunter/me/eval/baseline.txt`
(корневой `config.yaml` основной папки — нынешний, с `portrait`/`conflict` из job-search.)
Expected: отчёт с числом вакансий > 0 и строкой согласия. Если Claude недоступен — остановиться и сказать владельцу.

---

### Task 6: Перенос личных файлов владельца в `me/`

Без кода. Делается в основной рабочей папке (не в worktree), с участием владельца.

**Files (все — в `me/`, вне git):**
- Create: `me/profile.md`, `me/strategy.md`, `me/resumes/…`, `me/applications/<id>-<компания>/draft.md`, `me/config.yaml`, `me/.env`, `me/archive/…`

- [ ] **Step 1:** Скопировать без изменений: `job-search/strategy.md` → `me/strategy.md`; `job-search/resumes/` → `me/resumes/` (с подпапкой `hh/`); `job-search/letters/<id>-<компания>.md` → `me/applications/<id>-<компания>/draft.md`.
- [ ] **Step 2:** Собрать `me/profile.md` из трёх файлов: `# Профиль` → `## Кто я` (весь `job-search/profile.md`), `## Что ищу` (весь `portrait.md`), `## Красные флаги` (весь `conflict.md`). Текст не сокращать и не переписывать — только заголовки на уровень ниже и стыки. Проверка: каждая непустая строка трёх исходников (кроме заголовков первого уровня) встречается в `me/profile.md` — проверить скриптом в scratchpad.
- [ ] **Step 3:** Перенести корневые `config.yaml` и `.env` → `me/` (`mv`). В `me/config.yaml` секцию `scoring` пока не трогать — её меняет Task 7.
- [ ] **Step 4:** Корневые `state.db`, `state.db.bak-*`, `exports/`, `logs/`, `debug/` (если есть) → `me/archive/` без изменений (`mv`). После этого в корне `git status --ignored` не показывает личных файлов, кроме `.venv` и кешей.
- [ ] **Step 5:** Показать владельцу `me/profile.md` и попросить подтвердить. Ничего не коммитить (всё в `me/`).

---

### Task 7: Критерии из одного `profile.md`

**Files:**
- Modify: `hunter/config.py` (`ScoringConfig.profile`)
- Modify: `hunter/scoring.py` (`Criteria.profile`, промпт v2)
- Modify: `config.example.yaml`
- Test: `tests/test_config.py`, `tests/test_scoring.py`, `tests/test_run.py`

**Interfaces:**
- Consumes: `paths` (Task 2).
- Produces:
  - `ScoringConfig(profile: Path, threshold: int = 5, model: str = "sonnet")`; поля YAML `scoring`: `enabled`, `profile`, `threshold`, `model`. `profile` по умолчанию `profile.md`; относительный путь — от папки конфига; `~` раскрывается. `portrait`/`conflict` → `ConfigError` «устарело: критерии теперь в одном profile.md (разделы «Что ищу» и «Красные флаги»)».
  - `Criteria(profile: str, model: str = "sonnet")`; `PROMPT_VERSION = 2`; `criteria_hash` хеширует `profile` вместо пары.
  - Промпт: один блок `# Профиль: кто ищет работу, что ему подходит и красные флаги\n\n{profile}`; правила оценки те же по смыслу, но ссылаются на разделы «Что ищу» (роли, «да»/«нет», общие рамки) и «Красные флаги» (тяжёлые и мягкие). Правило «три и больше тяжёлых — ниже 5» дословно сохраняется. Раздел «Кто я» — контекст, сам по себе оценку не меняет.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config.py
def test_profile_path_is_relative_to_config(tmp_path): ...     # me/config.yaml + profile: profile.md → me/profile.md
def test_profile_defaults_to_profile_md(tmp_path): ...
def test_old_portrait_and_conflict_keys_explain_the_change(tmp_path): ...  # ConfigError с «profile.md»

# tests/test_scoring.py
def test_system_prompt_contains_the_profile_and_the_three_flags_rule():
    prompt = build_system_prompt(Criteria(profile="## Что ищу\nавтор"))
    assert "## Что ищу\nавтор" in prompt
    assert "три и больше тяжёлых признака — ниже 5" in prompt
def test_editing_profile_changes_the_hash(): ...
```

Обновить остальные тесты, создающие `Criteria(portrait=..., conflict=...)` или `scoring.portrait`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py tests/test_scoring.py tests/test_run.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement** — по Interfaces. В `config.example.yaml` секция `scoring` с `profile: profile.md` и комментарием, что конфиг лежит в `me/`. В `me/config.yaml` владельца заменить `portrait`/`conflict` на `profile: profile.md` (вне git).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: все PASS.

- [ ] **Step 5: Commit**

```bash
git add hunter tests config.example.yaml
git commit -m "feat(scoring): criteria from one profile.md"
```

- [ ] **Step 6: Сравнить с замером (тратит подписку)**

Run: `uv run vacancies eval --from ~/projects/personal/job-search/vacancies.db --config ~/projects/personal/vacancy-hunter/me/config.yaml | tee ~/projects/personal/vacancy-hunter/me/eval/profile.txt` и сравнить с `me/eval/baseline.txt` основной папки.
Expected: согласие с порогом не ниже, чем в замере. Показать владельцу обе цифры и новые промахи. Если хуже — не править промпт молча: сказать владельцу и предложить варианты.

---

### Task 8: Донастройка без сети — `vacancies rescore`

**Files:**
- Modify: `hunter/evaluate.py` (общая часть с `eval`)
- Modify: `hunter/corpus/cli.py` (подкоманда `rescore`)
- Modify: `hunter/corpus/GUIDE.md`
- Test: `tests/test_evaluate.py`, `tests/test_corpus_cli.py`

**Interfaces:**
- Consumes: `vacancy_from_row`, `score_vacancies`, `Store`, `latest_scores` (Tasks 1, 5, 7).
- Produces:
  - `hunter.evaluate.rescore(store: Store, *, ask, criteria, days: int) -> list[tuple[Vacancy, int | None, Score]]` — вакансии с `first_seen_at` за `days` дней и `description IS NOT NULL`; прежняя оценка — из `latest_scores` до пересчёта; новая — через `score_vacancies` (кешируется с новым хешем). Тексты — только из базы, `fetch_page` не нужен вовсе.
  - `vacancies rescore [--days N=7] [--config PATH]` — печатает: сколько прошло бы порог было/стало и список изменившихся (`было → стало · название — why`), сначала поднявшиеся выше порога, затем упавшие ниже. При `ClaudeError` — строка с причиной, код 1.

- [ ] **Step 1: Write the failing tests**

```python
def test_rescore_reports_crossings_of_the_threshold(tmp_path): ...  # было 4, стало 7 → в «поднялись»
def test_rescore_never_touches_the_network(tmp_path, monkeypatch):
    monkeypatch.setattr("hunter.fetch.get_html", boom)  # любой вызов → AssertionError
    # rescore(...) отрабатывает
def test_rescore_with_claude_down_exits_1_with_reason(tmp_path, monkeypatch, capsys): ...
def test_rescore_skips_vacancies_without_text(tmp_path): ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_evaluate.py tests/test_corpus_cli.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement** — по Interfaces. `score_vacancies` при недоступном Claude возвращает `Score(None, …)` с предупреждением, а не исключение — `rescore` CLI считает «все без оценки и есть предупреждение» отказом Claude и выходит с кодом 1.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: все PASS.

- [ ] **Step 5: Commit**

```bash
git add hunter tests
git commit -m "feat: rescore cached vacancies after tuning, offline"
```

---

### Task 9: Слой Claude — шаблоны, `init`, skills, хук, CLAUDE.md

**Files:**
- Create: `templates/profile.md`, `templates/strategy.md`
- Create: `CLAUDE.md`
- Create: `.claude/settings.json`
- Create: `.claude/skills/setup/SKILL.md`, `.claude/skills/collect/SKILL.md`, `.claude/skills/apply/SKILL.md`, `.claude/skills/tune/SKILL.md`
- Modify: `hunter/corpus/cli.py` (`init` без CLAUDE.md; создаёт `me/` и базу)
- Modify: `hunter/corpus/GUIDE.md` (только справочник команд и схемы)
- Test: `tests/test_corpus_cli.py`, `tests/test_claude_layer.py`

**Interfaces:**
- Consumes: все подкоманды `vacancies` (Tasks 1–8), `paths`.
- Produces:
  - `vacancies init` — создаёт `paths.ME` и базу; существующее не трогает; `CLAUDE.md` и `CLAUDE_TEMPLATE` больше не создаются (удалить).
  - `templates/profile.md` — три раздела «Кто я», «Что ищу» (роли с «да»/«нет», общие рамки: формат, деньги, занятость), «Красные флаги» (тяжёлые и мягкие) с подсказками в `>`-цитатах; `templates/strategy.md` — «Позиционирование», «Что сделать до откликов», «Очередь».
  - `.claude/settings.json`: хук `SessionStart` с командой `vacancies todo 2>/dev/null || true`.
  - `CLAUDE.md` (корень, публичный): что это за проект в 3–4 строки; карта этапов → skill; правила из Global Constraints про вердикты, отклики, код 3; где что лежит (`me/…`); строка `@hunter/corpus/GUIDE.md`.
  - Skills (каждый — frontmatter `name`, `description` с триггерами словами владельца, затем короткие шаги):
    - `setup` — первый запуск или «давай настроим»: цикл из нынешнего GUIDE «Цикл работы» (рассказ → примеры с `fetch`/`mark` → `profile.md` по шаблону → запросы в `me/config.yaml` по `config.example.yaml` → пробный `vacancies collect` и отбор → резюме `me/resumes/<роль>.md` → `me/strategy.md`), `vacancies init` в начале.
    - `collect` — «собери свежие», «что нового»: `hunter` (или `hunter --days N` после перерыва; долго — запускать в фоне), прочитать сводку, показать владельцу лучшие с номерами и id, число отсеянных, предупреждения. Код 3 — стоп.
    - `apply` — «черновики по 2, 5, 7»: на каждую — `vacancies fetch` при необходимости, `vacancies show`, прочитать `me/profile.md`, `me/resumes/`, `me/strategy.md`; написать `me/applications/<id>-<компания-латиницей>/draft.md` с разделами «Резюме» (какое и почему), «Письмо» (обычный текст для hh, до ~1200 знаков: зачем пишу → 2–3 доказательства с цифрами и ссылками из резюме → чем буду полезен; без штампов и без markdown), «Вопросы работодателя» (если есть), «Тестовое» (что и срок, если есть); если в `me/applications/` есть удачные письма — ориентироваться на них по тону; затем `vacancies status <id> drafted` или `test --test … --due …`. Отклик не отправлять.
    - `tune` — «слишком много X», «подними порог»: правка `me/profile.md` или `me/config.yaml` → `vacancies rescore` → показать, что изменилось; вердикты — только со слов владельца.
    - Статистика («покажи статистику за сегодня») — раздел в `CLAUDE.md`: отвечать через `vacancies sql` по примерам из GUIDE, коротко, словами.
  - GUIDE.md: убрать «Цикл работы», «Правила» (переехали в CLAUDE.md и `setup`), «Файлы папки»; заголовок — «Команды и база job-hunter»; добавить примеры SQL статистики: «за сегодня» (собрано, оценено, прошло порог), «всего» (по ролям, по статусам воронки).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_claude_layer.py
ROOT = Path(__file__).resolve().parent.parent

def test_every_skill_has_name_and_description(): ...       # 4 файла, frontmatter с name и description
def test_claude_md_imports_the_guide():
    assert "@hunter/corpus/GUIDE.md" in (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
def test_session_start_hook_runs_todo():
    settings = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
    commands = [h["command"] for e in settings["hooks"]["SessionStart"] for h in e["hooks"]]
    assert any("vacancies todo" in c for c in commands)
def test_templates_have_the_three_profile_sections():
    text = (ROOT / "templates/profile.md").read_text(encoding="utf-8")
    for section in ("## Кто я", "## Что ищу", "## Красные флаги"):
        assert section in text
def test_no_personal_paths_in_public_files():
    # во всех отслеживаемых git файлах (git ls-files) нет "job-search" и "/home/"
    # (кроме docs/superpowers — история решений)

# tests/test_corpus_cli.py
def test_init_creates_me_and_database_only(tmp_path, monkeypatch): ...  # CLAUDE.md не создаётся
```

Удалить `test_init_creates_database_and_claude_md`, `test_init_never_overwrites` переписать на базу.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_claude_layer.py tests/test_corpus_cli.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement** — по Interfaces. Skills — коротко: каждый ≤ ~60 строк.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: все PASS.

- [ ] **Step 5: Проверить хук вживую**

Run: `vacancies todo` в папке проекта (после `uv tool install --editable . --reinstall`).
Expected: при наличии `me/profile.md` — пусто или напоминания; без него (`ME` подменить нельзя — проверить на временном клоне `git clone . /tmp/...`) — строка «Первый запуск».

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md .claude/settings.json .claude/skills templates hunter tests
git commit -m "feat: Claude layer — skills per stage, session reminders, templates"
```

---

### Task 10: README-витрина, название `job-hunter`

**Files:**
- Modify: `README.md` (переписать)
- Modify: `pyproject.toml` (`name = "job-hunter"`)
- Modify: `config.example.yaml` (без ссылок на job-search)
- Modify: `docs/backlog.md` (пути, если упомянуты)
- Test: `tests/test_claude_layer.py::test_no_personal_paths_in_public_files` (уже есть)

- [ ] **Step 1:** README по спеку, раздел «README как витрина»: первым экраном — весь путь (опрос → критерии → сбор и оценка → черновики → тестовое), на каждом шаге — что делает ИИ и ссылка на skill или модуль (`.claude/skills/setup/SKILL.md`, `hunter/scoring.py`, `hunter/evaluate.py`, …); затем «Как это работает на деле» — агрегаты с базы владельца и цифра согласия оценки с его вердиктами из `me/eval/profile.txt` (только числа, без компаний и текстов); затем установка (`uv sync`, `uv tool install --editable .`, открыть Claude Code в папке — он начнёт настройку), шпаргалка команд, коды выхода (без кода 5), Телеграм как опция. По-русски.
- [ ] **Step 2:** Run: `uv sync && uv run pytest -q` → все PASS; `uv tool install --editable . --reinstall` → команды `hunter`, `vacancies` работают.
- [ ] **Step 3: Commit**

```bash
git add README.md pyproject.toml uv.lock config.example.yaml docs/backlog.md
git commit -m "docs: README as a showcase, project renamed to job-hunter"
```

---

### Task 11: Проверка вживую с владельцем

После слияния ветки в `master` основной папки и `uv tool install --editable . --reinstall` (установленные команды смотрят на код основной папки, а не worktree).

- [ ] **Step 1:** В разговоре: «собери свежие за неделю» → skill `collect` → `hunter --days 7` на пустой `me/job-hunter.db`. Expected: код 0, сводка с оценками, вакансии в базе (`vacancies sql "SELECT count(*) FROM vacancies"` > 0).
- [ ] **Step 2:** «покажи статистику за сегодня» → ответ по базе.
- [ ] **Step 3:** Владелец выбирает 1–2 вакансии → skill `apply` → `me/applications/<id>-…/draft.md`, статус `drafted`. Владелец оценивает письма; правки — в skill `apply`.
- [ ] **Step 4:** «выгрузи в таблицу» → xlsx в `me/exports/`.
- [ ] **Step 5:** Новый разговор → хук печатает напоминания (или ничего).

---

### Task 12: Аудит истории, переименование и открытие репозитория

- [ ] **Step 1: Аудит.** Просмотреть всю историю: `git log --all -p` по всем файлам, кроме `uv.lock` и `tests/fixtures/*.html` (фикстуры — проверить отдельно на личные запросы). Искать: город, деньги владельца, тексты портрета/профиля, запросы из личного `config.yaml`, имена, контакты, токены. Итог — список находок «коммит · файл · что» в чат владельцу.
- [ ] **Step 2: Решение владельца.** Есть находки — предложить: переписать историю или опубликовать новый репозиторий с чистой историей. Ничего не делать без ответа.
- [ ] **Step 3: Только по «да» владельца:** `gh repo rename job-hunter`; `git remote set-url origin git@github.com:postpunk93/job-hunter.git`; `git push`.
- [ ] **Step 4: Только по отдельному «да»:** `gh repo edit --visibility public --accept-visibility-change-consequences`.
- [ ] **Step 5: Локальная папка** (по согласию владельца, вне сессии Claude, которая в ней открыта): `mv ~/projects/personal/vacancy-hunter ~/projects/personal/job-hunter && cd ~/projects/personal/job-hunter && uv sync --extra dev && uv tool install --editable . --reinstall`. Expected: `vacancies todo` и `hunter --help` работают из новой папки.
