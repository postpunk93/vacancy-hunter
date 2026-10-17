# Оценка вакансий Claude — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** каждая новая вакансия прохода `hunter` получает от Sonnet оценку 1–10 с причиной и флагами конфликтности. В xlsx идут все вакансии, в Телеграм — только с оценкой не ниже порога.

**Architecture:** новый шаг между `fresh` и рендером в `run_pass`. Модуль `hunter/fulltext.py` скачивает полные тексты (с кешем в `state.db`). Модуль `hunter/scoring.py` собирает промпт из `portrait.md` и `conflict.md`, вызывает `claude -p` пачками и кеширует оценки по хешу критериев. `run_pass` получает готовую функцию оценки аргументом и решает, кому идти в дайджест. Digest и export только показывают оценку.

**Tech Stack:** Python 3.12, uv, pytest, httpx, openpyxl, sqlite3, `subprocess` → Claude Code CLI 2.1.x.

**Spec:** `docs/superpowers/specs/2026-10-17-vacancy-scoring-design.md`

## Global Constraints

- Новых зависимостей нет.
- `run_pass` получает всё внешнее аргументами; тесты без сети, без Claude и без настоящих пауз.
- Вызов Claude: `claude -p --model <model> --tools "" --strict-mcp-config --setting-sources "" --no-session-persistence --system-prompt <...> --output-format json --json-schema <...>`, вход — stdin, результат — `structured_output`. Без `--bare`.
- Одна повторная попытка на пачку Claude.
- Порог по умолчанию 5; оценка — целое 1–10, выше — лучше.
- В Телеграм идёт: оценка ≥ порога, **или** оценки нет, **или** оценка по карточке (`from_card`).
- `mark_seen` после успешной отправки — для **всех** вакансий `fresh`: отправленным `sent=True`, отсеянным `sent=False`.
- При `Blocked`/`Captcha` на странице вакансии — прекратить скачивание, не повторять; код выхода прохода не меняется.
- Никаких новых кодов выхода. Сбой оценки виден ⚠️ в дайджесте и в логе.
- `--dry-run` оценивает и пополняет кеши `texts`/`scores`, но не трогает `seen` и не пишет xlsx.
- Кеши `texts` и `scores` чистятся вместе с `seen` по сроку 90 дней.
- Без секции `scoring` или с `enabled: false` бот работает как сейчас: дайджест без оценок, байт-в-байт прежний.
- Тексты для пользователя — по-русски, в стиле существующих сообщений.

## Review Focus

1. Claude вернул не все id, лишние id или мусор → пропавшие вакансии идут как неоценённые, не теряются. (Task 4)
2. `claude` не установлен (`FileNotFoundError`) или завис (таймаут) → все вакансии неоценённые, проход доходит до отправки с ⚠️. (Task 4, Task 7)
3. hh заблокировал на середине скачивания текстов → остаток оценивается по карточке и идёт в Телеграм независимо от оценки, ⚠️ в дайджесте, код 0. (Task 3, Task 7)
4. Правка `portrait.md`/`conflict.md` → старые оценки не используются, вакансии переоцениваются по кешированному тексту без запросов к hh. (Task 4)
5. Телеграм не принял дайджест после оценки → ничего не помечено виденным; повторный запуск не качает тексты и не тратит токены заново. (Task 7)

---

## Карта файлов

| Файл | Что меняется |
|---|---|
| `hunter/models.py` | + `FullText`, `Score` |
| `hunter/config.py` | + `ScoringConfig`, секция `scoring` |
| `hunter/store.py` | + таблицы `texts`, `scores`, методы кеша, чистка |
| `hunter/fulltext.py` | новый: скачивание полных текстов |
| `hunter/scoring.py` | новый: критерии, промпт, вызов Claude, разбор ответа, пачки, `score_pass` |
| `hunter/filters.py` | + `sort_by_score` |
| `hunter/export.py` | + колонки «Оценка», «Почему», «Флаги» |
| `hunter/digest.py` | + оценка в строке, счётчик отсеянных в шапке |
| `hunter/run.py` | фильтр по порогу, `--no-score`, сборка функции оценки |
| `config.example.yaml`, `README.md` | секция `scoring`, раздел про оценку |
| `~/projects/personal/job-search/conflict.md` | новый файл критериев (вне репозитория) |

---

### Task 1: Модели и конфиг

**Files:**
- Modify: `hunter/models.py`, `hunter/config.py`, `config.example.yaml`
- Test: `tests/test_models.py`, `tests/test_config.py`

**Interfaces:**
- Produces:
  - `models.FullText(description: str, key_skills: tuple[str, ...])` — frozen, slots.
  - `models.Score(value: int | None, why: str, flags: tuple[str, ...] = (), from_card: bool = False)` — frozen, slots. `value is None` — не оценена, в `why` причина. Свойство `scored -> bool` (`value is not None`).
  - `config.ScoringConfig(portrait: Path, conflict: Path, threshold: int = 5, model: str = "sonnet")` — frozen.
  - `config.Config.scoring: ScoringConfig | None = None` (новое поле с умолчанием; существующие `Config(sources=..., queries=...)` в тестах не ломаются).

- [ ] **Step 1: Тесты конфига**

В `tests/test_config.py` (через существующий способ писать yaml во временный файл):
- `test_scoring_section_is_optional` — без секции `config.scoring is None`.
- `test_scoring_disabled_means_none` — `scoring: {enabled: false, ...}` → `None`, даже если файлов нет.
- `test_scoring_paths_expand_home_and_must_exist` — `portrait: ~/x.md` разворачивается через `expanduser`; несуществующий файл → `ConfigError`, в тексте путь.
- `test_scoring_defaults` — только `portrait`/`conflict` → `threshold == 5`, `model == "sonnet"`.
- `test_scoring_threshold_must_be_1_to_10` — `threshold: 0`, `11`, `"5"`, `true` → `ConfigError`.
- `test_scoring_unknown_keys_rejected` — `scoring: {treshold: 5, ...}` → `ConfigError` со списком допустимых полей (`enabled`, `portrait`, `conflict`, `threshold`, `model`).

В `tests/test_models.py`: `test_score_without_value_is_not_scored` — `Score(None, "x").scored is False`, `Score(7, "x").scored is True`.

- [ ] **Step 2: Прогнать — падают**

Run: `uv run pytest tests/test_config.py tests/test_models.py -v`
Expected: FAIL (`ScoringConfig`/`Score` не определены).

- [ ] **Step 3: Реализовать**

`load_config` разбирает `raw.get("scoring")` отдельной функцией `_load_scoring(raw: object) -> ScoringConfig | None`. `enabled` по умолчанию `true`. В `config.example.yaml` добавить закомментированную секцию из спеки («Конфиг») с пояснением, что без неё бот работает без оценки.

- [ ] **Step 4: Прогнать — проходят, весь набор зелёный**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hunter/models.py hunter/config.py config.example.yaml tests/test_models.py tests/test_config.py
git commit -m "feat(scoring): config section and score model"
```

---

### Task 2: Кеши в state.db

**Files:**
- Modify: `hunter/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `FullText`, `Score` (Task 1).
- Produces (методы `Store`):
  - `get_text(key: str) -> FullText | None`, `save_text(key: str, text: FullText) -> None`
  - `get_score(key: str, criteria_hash: str) -> Score | None`, `save_score(key: str, criteria_hash: str, score: Score) -> None`
  - `purge_older_than(days)` теперь чистит и `texts`/`scores`; возвращает общее число удалённых строк.

Схема (в `_SCHEMA`, `CREATE TABLE IF NOT EXISTS` — старые базы открываются без миграций):

```sql
CREATE TABLE IF NOT EXISTS texts (
  key TEXT PRIMARY KEY, description TEXT NOT NULL,
  key_skills TEXT NOT NULL,            -- JSON-список
  fetched_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scores (
  key TEXT NOT NULL, criteria_hash TEXT NOT NULL,
  score INTEGER NOT NULL, why TEXT NOT NULL,
  flags TEXT NOT NULL,                 -- JSON-список
  scored_at TEXT NOT NULL,
  PRIMARY KEY (key, criteria_hash));
```

- [ ] **Step 1: Тесты**

- `test_text_roundtrip` — `save_text` → `get_text` возвращает равный `FullText`; неизвестный ключ → `None`; повторный `save_text` перезаписывает.
- `test_score_is_keyed_by_criteria_hash` — сохранено под `"h1"`; `get_score(key, "h2") is None`; `get_score(key, "h1") == Score(8, "почему", ("ГПХ",))`.
- `test_save_score_refuses_unscored_and_card_scores` — `save_score` с `Score(None, ...)` или `from_card=True` → `ValueError` (такие кешировать нельзя по спеке).
- `test_purge_cleans_caches_too` — подменить `fetched_at`/`scored_at` на дату 100 дней назад прямым UPDATE, `purge_older_than(90)` их удаляет, свежие остаются.
- `test_old_database_gets_new_tables` — база, созданная только со старыми таблицами `seen`/`meta`, открывается `Store`, и `get_text` работает.

- [ ] **Step 2: Прогнать — падают**

Run: `uv run pytest tests/test_store.py -v`
Expected: FAIL.

- [ ] **Step 3: Реализовать в `hunter/store.py`**

Списки — `json.dumps(..., ensure_ascii=False)`. Время — `_now().isoformat()`.

- [ ] **Step 4: Прогнать**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hunter/store.py tests/test_store.py
git commit -m "feat(scoring): cache full texts and scores in state.db"
```

---

### Task 3: Скачивание полных текстов

**Files:**
- Create: `hunter/fulltext.py`
- Test: `tests/test_fulltext.py`

**Interfaces:**
- Consumes: `Store.get_text/save_text` (Task 2); `corpus.extract.parse_detail`, `corpus.extract.Captcha`, `corpus.extract.VACANCY_URL`; `fetch.Blocked`, `fetch.NotFound`, `fetch.FetchError`; `parse.ParseError`.
- Produces:
  - `TEXT_PAUSE_RANGE = (3.0, 6.0)`
  - `fetch_texts(vacancies: list[Vacancy], *, fetch_page: Callable[[str], str], store: Store, pause: Callable[[float], None], jitter: Callable[[float, float], float] = random.uniform) -> tuple[dict[str, FullText], list[str]]` — словарь `vacancy.key → FullText` только для тех, чей текст есть; список предупреждений.

Поведение:
- Кеш первым: если `store.get_text(key)` есть — без запроса.
- Пауза `pause(jitter(*TEXT_PAUSE_RANGE))` только **между** настоящими запросами (перед первым — нет).
- URL — `VACANCY_URL.format(id=vacancy.vacancy_id)` (zarplata тоже, id общие).
- `Blocked`/`Captcha` → стоп, предупреждение `"hh заблокировал скачивание текстов вакансий: <exc>; не скачано N"`.
- `NotFound`, прочие `FetchError`, `ParseError`, а также `KeyError/TypeError/ValueError` из разбора → эта вакансия без текста, лог `warning`, идём дальше. Отдельного предупреждения в дайджест на единичные неудачи нет (видно в «Почему»).
- Удачный текст → `store.save_text`. Описание — `detail.description` (уже markdown), навыки — `tuple(detail.key_skills)`.

- [ ] **Step 1: Тесты** (страницы — `tests.factories.vacancy_page`, вакансии — `make_vacancy(vacancy_id=...)`, `Store(tmp_path / "s.db")`)

- `test_texts_are_fetched_and_cached` — две вакансии → два запроса на `https://hh.ru/vacancy/<id>`, оба текста в результате и в `store.get_text`.
- `test_cached_text_is_not_refetched` — текст уже в кеше → `fetch_page` не вызывается для него.
- `test_pause_only_between_real_requests` — три вакансии, одна из кеша → `pause` вызван ровно 1 раз со значением из `jitter` (подставить `jitter=lambda a, b: 4.2`, проверить `4.2`).
- `test_block_stops_and_warns` — вторая страница бросает `Blocked` → третья не запрашивается; в результате только первая; одно предупреждение, в нём «не скачано 2».
- `test_captcha_page_counts_as_block` — страница без `HH-Lux-InitialState` → как блокировка.
- `test_single_failures_are_skipped` — `NotFound` на одной и `FetchError` на другой → остальные скачаны, предупреждений нет.

- [ ] **Step 2: Прогнать — падают**

Run: `uv run pytest tests/test_fulltext.py -v`
Expected: FAIL (модуля нет).

- [ ] **Step 3: Реализовать `hunter/fulltext.py`**

- [ ] **Step 4: Прогнать**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hunter/fulltext.py tests/test_fulltext.py
git commit -m "feat(scoring): fetch full vacancy texts with pauses and cache"
```

---

### Task 4: Оценка через Claude

**Files:**
- Create: `hunter/scoring.py`
- Test: `tests/test_scoring.py`

**Interfaces:**
- Consumes: `FullText`, `Score`, `ScoringConfig` (Task 1); `Store.get_score/save_score` (Task 2); `fulltext.fetch_texts` (Task 3); `digest.format_salary` для зарплаты во входе.
- Produces:
  - `PROMPT_VERSION = 1`, `BATCH_SIZE = 10`, `DESCRIPTION_LIMIT = 6000`, `CLAUDE_TIMEOUT = 600`
  - `class ClaudeError(Exception)`
  - `Criteria(portrait: str, conflict: str)` — frozen; `load_criteria(cfg: ScoringConfig) -> Criteria` (читает utf-8; `OSError` → `ConfigError`).
  - `criteria_hash(criteria: Criteria) -> str` — sha256 hex от `PROMPT_VERSION`, `portrait`, `conflict` (с разделителем).
  - `build_system_prompt(criteria: Criteria) -> str`
  - `SCHEMA: dict` — `{"items": [{"id": str, "score": int, "why": str, "flags": [str]}]}`, все поля обязательны.
  - `vacancy_payload(vacancy: Vacancy, text: FullText | None) -> dict` — ключи `id` (=`vacancy.key`), `name`, `company`, `salary`, `work_formats`, `experience`, `description` (обрезан до `DESCRIPTION_LIMIT` с `…`; при `None` — пустая строка), `key_skills`.
  - `parse_scores(data: object, keys: list[str]) -> dict[str, Score]`
  - `ask_claude(system: str, payload: str, *, model: str, timeout: float = CLAUDE_TIMEOUT, run=subprocess.run) -> object` — возвращает `structured_output` (или `json.loads(result)`), любые сбои → `ClaudeError`.
  - `score_vacancies(vacancies: list[Vacancy], texts: dict[str, FullText], *, ask: Callable[[str, str], object], criteria: Criteria, store: Store) -> tuple[dict[str, Score], list[str]]` — `ask(system, payload)`; результат содержит `Score` для **каждой** вакансии.
  - `score_pass(vacancies: list[Vacancy], *, fetch_page, store: Store, pause, ask, criteria: Criteria) -> tuple[dict[str, Score], list[str]]` — `fetch_texts` + `score_vacancies`, предупреждения обоих.

Правила:
- **Промпт** — смысл из спеки («Оценка через Claude», «Шкала и сведение»): роль (подбор вакансий для конкретного человека плюс взгляд конфликтолога), портрет целиком под заголовком, `conflict.md` целиком под заголовком, шкала (да → 7–10, может быть → 5–6, нет/вне рамок → 1–4), конфликтность только понижает; мягкие признаки — только во `flags`; три и больше тяжёлых — ниже 5; один-два тяжёлых — минус балл-два, не ниже 5 сами по себе; нехватка информации — не повод снижать; пустое описание — оценивать по остальным полям; `why` — одна короткая фраза по-русски; `flags` — короткие названия признаков из `conflict.md`; вернуть оценку для каждого id. Формулировку при изменении — поднять `PROMPT_VERSION`.
- **parse_scores**: как `Scorer::parseScores` в media-scan — только id из `keys`, первый побеждает при дублях, `score` числовой (bool — нет) → `round` и обрезка в 1–10, пустой/нестроковый `why` → строка пропущена, `why` обрезается до 300 символов, `flags` — только непустые строки, не больше 6, каждая до 60 символов.
- **ask_claude**: команда из Global Constraints, `cwd` — временный каталог (`tempfile.gettempdir()`), `input=payload`, `capture_output=True`, `text=True`, `encoding="utf-8"`. `FileNotFoundError` → `ClaudeError("claude не найден — установлен ли Claude Code?")`; `TimeoutExpired` → `ClaudeError(f"claude не ответил за {timeout} с")`; stdout не JSON, `returncode != 0` или `is_error` → `ClaudeError` с первыми 300 символами stderr/result; нет `structured_output` и `result` не разбирается в JSON → `ClaudeError`.
- **score_vacancies**:
  - хеш критериев один раз; вакансии с кешем `store.get_score(key, hash)` не отправляются;
  - остальные — пачками по `BATCH_SIZE`, `json.dumps([...], ensure_ascii=False)`; на пачку до 2 попыток `ask`;
  - разобранная оценка вакансии без текста → `from_card=True` и **не** кешируется; с текстом → `store.save_score`;
  - вакансия без валидной строки → `Score(None, "Claude не оценил: <последняя ошибка или 'нет в ответе'>")`;
  - если не оценена хотя бы одна → предупреждение `"не оценено N вакансий: <последняя ошибка>"` (без ошибки — `"…: нет в ответе Claude"`).

- [ ] **Step 1: Тесты**

Фейки: `ask` — функция, возвращающая заранее заданный `{"items": [...]}` и записывающая вызовы; `run` — функция, возвращающая `subprocess.CompletedProcess(args, returncode, stdout, stderr)` или бросающая.

- `test_parse_scores_keeps_only_batch_ids_and_clamps` — лишний id отброшен, дубль — берётся первый, `score: 14` → 10, `score: 0.4` → 1, `score: true` → строка отброшена, пустой `why` → отброшена.
- `test_payload_truncates_description_and_marks_missing_text` — описание 7000 символов → длина `DESCRIPTION_LIMIT + 1`, оканчивается `…`; `text=None` → `description == ""`, `key_skills == []`; `id == vacancy.key`.
- `test_prompt_contains_both_criteria_files` — оба текста целиком входят в `build_system_prompt`.
- `test_criteria_hash_changes_when_a_file_changes` — разные `conflict` → разные хеши; одинаковые → равны.
- `test_ask_claude_builds_the_documented_command` — фейковый `run` записывает `args`: первый `claude`, затем `-p`; есть пары `--model sonnet`, `--tools ""`, `--setting-sources ""`, `--output-format json`; есть `--strict-mcp-config`, `--no-session-persistence`, `--json-schema`; нет `--bare`; `input` — переданный payload.
- `test_ask_claude_returns_structured_output` — stdout `{"is_error": false, "structured_output": {"items": []}}` → `{"items": []}`.
- `test_ask_claude_failures_raise_claude_error` (parametrize) — `FileNotFoundError`, `TimeoutExpired`, stdout не JSON, `returncode=1`, `is_error: true` → `ClaudeError`.
- `test_vacancies_are_sent_in_batches` — 23 вакансии → 3 вызова `ask` (10, 10, 3).
- `test_failed_batch_is_retried_once_then_left_unscored` — `ask` всегда бросает `ClaudeError("boom")` → 2 вызова на пачку; все `Score.value is None`, `why` содержит `boom`; одно предупреждение `"не оценено 3 вакансий: boom"` (формулировку числа сделать через `text.plural`; в тесте проверить вхождение `"не оценено"` и `"boom"`).
- `test_missing_id_in_answer_is_unscored_not_lost` — ответ без одной вакансии → для неё `Score(None, ...)`, остальные оценены; ключи результата == ключи всех входных вакансий.
- `test_card_scores_are_marked_and_not_cached` — вакансия без текста → `from_card=True`, в `store.get_score` её нет; с текстом — есть.
- `test_cached_scores_skip_claude` — вторая `score_vacancies` с теми же критериями → `ask` не вызывается.
- `test_changed_criteria_rescore_from_cached_text_without_hh` — `score_pass` дважды: второй раз с другим `conflict`; `fetch_page` во втором прогоне не вызывается, `ask` вызывается.

- [ ] **Step 2: Прогнать — падают**

Run: `uv run pytest tests/test_scoring.py -v`
Expected: FAIL (модуля нет).

- [ ] **Step 3: Реализовать `hunter/scoring.py`**

- [ ] **Step 4: Прогнать**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Ручная проверка флагов CLI** (Claude Code на хосте 2.1.301)

Run: `echo '[{"id":"hh:1","name":"Тест","company":"X","salary":"от 100 000 ₽","work_formats":["REMOTE"],"experience":null,"description":"Пишем статьи для Хабра","key_skills":[]}]' | uv run python -c "import sys; from hunter.scoring import ask_claude, SCHEMA; print(ask_claude('Оцени вакансию от 1 до 10 по шкале, верни items.', sys.stdin.read(), model='sonnet'))"`
Expected: словарь вида `{'items': [{'id': 'hh:1', 'score': ..., 'why': ..., 'flags': [...]}]}`. Если какой-то флаг CLI отвергнут — поправить команду и тест, отметить в сообщении коммита.

- [ ] **Step 6: Commit**

```bash
git add hunter/scoring.py tests/test_scoring.py
git commit -m "feat(scoring): score vacancies with Claude in batches"
```

---

### Task 5: Оценки в xlsx и сортировка

**Files:**
- Modify: `hunter/export.py`, `hunter/filters.py`
- Test: `tests/test_export.py`, `tests/test_filters.py`

**Interfaces:**
- Consumes: `Score` (Task 1).
- Produces:
  - `filters.sort_by_score(vacancies: list[Vacancy], scores: Mapping[str, Score]) -> list[Vacancy]` — по убыванию `value`, неоценённые (и отсутствующие в `scores`) в конце; устойчиво.
  - `export.write_xlsx(groups: list[Group], path: Path, scores: Mapping[str, Score] | None = None) -> None` — порядок строк не меняет (сортирует вызывающий).

Колонки: `("Ссылка", "Название", "Оценка", "Почему", "Флаги", "Запрос", "Статус", "Комментарий")`, ширины `(40, 60, 9, 60, 35, 25, 16, 50)`. Выпадающий список статусов — `G2:G1000`; автофильтр до `H`.
- «Оценка» — `Score.value` числом (пусто, если `None` или `scores` нет).
- «Почему» — `why`; при `from_card` — `"по карточке, текст не скачался: " + why`.
- «Флаги» — `", ".join(flags)`.

- [ ] **Step 1: Тесты**

- поправить существующие `test_writes_header_and_one_row_per_vacancy`, `test_status_column_offers_a_dropdown_of_funnel_stages`, `test_header_is_frozen_and_filterable` под новые колонки (`G2:G1000`, `A1:H…`);
- `test_score_columns_are_filled` — `Score(8, "подходит", ("ГПХ", "часовой пояс"))` → `C2 == 8`, `D2 == "подходит"`, `E2 == "ГПХ, часовой пояс"`;
- `test_unscored_and_card_rows` — `Score(None, "Claude не оценил: boom")` → `C` пусто, `D` с причиной; `Score(6, "мало данных", from_card=True)` → `D` начинается с `"по карточке, текст не скачался: "`;
- `test_without_scores_columns_are_empty` — `scores=None` → `C`, `D`, `E` пусты;
- в `tests/test_filters.py`: `test_sort_by_score_descending_unscored_last` — оценки 5, None, 9, отсутствует, 7 → порядок 9, 7, 5, None, отсутствует (стабильность для двух последних).

- [ ] **Step 2: Прогнать — падают**

Run: `uv run pytest tests/test_export.py tests/test_filters.py -v`
Expected: FAIL.

- [ ] **Step 3: Реализовать**

- [ ] **Step 4: Прогнать**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hunter/export.py hunter/filters.py tests/test_export.py tests/test_filters.py
git commit -m "feat(scoring): score, reason and flags columns in xlsx"
```

---

### Task 6: Оценка в дайджесте

**Files:**
- Modify: `hunter/digest.py`
- Test: `tests/test_digest.py`

**Interfaces:**
- Consumes: `Score` (Task 1).
- Produces: `render(groups: list[Group], now: datetime, warnings: tuple[str, ...] = (), *, scores: Mapping[str, Score] | None = None, below: int = 0, threshold: int = 5) -> list[str]`. Порядок вакансий не меняет.

Точный текст:
- строка вакансии при `scores is not None`: `• <оценка> · <a href=…>Название</a>`; оценка — число, `?` если нет оценки, `<число>*` если `from_card`. Вторая строка как сейчас. При `scores is None` — строка прежняя, без префикса.
- шапка при `below > 0` и `total > 0`: `🔎 {N новых вакансий} · {день мес}, {ЧЧ:ММ} · ещё {below} ниже {threshold} — в xlsx`.
- при `total == 0` и `below > 0`: одна шапка `🔎 {день мес}, {ЧЧ:ММ} · все {below} новых ниже {threshold} — в xlsx` (+ предупреждения, если есть). «новых» без склонения по числу — допустимо, но лучше через `plural` (`"новая"`, `"новые"`, `"новых"`): на выбор исполнителя, тест проверяет вхождение `f"{below}"` и `"ниже 5 — в xlsx"`.
- при `total == 0`, `below == 0`, без предупреждений — `[]`, как сейчас.

- [ ] **Step 1: Тесты**

- `test_without_scores_lines_are_unchanged` — существующий `test_digest_has_a_header_groups_and_links` остаётся зелёным без правок (это и есть проверка).
- `test_score_prefixes_each_line` — оценки 8, None, 6 (from_card) → в тексте `"• 8 · <a "`, `"• ? · <a "`, `"• 6* · <a "`.
- `test_header_counts_vacancies_below_threshold` — `below=4` → шапка содержит `"ещё 4 ниже 5 — в xlsx"`.
- `test_only_header_when_everything_is_below` — `groups=[]`, `below=3` → одно сообщение, в нём `"3"` и `"ниже 5 — в xlsx"`.

- [ ] **Step 2: Прогнать — падают**

Run: `uv run pytest tests/test_digest.py -v`
Expected: FAIL.

- [ ] **Step 3: Реализовать**

- [ ] **Step 4: Прогнать**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hunter/digest.py tests/test_digest.py
git commit -m "feat(scoring): show scores and filtered count in the digest"
```

---

### Task 7: Порог в проходе

**Files:**
- Modify: `hunter/run.py` (`run_pass`)
- Test: `tests/test_run.py`

**Interfaces:**
- Consumes: `Score`; `filters.sort_by_score`; `render(..., scores=, below=, threshold=)`; `write_xlsx(..., scores=)`.
- Produces: `run_pass(..., score: Callable[[list[Vacancy]], tuple[dict[str, Score], list[str]]] | None = None, threshold: int = 5)` — новые keyword-аргументы, остальное как было.

Поведение `run_pass` после вычисления `fresh` (до рендера):
1. `score is None` → всё как сейчас (`render` без `scores`, `write_xlsx` без `scores`).
2. Иначе `scores, extra = score([v for _, vs in fresh for v in vs])`; `warnings += extra`; внутри групп `sort_by_score`.
3. Проходит в дайджест: `s is None or not s.scored or s.from_card or s.value >= threshold`. Остальное — отсеянные; `below = len(отсеянных)`; пустые группы из дайджеста убрать.
4. При `dry_run` — после отправки (печати) напечатать `print()` блок `"Ниже порога {threshold}:"` и строки `"  {value} · {name} — {why}"`; при нуле отсеянных — ничего.
5. После успешной отправки: `mark_seen(отправленные, sent=True)`, `mark_seen(отсеянные, sent=False)`.
6. xlsx — все `fresh` (отсортированные) с `scores`.
7. Если `fresh` пуст — функция оценки **не** вызывается.

- [ ] **Step 1: Тесты** (фейковая `score` возвращает заданные оценки по ключу и записывает вызовы; используются существующие `Sender`, `page_returning`, `no_pause`, `Store(tmp_path/...)`)

- `test_below_threshold_goes_to_xlsx_not_telegram` — оценки 8 и 3 → в Телеграме одна вакансия и «ещё 1 ниже 5»; в xlsx обе строки; обе в `seen`, у отсеянной `sent_at IS NULL`.
- `test_unscored_and_card_scores_bypass_the_threshold` — `Score(None, …)` и `Score(2, …, from_card=True)` → обе в Телеграме.
- `test_all_below_threshold_sends_header_and_writes_xlsx` — все с оценкой 2 → одно сообщение с «ниже 5 — в xlsx», xlsx создан, все в `seen`.
- `test_telegram_failure_after_scoring_marks_nothing` — `Sender(fail=True)` → `EXIT_TELEGRAM_FAILED`, `store.unseen` возвращает все вакансии; второй проход с той же `Store` и фейком `score`, собранным через настоящий `scoring.score_pass` с фейками `fetch_page`/`ask`, не запрашивает тексты повторно (счётчик вызовов `fetch_page` для `https://hh.ru/vacancy/` не растёт) и не вызывает `ask`.
- `test_scoring_warnings_reach_the_digest` — `score` возвращает предупреждение `"не оценено 2 вакансий: boom"` → оно в тексте с ⚠️, код `EXIT_OK`.
- `test_block_during_text_fetch_keeps_exit_ok` — `score_pass` с `fetch_page`, бросающим `Blocked` на страницах вакансий (выдача — из фикстуры) → код `EXIT_OK`, вакансии в дайджесте (по карточке), ⚠️ про блокировку.
- `test_dry_run_prints_filtered_and_keeps_seen_untouched` — `dry_run=True`, оценки 8 и 3 → `capsys` содержит `"Ниже порога 5:"` и название отсеянной; `store.unseen` возвращает обе; xlsx нет.
- `test_no_fresh_vacancies_means_no_scoring_call` — всё уже видено → `score` не вызывалась.

- [ ] **Step 2: Прогнать — падают**

Run: `uv run pytest tests/test_run.py -v`
Expected: FAIL (нет аргумента `score`).

- [ ] **Step 3: Реализовать в `run_pass`**

- [ ] **Step 4: Прогнать**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hunter/run.py tests/test_run.py
git commit -m "feat(scoring): threshold filter for telegram in the pass"
```

---

### Task 8: Командная строка, критерии, документация

**Files:**
- Modify: `hunter/run.py` (`main`), `README.md`, `docs/backlog.md` (только если что-то отложено)
- Create: `~/projects/personal/job-search/conflict.md` (вне репозитория, не коммитится здесь)
- Modify (локально, в git не идёт): `config.yaml`
- Test: `tests/test_run.py`

**Interfaces:**
- Consumes: `ScoringConfig`, `load_criteria`, `score_pass`, `ask_claude` (Tasks 1, 4).

`main`:
- флаг `--no-score` (`help`: «не скачивать тексты и не оценивать вакансии в этом запуске»);
- если `config.scoring` есть и нет `--no-score`: `criteria = load_criteria(config.scoring)` внутри того же `try`, что и `load_config` (ошибка → `EXIT_BAD_CONFIG` до сбора); функция оценки — замыкание над `score_pass` с `fetch_page` (тот же клиент httpx), `store`, `pause=time.sleep`, `ask=lambda system, payload: ask_claude(system, payload, model=config.scoring.model)`; `threshold=config.scoring.threshold`.

- [ ] **Step 1: Тесты**

- `test_missing_criteria_file_is_a_config_error` — конфиг с `scoring.conflict` на несуществующий файл → `main([...]) == EXIT_BAD_CONFIG` (по образцу существующих тестов `main` с `clean_root_logger`).
- `test_no_score_flag_skips_scoring` — `monkeypatch` на `run_module.run_pass`, проверить, что при `--no-score` аргумент `score is None`, а без флага — вызываемый объект.

- [ ] **Step 2: Прогнать — падают**

Run: `uv run pytest tests/test_run.py -v`
Expected: FAIL.

- [ ] **Step 3: Реализовать `main`**

- [ ] **Step 4: Прогнать**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Создать `~/projects/personal/job-search/conflict.md`**

Содержимое — по спеке («Файлы критериев» и приложение A): заголовок; короткое вступление «список для оценки вакансий ботом, правится свободно»; раздел **«Тяжёлые признаки»** (шесть пунктов из спеки с описаниями владельца; «деньги не определены» — с уточнением, что одна широкая вилка сюда не относится); раздел **«Мягкие признаки — только флаг, на оценку не влияют»** (пять пунктов, плюс широкая вилка / «от» без верха); раздел **«Что снижает конфликтность»** (абзац владельца); раздел **«Как сводить»**: три и больше тяжёлых — ниже 5; один-два — минус балл-два, не ниже 5 сами по себе; мягкие — только во флаги. Описания признаков переносить словами владельца из приложения A, не переписывать. Файл не перезаписывать, если он уже существует, — сообщить.

- [ ] **Step 6: Включить в локальном `config.yaml`**

Добавить секцию `scoring` из спеки с реальными путями. `config.yaml` в `.gitignore`.

- [ ] **Step 7: Живая проверка**

Run: `hunter --dry-run --days 1 --db /tmp/claude-scoring-try.db` (если `hunter` не переустановлен — `uv run hunter …`)
Expected: в консоли дайджест со строками `• N · …`, блок «Ниже порога 5:», код 0; в логе нет исключений. Посмотреть 3–5 оценок глазами: похожи ли на правду по `portrait.md`. Повторный запуск с той же базой не качает тексты (в логе нет запросов к `hh.ru/vacancy/`) и не вызывает Claude.

Если hh заблокировал (⚠️) — не повторять, сообщить владельцу.

- [ ] **Step 8: README**

- в шпаргалку: `hunter --no-score` — без оценки;
- новый раздел «Оценка вакансий»: что делает (Sonnet через Claude Code по подписке, `claude` должен быть установлен и залогинен), где критерии (`portrait.md`, `conflict.md` в job-search — правка текста меняет поведение, вакансии переоцениваются по кешу без запросов к hh), порог в `config.yaml`, что значат `?` и `*` в дайджесте, колонки xlsx, что проход стал дольше (пауза 3–6 с на вакансию);
- в «Воронка»: новые колонки;
- про `--dry-run`: теперь оценивает и запоминает скачанные тексты, но не трогает, что уже присылали; проверять правки критериев — `hunter --dry-run` на обычной базе (кеш текстов сэкономит запросы к hh).

- [ ] **Step 9: Commit**

```bash
git add hunter/run.py tests/test_run.py README.md docs/backlog.md
git commit -m "feat(scoring): --no-score flag, criteria loading, docs"
```
