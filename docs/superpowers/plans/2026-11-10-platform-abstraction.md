# Площадка как адаптер: hh + superjob — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ввести шов «площадка» и подключить superjob.ru вторым источником целиком — выдача, карточка, страница вакансии — не сломав существующие базы, команды и конфиги.

**Architecture:** Новый пакет `hunter/platforms/` с протоколом `Platform`; hh-код переезжает в `platforms/hh.py` без изменения логики, superjob пишется с нуля. В базе `vacancies` получает `source` + `external_id` с уникальным индексом, а `id` остаётся числовым суррогатным ключом, поэтому `hits`/`verdicts`/`applications`/`status_history` и все их запросы не меняются.

**Tech Stack:** Python 3.14, stdlib (`json`, `sqlite3`, `urllib.parse`, `re`), httpx уже есть в `hunter/fetch.py`, pytest.

**Spec:** `docs/superpowers/specs/2026-11-10-platform-abstraction-design.md`

## Global Constraints

- Новых зависимостей нет: только стандартная библиотека и уже подключённый httpx.
- Миграции в `hunter/corpus/store.py` только добавляются в конец `_MIGRATIONS`; старые шаги не редактируются, потому что у владельца есть базы, созданные ими.
- Каждая запись в корпус коммитится сразу (`self._conn.commit()` в конце метода).
- Ошибка разбора — громкая (`ParseError`), никогда не пустой список.
- Тексты для владельца — по-русски; идентификаторы в коде — по-английски.
- Тесты: pytest, фикстуры в `tests/fixtures/`. Прогон: `python -m pytest -q`.
- Сообщения коммитов в стиле репозитория: `feat:`, `fix:`, `docs:`, `chore:` + описание по-русски.
- `me/` целиком в `.gitignore`: правки `me/config.yaml` делаются, но не коммитятся.

## Review Focus

- Существующая база v3 после обновления кода: все вакансии на месте, `latest_scores` отдаёт прежние оценки, `vacancies init` не пересоздаёт таблицы.
- `vacancies fetch <ссылка superjob>` на вакансию, которой нет в базе: добавляет её, а не падает с «нет в базе».
- Повторный сбор той же выдачи superjob: строки не задваиваются, `hits` не плодятся.
- Страница выдачи superjob, ушедшая на SEO-лендинг: в базу не попадают «похожие» вакансии, сбор сообщает об этом, а не молчит.
- Дайджест и таблица: вакансия superjob без даты публикации (`published_at IS NULL`) не ломает сортировку, фильтр по периоду и печать.

---

### Task 1: Карточка площадки и необязательная дата публикации

**Files:**
- Create: `hunter/platforms/__init__.py`
- Create: `hunter/platforms/base.py`
- Modify: `hunter/models.py` (`Vacancy.published_at`)
- Modify: `hunter/filters.py` (`drop_published_before`, `sort_by_freshness`)
- Modify: `hunter/run.py:241` (сортировка «рядом»)
- Test: `tests/test_platforms_base.py`, `tests/test_filters.py`

**Interfaces:**
- Consumes: `hunter.models.Vacancy`, `hunter.models.SearchQuery`.
- Produces: `Card`, `Detail`, `Platform`, `UnknownPlatform`, `Captcha` из `hunter.platforms.base`; `Vacancy.published_at: datetime | None`.

- [ ] **Step 1: Написать падающий тест на `Card`**

```python
# tests/test_platforms_base.py
from datetime import datetime, timezone

from hunter.platforms.base import Card


def _card(**over):
    base = dict(
        source="hh", external_id="42", name="Редактор", company="АСТ",
        url="https://hh.ru/vacancy/42", area="Москва",
        published_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        salary_from=100000, salary_to=None, currency="RUR", gross=False,
        salary_mode="MONTH", work_formats=("REMOTE",), experience="between1And3",
        is_ad=False, row={"name": "Редактор"},
    )
    base.update(over)
    return Card(**base)


def test_card_key_has_source_prefix():
    assert _card().key == "hh:42"


def test_card_builds_digest_vacancy():
    vacancy = _card().vacancy
    assert (vacancy.source, vacancy.vacancy_id, vacancy.name) == ("hh", "42", "Редактор")
    assert vacancy.work_formats == ("REMOTE",)


def test_card_vacancy_allows_unknown_date():
    assert _card(published_at=None).vacancy.published_at is None
```

- [ ] **Step 2: Прогнать — падает**

Run: `python -m pytest tests/test_platforms_base.py -q`
Expected: FAIL — `ModuleNotFoundError: hunter.platforms`.

- [ ] **Step 3: Написать `hunter/platforms/base.py`**

`Card` — frozen slots-датакласс с полями из теста и `row: dict[str, object]`; свойства `key` (`f"{source}:{external_id}"`) и `vacancy` (`Vacancy(...)`). `Detail` — frozen slots-датакласс `source: str`, `external_id: str`, `row: dict`, `description: str`, `key_skills: list[str]`, `status: str`, `raw: dict`. `Captcha(Exception)` переезжает сюда из `corpus/extract.py` — одна на все площадки: «Ответ 200 без состояния страницы — капча или заглушка». `UnknownPlatform(Exception)` с сообщением «Неизвестная площадка: X. Доступны: …». `Platform` — `typing.Protocol` с `name`, `page_size`, `max_pages`, `search_url(query, page=0)`, `search_period(days)`, `parse_cards(page_html)`, `vacancy_url(external_id)`, `parse_detail(page_html)`, `parse_ref(ref)`, `unsupported(query) -> tuple[str, ...]`.

`hunter/platforms/__init__.py` пока пустой (только docstring).

- [ ] **Step 4: Сделать дату необязательной и научить фильтры**

В `hunter/models.py` — `published_at: datetime | None` (комментарий: у superjob даты в карточке нет). В `hunter/filters.py`: `drop_published_before` оставляет вакансии с `None` (сервер уже отфильтровал по периоду), `sort_by_freshness` кладёт `None` в конец. В `hunter/run.py:241` — `-v.published_at.timestamp()` заменить на `-(v.published_at or _EPOCH).timestamp()`, где `_EPOCH = datetime.fromtimestamp(0, tz=timezone.utc)` рядом с константами модуля.

Тесты в `tests/test_filters.py`:

```python
def test_drop_published_before_keeps_unknown_date():
    vacancy = _vacancy(published_at=None)
    assert drop_published_before([vacancy], cutoff) == [vacancy]


def test_sort_by_freshness_puts_unknown_date_last():
    assert sort_by_freshness([unknown, fresh]) == [fresh, unknown]
```

- [ ] **Step 5: Прогнать тесты и весь набор**

Run: `python -m pytest -q`
Expected: PASS.

- [ ] **Step 6: Коммит**

```bash
git add hunter/platforms tests/test_platforms_base.py hunter/models.py hunter/filters.py hunter/run.py tests/test_filters.py
git commit -m "feat: карточка площадки и необязательная дата публикации"
```

---

### Task 2: Миграция базы v4 — `source` + `external_id`

**Files:**
- Modify: `hunter/corpus/store.py` (`_MIGRATIONS`, `upsert_card`, `note_seen`, `save_detail`, новые `find`/`resolve`)
- Modify: `hunter/corpus/collect.py:92` (распаковка результата upsert)
- Modify: `hunter/run.py:317` (`note_seen` больше не bool)
- Test: `tests/test_corpus_store.py`

**Interfaces:**
- Consumes: `Detail` из `hunter.platforms.base` (Task 1).
- Produces:
  - `Corpus.upsert_card(row: dict) -> tuple[bool, int]` — `(новая?, id)`;
  - `Corpus.note_seen(row: dict) -> tuple[bool, int]`;
  - `Corpus.find(source: str, external_id: str) -> int | None`;
  - `Corpus.resolve(source: str, external_id: str) -> int` — `CorpusError`, если нет;
  - `Corpus.save_detail(detail: Detail) -> None` — по паре `detail.source`/`detail.external_id`.

- [ ] **Step 1: Падающий тест миграции v3 → v4**

```python
# tests/test_corpus_store.py
import sqlite3

from hunter.corpus import store as store_module
from hunter.corpus.store import Corpus


def test_v3_base_migrates_without_losing_rows(tmp_path):
    path = tmp_path / "db.sqlite"
    conn = sqlite3.connect(path)
    for sql in store_module._MIGRATIONS[:3]:
        conn.executescript(sql)
    conn.execute("PRAGMA user_version = 3")
    conn.execute(
        "INSERT INTO vacancies (id, name, company, url, first_seen_at, last_seen_at) "
        "VALUES (114811554, 'Редактор', 'АСТ', 'https://hh.ru/vacancy/114811554', "
        "'2026-11-01', '2026-11-01')"
    )
    conn.execute(
        "INSERT INTO scores (key, criteria_hash, score, why, flags, scored_at) "
        "VALUES ('hh:114811554', 'h', 7, 'ok', '[]', '2026-11-02')"
    )
    conn.commit()
    conn.close()

    with Corpus.open(path) as corpus:
        row = corpus.vacancy(114811554)
        assert (row["source"], row["external_id"]) == ("hh", "114811554")
        assert corpus.find("hh", "114811554") == 114811554
        assert corpus.query("SELECT vacancy_id, score FROM latest_scores")[1] == [(114811554, 7)]
```

- [ ] **Step 2: Прогнать — падает**

Run: `python -m pytest tests/test_corpus_store.py::test_v3_base_migrates_without_losing_rows -q`
Expected: FAIL — `IndexError: no such column: source`.

- [ ] **Step 3: Тест на пару ключей**

```python
def test_same_number_from_two_sources_are_two_rows(tmp_path):
    with Corpus.open(tmp_path / "db.sqlite", create=True) as corpus:
        hh = _row(source="hh", external_id="1")
        sj = _row(source="superjob", external_id="1")
        first = corpus.upsert_card(hh)
        second = corpus.upsert_card(sj)
        assert first[0] is True and second[0] is True
        assert first[1] != second[1]
        assert corpus.find("hh", "1") == first[1]
        assert corpus.find("superjob", "1") == second[1]
        assert corpus.upsert_card(sj) == (False, second[1])
```

`_row(...)` — существующий в файле конструктор строки карточки; ему добавить `source`/`external_id` вместо `id`.

- [ ] **Step 4: Добавить миграцию v4**

В конец `_MIGRATIONS`:

```sql
ALTER TABLE vacancies ADD COLUMN source TEXT NOT NULL DEFAULT 'hh';
ALTER TABLE vacancies ADD COLUMN external_id TEXT;
UPDATE vacancies SET external_id = CAST(id AS TEXT);
CREATE UNIQUE INDEX vacancies_source_external ON vacancies(source, external_id);
DROP VIEW latest_scores;
CREATE VIEW latest_scores AS
SELECT vacancy_id, score, why, flags, scored_at FROM (
  SELECT v.id AS vacancy_id, s.score, s.why, s.flags, s.scored_at,
         ROW_NUMBER() OVER (PARTITION BY v.id ORDER BY s.scored_at DESC) AS n
  FROM scores s JOIN vacancies v ON s.key = v.source || ':' || v.external_id
) WHERE n = 1;
```

- [ ] **Step 5: Переписать запись и чтение по паре**

- Приватный `_row_key(row: dict) -> tuple[str, str]`: `(row.get("source") or "hh", str(row.get("external_id") or row["id"]))` — переходная совместимость, пока не все вызывающие перешли на `external_id`.
- `upsert_card`/`note_seen`: `INSERT … ON CONFLICT(source, external_id) DO UPDATE … RETURNING id`; новизна определяется `find(...) is None` до вставки; возвращают `(is_new, id)`.
- `find`/`resolve` — простой `SELECT id FROM vacancies WHERE source = ? AND external_id = ?`.
- `save_detail`: `vacancy_id = self.find(detail.source, detail.external_id)`; если `None` — `upsert_card(detail.row)`, затем `UPDATE … WHERE id = ?`.
- `exists(vacancy_id: int)`, `has_detail`, `mark_deleted`, `set_verdict`, `set_status` не трогать: они работают с суррогатным id.
- `from ..platforms.base import Detail` вместо `.extract`.

- [ ] **Step 6: Поправить вызывающих**

`hunter/corpus/collect.py:92` — `is_new, vacancy_id = corpus.upsert_card(row)`, дальше `add_hit(vacancy_id, …)` и `counted.add(vacancy_id)` вместо `row["id"]`. `hunter/run.py:317` — `store.corpus.note_seen(row)` без использования результата.

- [ ] **Step 7: Прогнать весь набор**

Run: `python -m pytest -q`
Expected: PASS (в том числе `tests/test_corpus_ui.py` — там форма постит `id`, он остался суррогатным).

- [ ] **Step 8: Коммит**

```bash
git add hunter/corpus/store.py hunter/corpus/collect.py hunter/run.py tests/test_corpus_store.py
git commit -m "feat: база v4 — площадка и внешний id вакансии"
```

---

### Task 3: hh как площадка

**Files:**
- Create: `hunter/platforms/hh.py`
- Modify: `hunter/platforms/__init__.py` (`PLATFORMS`, `get`)
- Delete: `hunter/sources.py`, `hunter/corpus/extract.py`
- Modify: `hunter/parse.py` (убрать `parse_search_page`, `_to_vacancy`, `_EPOCH`)
- Modify: `hunter/corpus/collect.py`, `hunter/fulltext.py`, `hunter/run.py`
- Tests: `tests/test_platforms_hh.py` (из `tests/test_sources.py` и `tests/test_corpus_extract.py`), `tests/test_parse.py`

**Interfaces:**
- Consumes: `Card`, `Detail`, `Platform` (Task 1); `Corpus.upsert_card` (Task 2).
- Produces:
  - `Hh(site: str)` с `name`, `page_size = 50`, `max_pages = 40`;
  - `hunter.platforms.PLATFORMS: dict[str, Platform]` = `{"hh": Hh("hh"), "zarplata": Hh("zarplata")}`;
  - `hunter.platforms.get(name: str) -> Platform` (бросает `UnknownPlatform`);
  - `collect(..., source: str = "hh")`.

- [ ] **Step 1: Тест паритета URL**

```python
# tests/test_platforms_hh.py
from hunter.models import SearchQuery
from hunter.platforms import get


def test_hh_search_url_keeps_hh_parameters():
    query = SearchQuery(name="author", text="редактор", area=113, search_period=3,
                        work_format="remote", only_with_salary=True, max_pages=2)
    url = get("hh").search_url(query, page=0)
    assert url.startswith("https://hh.ru/search/vacancy?")
    assert "text=%D1%80%D0%B5%D0%B4%D0%B0%D0%BA%D1%82%D0%BE%D1%80" in url
    assert "area=113" in url and "search_period=3" in url
    assert "work_format=REMOTE" in url and "only_with_salary=true" in url
    assert "page=" not in url
    assert "page=2" in get("hh").search_url(query, page=2)


def test_zarplata_is_its_own_platform_with_hh_engine():
    assert get("zarplata").name == "zarplata"
    assert get("zarplata").search_url(SearchQuery(name="a", text="x")).startswith(
        "https://www.zarplata.ru/search/vacancy?"
    )
```

- [ ] **Step 2: Тест карточек и детали на существующих фикстурах**

```python
def test_hh_parse_cards_matches_fixture():
    cards = get("hh").parse_cards((FIXTURES / "hh_search_python_msk.html").read_text("utf-8"))
    first = cards[0]
    assert first.source == "hh" and first.external_id == "114811554"
    assert first.name and first.company
    assert first.row["external_id"] == "114811554"
    assert first.row["published_at"].startswith("2026-08-16")


def test_hh_parse_detail_returns_full_text():
    detail = get("hh").parse_detail((FIXTURES / "hh_vacancy_page.html").read_text("utf-8"))
    assert detail.source == "hh" and detail.external_id.isdigit()
    assert detail.description and detail.row["name"]
```

- [ ] **Step 3: Прогнать — падает**

Run: `python -m pytest tests/test_platforms_hh.py -q`
Expected: FAIL — `ImportError: cannot import name 'get'`.

- [ ] **Step 4: Перенести код hh в `hunter/platforms/hh.py`**

Переезжает без изменения логики: `SOURCES`/`HH_PERIODS`/`hh_period`/`build_url` из `hunter/sources.py` и `_page_state`/`search_cards`/`card_row`/`parse_detail`/`VACANCY_URL` из `hunter/corpus/extract.py` (`Captcha` теперь в `platforms/base.py`, `Detail` — тоже).

`Hh.__init__(self, site: str)` берёт домен и путь из словаря `SITES` (`hh` → `https://hh.ru/search/vacancy`, `zarplata` → `https://www.zarplata.ru/search/vacancy`); неизвестный сайт — `UnknownPlatform`. `name` — свойство, возвращает `site`. `search_period` — прежний `hh_period`. `search_url(query, page=0)` — прежний `build_url` с `items_on_page=50`. `parse_cards` строит `Card`: `external_id = str(raw["vacancyId"])`, `published_at` из `(raw.get("publicationTime") or {}).get("$")` через `datetime.fromisoformat` (нет — `None`), `row` — прежний `card_row` с `source`/`external_id` вместо `id`. `vacancy_url(external_id)` — `https://hh.ru/vacancy/{external_id}` (один адрес для hh и zarplata: id общие). `parse_detail` — прежний, с `source=self.name`. `parse_ref(ref)` — `ref` целиком цифрами → он же; ссылка на hh/zarplata `/vacancy/(\d+)` → id; иначе `None`.

`hunter/platforms/__init__.py`: `PLATFORMS` и `get` (сообщение об ошибке перечисляет `PLATFORMS`).

- [ ] **Step 5: Перевести потребителей**

- `hunter/corpus/collect.py`: сигнатура `collect(corpus, fetch_page, query, role, wait=_no_wait, *, source="hh")`; `platform = get(source)`; `platform.search_url(query, page)`, `platform.parse_cards(...)`, `platform.page_size` вместо `ITEMS_PER_PAGE`; `row = card.row`.
- `hunter/fulltext.py`: `get(vacancy.source).vacancy_url(vacancy.vacancy_id)` вместо `VACANCY_URL.format(...)`.
- `hunter/run.py`: `platform = get(source)`; `found = platform.parse_cards(page_html)`; `collected.extend(card.vacancy for card in found)`; `len(found) < platform.page_size`; импорты `build_url`/`hh_period`/`parse_search_page` убрать.
- `hunter/parse.py`: остаются `ParseError` и `extract_state`; `parse_search_page`, `_to_vacancy`, `_EPOCH` удалить.
- `hunter/corpus/store.py` уже импортирует `Detail` из `platforms.base` (Task 2).

- [ ] **Step 6: Переписать тесты парсеров**

`tests/test_sources.py` и `tests/test_corpus_extract.py` переезжают в `tests/test_platforms_hh.py` (URL, периоды, карточки, деталь, `Captcha` на странице без `HH-Lux-InitialState`, `ParseError` на битом состоянии). `tests/test_parse.py` тестирует `Hh("hh").parse_cards` на той же фикстуре вместо `parse_search_page`; `extract_state` — как раньше.

- [ ] **Step 7: Прогнать весь набор**

Run: `python -m pytest -q`
Expected: PASS; `grep -rn "hunter.sources\|corpus.extract" hunter tests` не находит ничего.

- [ ] **Step 8: Коммит**

```bash
git add -A hunter tests
git commit -m "feat: hh и zarplata как площадки за общим протоколом"
```

---

### Task 4: Ссылка на вакансию, `fetch` добавляет, вывод печатает `ref`

**Files:**
- Modify: `hunter/platforms/__init__.py` (`parse_ref`, `format_ref`)
- Modify: `hunter/corpus/cli.py`, `hunter/corpus/collect.py` (`fetch_details`), `hunter/corpus/show.py`, `hunter/corpus/funnel.py`, `hunter/export.py`
- Test: `tests/test_platforms_hh.py`, `tests/test_corpus_cli.py`, `tests/test_corpus_show.py`, `tests/test_corpus_funnel.py`, `tests/test_export.py`

**Interfaces:**
- Consumes: `PLATFORMS`, `get` (Task 3).
- Produces:
  - `parse_ref(value: str) -> tuple[str, str]`;
  - `format_ref(source: str, external_id: str) -> str` — для `hh` это `external_id`, иначе `f"{source}:{external_id}"`;
  - `fetch_details(corpus, fetch_page, refs: list[tuple[str, str]], *, role=None, refresh=False, wait=_no_wait) -> FetchReport`.

- [ ] **Step 1: Тест грамматики ссылок**

```python
def test_parse_ref_forms():
    assert parse_ref("114811554") == ("hh", "114811554")
    assert parse_ref("superjob:52295791") == ("superjob", "52295791")
    assert parse_ref("https://hh.ru/vacancy/114811554?from=serp") == ("hh", "114811554")
    assert parse_ref("https://www.zarplata.ru/vacancy/114811554") == ("hh", "114811554")
    assert parse_ref("https://russia.superjob.ru/vakansii/redaktor-52295791.html") == (
        "superjob", "52295791")


def test_parse_ref_rejects_foreign_links():
    with pytest.raises(ValueError):
        parse_ref("https://example.com/vacancy/1")


def test_format_ref_is_bare_for_hh():
    assert format_ref("hh", "114811554") == "114811554"
    assert format_ref("superjob", "52295791") == "superjob:52295791"
```

- [ ] **Step 2: Прогнать — падает**

Run: `python -m pytest tests/test_platforms_hh.py -q`
Expected: FAIL — `ImportError: cannot import name 'parse_ref'`.

- [ ] **Step 3: Реализовать `parse_ref`/`format_ref`**

`parse_ref`: `source:id` при известной площадке и цифровом id; голое число → `("hh", value)`; иначе перебор `PLATFORMS.values()` и первый `platform.parse_ref(value)`; `hh`-движок (hh, zarplata) всегда отдаёт пару с `"hh"`, потому что id у них общие и страница вакансии живёт на hh. Ничего не подошло — `ValueError("Не похоже на вакансию: …")`.

- [ ] **Step 4: Тест `fetch` добавляет незнакомую вакансию**

```python
def test_fetch_adds_vacancy_missing_from_base(tmp_path, fake_fetch):
    with Corpus.open(tmp_path / "db.sqlite", create=True) as corpus:
        report = fetch_details(corpus, fake_fetch, [("hh", "114811554")])
        assert report.fetched == ["114811554"]
        assert corpus.find("hh", "114811554") is not None
```

- [ ] **Step 5: Перевести `fetch_details` и CLI**

`fetch_details` принимает пары; внутри `vacancy_id = corpus.find(source, external_id)`; пропуск, если `vacancy_id` и `has_detail`; `url = get(source).vacancy_url(external_id)`; `detail = get(source).parse_detail(html)`; `save_detail` заводит строку, если её не было; `NotFound` — `mark_deleted` только когда `vacancy_id` известен. `FetchReport` хранит `ref`-строки (`format_ref`), печать — по ним; «удалены на hh» → «удалены».

`cli.py`: `parse_vacancy_ref`/`_parse_refs` удалить, использовать `parse_ref`; `mark`/`status`/`show` — `corpus.resolve(source, external_id)`; `fetch` — пары без резолва.

- [ ] **Step 6: Вывод печатает `ref`**

`show.py`: `id {format_ref(v["source"], v["external_id"])}` и подсказка `vacancies fetch {ref}`. `funnel.py`: в запросы добавить `v.source, v.external_id`, в хвосте напоминаний `format_ref`. `export.py`: `export_rows` добавляет `v.source` первой колонкой (перед `v.id`); `_cmd_export` подменяет значение колонки `id` на `format_ref` перед записью xlsx (UI продолжает работать с суррогатным `id`).

- [ ] **Step 7: Прогнать весь набор**

Run: `python -m pytest -q`
Expected: PASS.

- [ ] **Step 8: Коммит**

```bash
git add -A hunter tests
git commit -m "feat: ссылки вида superjob:id и добавление вакансии по ссылке"
```

---

### Task 5: superjob — выдача и карточки

**Files:**
- Create: `hunter/platforms/superjob.py`
- Create: `tests/fixtures/superjob_search_sales.html`, `tests/fixtures/superjob_search_editor.html`
- Modify: `hunter/platforms/__init__.py` (регистрация)
- Test: `tests/test_platforms_superjob.py`

**Interfaces:**
- Consumes: `Card`, `UnknownPlatform` (Task 1); `get` (Task 3).
- Produces: `Superjob()` с `name = "superjob"`, `page_size = 40`, `max_pages = 14`, `search_url`, `search_period`, `parse_cards`, `vacancy_url`, `parse_ref`; `PLATFORMS["superjob"]`.

**Как устроена выдача (проверено на живых страницах 2026-11-10):**

- `window.APP_STATE=` + JSON: искать маркер, затем `json.JSONDecoder().raw_decode(html[i:])`.
- `state["pageInfo"]["pageType"]` — `"search-vacancy"` у выдачи, `"landing-page"` у SEO-лендинга (он тоже содержит 40 карточек и `responses.lists.vacancy` — поэтому проверять надо именно `pageType`).
- `state["responses"]["lists"]["vacancy"]` — словарь из одной записи `url → {"meta": {...}, "result": [id, ...]}`; `meta.offset` — смещение.
- `state["entities"]["vacancy"][id]["relationships"]` — ссылки на сущности; читаются `mainInfo`, `companyInfo`, `town`, `detailInfo`, `requiredExperience`, `searchSnippet`.
- `entities["vacancyMainInfo"][id].attributes.profession` — название; `.relationships.salary.data` — ссылка на `vacancySalary` (бывает `None`).
- `entities["vacancySalary"][id].attributes` — `minSalary`, `maxSalary` (0 = нет границы), `paymentAgreement`; `relationships.currency.data.id` — `rub`, `salaryType` → словарь `vacancySalaryTypeDictionary` (`key` = `gross`/`net`), `salaryPeriod` → `vacancyPaymentPeriodDictionary` (`key` = `month`).
- `entities["vacancyCompanyInfo"][id].attributes.name` — компания.
- `entities["town"][id].attributes.name` — город.
- `entities["vacancyDetailInfo"][id].attributes.isRemoteWork` — удалёнка.
- `entities["vacancyExperienceDictionary"][id].attributes.value` — опыт: `0` → `None`, `1` → `noExperience`, `2` → `between1And3`, `3` → `between3And6`, `4` → `moreThan6`.
- Пагинация 1-based: `page=1` совпадает с отсутствием параметра, `page=2` даёт `offset=40`.

- [ ] **Step 1: Скачать фикстуры**

```bash
UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36'
curl -sS -A "$UA" 'https://russia.superjob.ru/vacancy/search/?keywords=%D0%BC%D0%B5%D0%BD%D0%B5%D0%B4%D0%B6%D0%B5%D1%80+%D0%BF%D1%80%D0%BE%D0%B4%D0%B0%D0%B6&period=7' -o tests/fixtures/superjob_search_sales.html
curl -sS -A "$UA" 'https://russia.superjob.ru/vacancy/search/?keywords=%D1%80%D0%B5%D0%B4%D0%B0%D0%BA%D1%82%D0%BE%D1%80+%D1%83%D0%B4%D0%B0%D0%BB%D0%B5%D0%BD%D0%BD%D0%BE&period=7' -o tests/fixtures/superjob_search_editor.html
```

- [ ] **Step 2: Падающие тесты**

```python
# tests/test_platforms_superjob.py
from hunter.models import SearchQuery
from hunter.parse import ParseError
from hunter.platforms import get
from hunter.platforms.base import Captcha


def _query(**over):
    base = dict(name="author", text="редактор удаленно", search_period=7,
                work_format="remote", max_pages=2)
    base.update(over)
    return SearchQuery(**base)


def test_search_url_uses_keywords_period_and_remote_tag():
    url = get("superjob").search_url(_query(), page=0)
    assert url.startswith("https://russia.superjob.ru/vacancy/search/?")
    assert "period=7" in url and "workFormatTag%5B0%5D=81" in url
    assert "page=" not in url
    assert "page=2" in get("superjob").search_url(_query(), page=1)


def test_period_is_capped_to_week():
    assert get("superjob").search_period(30) == 7
    assert get("superjob").search_period(3) == 3


def test_cards_carry_salary_and_company():
    cards = get("superjob").parse_cards(
        (FIXTURES / "superjob_search_sales.html").read_text("utf-8"))
    assert len(cards) >= 40
    paid = next(c for c in cards if c.salary_from)
    assert paid.source == "superjob" and paid.external_id.isdigit()
    assert paid.company and paid.url.endswith(f"x-{paid.external_id}.html")


def test_cards_without_salary_are_payment_agreement():
    cards = get("superjob").parse_cards(
        (FIXTURES / "superjob_search_editor.html").read_text("utf-8"))
    assert all(c.salary_from is None and c.salary_to is None for c in cards[:5])


def test_landing_page_is_a_loud_parse_error():
    with pytest.raises(ParseError):
        get("superjob").parse_cards((FIXTURES / "superjob_landing.html").read_text("utf-8"))


def test_page_without_state_is_captcha():
    with pytest.raises(Captcha):
        get("superjob").parse_cards("<html><body>captcha</body></html>")
```

Для `test_landing_page_is_a_loud_parse_error` добавить третью фикстуру: `curl -sSL -A "$UA" 'https://russia.superjob.ru/vacancy/search/?keywords=%D1%80%D0%B5%D0%B4%D0%B0%D0%BA%D1%82%D0%BE%D1%80' -o tests/fixtures/superjob_landing.html` (редирект на `/vakansii/redaktor.html`).

- [ ] **Step 3: Прогнать — падает**

Run: `python -m pytest tests/test_platforms_superjob.py -q`
Expected: FAIL — `ModuleNotFoundError: hunter.platforms.superjob`.

- [ ] **Step 4: Реализовать `parse_cards`**

Порядок проверок: нет маркера `window.APP_STATE=` и нет `f-test-vacancy-item` в HTML → `Captcha("в ответе superjob нет APP_STATE — похоже на капчу")`; `pageType != "search-vacancy"` → `ParseError("выдача ушла на SEO-лендинг (<pageType>) — уточните запрос")`; нет `responses.lists.vacancy` → `ParseError`. Карточка: `id` из `result`, поля из сущностей выше; `published_at = None`; `is_ad = False`; `url = self.vacancy_url(id)`; `work_formats = ("REMOTE",) if isRemoteWork else ()`; `currency` — `{"rub": "RUR", "usd": "USD", "eur": "EUR"}.get(key, key.upper())`; `salary_mode` — `key.upper()` из `salaryPeriod` (`month` → `MONTH`); `row` — те же колонки, что у hh (`company_id` = `None`, `employment_form` = ключ `workType`, `card_json` — `json.dumps` прочитанных сущностей). Ошибки разбора полей — `ParseError` с именем сущности.

- [ ] **Step 5: `search_url`, `search_period`, `vacancy_url`, `parse_ref`**

`search_url`: `https://russia.superjob.ru/vacancy/search/?` + `urlencode([("keywords", text), ("period", search_period), ("workFormatTag[0]", "81") при work_format == "remote", ("page", page + 1) если page])`. `search_period`: ближайшее из `(1, 3, 7)` не короче запрошенного, иначе 7. `vacancy_url`: `https://russia.superjob.ru/vakansii/x-{id}.html` (301 на канонический слаг — проверено). `parse_ref`: `re.search(r"/vakansii/(?:[^/]*-)?(\d+)\.html", ref)` → id.

- [ ] **Step 6: Зарегистрировать площадку и прогнать**

`PLATFORMS["superjob"] = Superjob()` в `hunter/platforms/__init__.py`. Run: `python -m pytest -q` — PASS.

- [ ] **Step 7: Коммит**

```bash
git add hunter/platforms tests/test_platforms_superjob.py tests/fixtures/superjob_*.html
git commit -m "feat: superjob — выдача и карточки"
```

---

### Task 6: superjob — страница вакансии и полный текст

**Files:**
- Modify: `hunter/platforms/superjob.py` (`parse_detail`)
- Create: `tests/fixtures/superjob_vacancy.html`
- Test: `tests/test_platforms_superjob.py`, `tests/test_corpus_collect.py`

**Interfaces:**
- Consumes: `Detail` (Task 1), `get` (Task 3), `fetch_details` (Task 4).
- Produces: `Superjob.parse_detail(page_html) -> Detail`.

**Что в странице (проверено):** JSON-LD `JobPosting` — `title`, `url`, `datePosted`, `employmentType` (`FULL_TIME`), `description` (HTML полного текста), `baseSalary` (`currency`, `value.minValue`/`maxValue`), `hiringOrganization.name`, `jobLocation.address.addressLocality`. Ключевых навыков нет. `identifier.value` — id компании, а не вакансии: id берётся из `url` (или из `x-<id>` ссылки), а не из `identifier`.

- [ ] **Step 1: Фикстура**

```bash
UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36'
curl -sSL -A "$UA" 'https://russia.superjob.ru/vakansii/x-52295791.html' -o tests/fixtures/superjob_vacancy.html
```

- [ ] **Step 2: Падающий тест**

```python
def test_detail_comes_from_jsonld():
    html = (FIXTURES / "superjob_vacancy.html").read_text("utf-8")
    detail = get("superjob").parse_detail(html)
    assert detail.source == "superjob" and detail.external_id == "52295791"
    assert detail.row["name"].startswith("Ведущий менеджер")
    assert detail.row["company"]
    assert detail.row["published_at"].startswith("2026-10-30")
    assert detail.row["salary_from"] == 75000
    assert detail.description and "<p>" not in detail.description
    assert detail.status == "active"
```

- [ ] **Step 3: Прогнать — падает**

Run: `python -m pytest tests/test_platforms_superjob.py::test_detail_comes_from_jsonld -q`
Expected: FAIL — `AttributeError`/`NotImplementedError` из `parse_detail`.

- [ ] **Step 4: Реализовать `parse_detail`**

Найти все `<script type="application/ld+json">`, взять блок с `"@type": "JobPosting"`. Нет блока и нет `window.APP_STATE` → `Captcha`; нет блока при живом `APP_STATE` → `ParseError("на странице superjob нет JobPosting — сайт сменил формат")`. `external_id` — из `url` через `parse_ref`. `description` — `html_to_markdown` из `hunter.corpus.markdown`. `key_skills = []`. `row` — `name`, `company`, `area`, `published_at = datePosted`, `salary_from`/`salary_to` из `baseSalary.value` (нет — `None`), `currency` по словарю, `gross = None`, `employment_form` — `FULL_TIME → FULL`, `PART_TIME → PART`, иначе исходное значение, остальные поля `None`, `card_json = None`. `raw` — сам `JobPosting`.

- [ ] **Step 5: Сквозной тест через `fetch_details`**

```python
def test_superjob_link_adds_vacancy_end_to_end(tmp_path):
    html = (FIXTURES / "superjob_vacancy.html").read_text("utf-8")
    with Corpus.open(tmp_path / "db.sqlite", create=True) as corpus:
        report = fetch_details(
            corpus,
            lambda url: html if "52295791" in url else (_ for _ in ()).throw(AssertionError(url)),
            [("superjob", "52295791")],
        )
        assert report.fetched == ["superjob:52295791"]
        vacancy_id = corpus.find("superjob", "52295791")
        assert corpus.vacancy(vacancy_id)["description"]
```

- [ ] **Step 6: Прогнать и закоммитить**

Run: `python -m pytest -q` — PASS.

```bash
git add hunter/platforms/superjob.py tests/test_platforms_superjob.py tests/test_corpus_collect.py tests/fixtures/superjob_vacancy.html
git commit -m "feat: superjob — страница вакансии и полный текст"
```

---

### Task 7: Конфиг — запрос под площадку, неподдерживаемые фильтры

**Files:**
- Modify: `hunter/models.py` (`SearchQuery.text_by_source`, `text_for`)
- Modify: `hunter/config.py` (`_QUERY_FIELDS`, валидация `sources` по `PLATFORMS`, разбор `text_by_source`)
- Modify: `hunter/run.py` (`_collect`: текст под площадку, пропуск, предупреждения)
- Modify: `hunter/corpus/collect.py` (`CollectReport.skipped`, предупреждения)
- Modify: `hunter/corpus/cli.py` (`--source`)
- Modify: `me/config.yaml` (не коммитится)
- Test: `tests/test_config.py`, `tests/test_run.py`, `tests/test_corpus_collect.py`

**Interfaces:**
- Consumes: `PLATFORMS`, `get` (Task 3), `Superjob` (Task 5).
- Produces:
  - `SearchQuery.text_by_source: Mapping[str, str | None]` (по умолчанию пустой), `SearchQuery.text_for(source: str) -> str | None`;
  - `Platform.unsupported(query: SearchQuery) -> tuple[str, ...]` (hh — `()`, superjob — имена проигнорированных полей);
  - `collect(..., source="hh")` печатает «пропущено» и предупреждения.

- [ ] **Step 1: Тесты правила «чей текст»**

```python
def test_text_falls_back_to_common_text():
    query = SearchQuery(name="a", text="редактор")
    assert query.text_for("superjob") == "редактор"


def test_missing_platform_text_skips_platform():
    query = SearchQuery(name="a", text="NAME:(редактор)", text_by_source={"superjob": "редактор"})
    assert query.text_for("hh") is None
    assert query.text_for("superjob") == "редактор"
```

- [ ] **Step 2: Тесты конфига и `unsupported`**

```python
def test_config_accepts_superjob_source_and_texts(tmp_path):
    config = load_config(_write_config(tmp_path, sources=["hh", "superjob"], text_by_source={"superjob": "редактор удаленно"}))
    assert config.sources == ("hh", "superjob")
    assert config.queries[0].text_for("superjob") == "редактор удаленно"


def test_config_rejects_unknown_source(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_write_config(tmp_path, sources=["avito"]))


def test_superjob_reports_unsupported_filters():
    query = SearchQuery(name="a", text="x", area=1, salary=100000, only_with_salary=True)
    assert set(get("superjob").unsupported(query)) == {"area", "salary", "only_with_salary"}
    assert get("hh").unsupported(query) == ()
```

- [ ] **Step 3: Прогнать — падает**

Run: `python -m pytest tests/test_config.py tests/test_models.py -q`
Expected: FAIL — неизвестное поле `text_by_source`.

- [ ] **Step 4: Реализовать**

`SearchQuery`: поле `text_by_source: Mapping[str, str | None] = field(default_factory=dict)`; `text_for` по правилу «нет карты → общий `text`; площадка в карте → её значение (`null` → `None`); карта есть, площадки нет → `None`». `config.py`: `text_by_source` в `_QUERY_FIELDS`, проверка «словарь со строковыми или пустыми значениями», `KNOWN_SOURCES` заменить на `tuple(PLATFORMS)`. `Superjob.unsupported`: `area` (когда `!= 113`), `salary`, `experience`, `only_with_salary` (когда `True`), `work_format` (когда не `None` и не `remote`); `Hh.unsupported` — `()`.

- [ ] **Step 5: Провести через сбор**

`run.py._collect`: `text = query.text_for(source)`; `None` → `warnings.append(f"«{query.name}» не отправлен на {source}: нет запроса для площадки")` и `continue`; иначе `replace(query, text=text)` для `search_url`. Плюс `warnings.extend(f"{source}: не поддерживает {', '.join(fields)} — проигнорировано" for ...)` один раз на площадку.

`collect`: `CollectReport` получает `skipped: str | None` и `notes: list[str]`; в начале — если `query.text_for(source) is None`, вернуть отчёт с `skipped`; предупреждения `unsupported` добавить в `notes`, `summary()` печатает их строками. `cli.py`: `--source` (choices из `PLATFORMS`, default `hh`) передаётся в `collect`.

- [ ] **Step 6: Запросы superjob в личном конфиге**

В `me/config.yaml`: `sources: [hh, superjob]`; у запросов ролей `author` и `promo` добавить

```yaml
    text_by_source:
      superjob: "редактор удаленно"
```

(для `promo` — «продвижение сайта удаленно»). У остальных ролей — `text_by_source: {superjob: null}`: значение `null` значит «на эту площадку запрос не отправлять», так hh-синтаксис не улетает на superjob. Файл в `.gitignore`, в коммит не попадает.

- [ ] **Step 7: Прогнать и закоммитить**

Run: `python -m pytest -q` — PASS.

```bash
git add hunter/models.py hunter/config.py hunter/run.py hunter/corpus/collect.py hunter/corpus/cli.py tests
git commit -m "feat: запрос под площадку и предупреждения о неподдерживаемых фильтрах"
```

---

### Task 8: Пагинация, отчёт по площадке, стоп на странице без новых

**Files:**
- Modify: `hunter/corpus/collect.py`, `hunter/run.py`
- Test: `tests/test_corpus_collect.py`, `tests/test_run.py`

**Interfaces:**
- Consumes: `Platform.max_pages`, `Platform.page_size` (Tasks 3, 5).
- Produces: `CollectReport.summary()` с именем площадки; `run.py` останавливает листание на странице без новых `key`.

- [ ] **Step 1: Тесты**

```python
def test_collect_caps_pages_by_platform_limit(corpus, fake_fetch):
    query = SearchQuery(name="a", text="редактор", max_pages=40)
    report = collect(corpus, fake_fetch, query, "author", source="superjob")
    assert report.pages <= get("superjob").max_pages


def test_page_without_new_ids_stops_pagination(corpus, fake_fetch):
    # две страницы подряд отдают одни и те же 40 карточек
    report = collect(corpus, fake_fetch, query, "author", source="superjob")
    assert report.pages == 1
```

- [ ] **Step 2: Прогнать — падает**

Run: `python -m pytest tests/test_corpus_collect.py -q`
Expected: FAIL — листает 40 страниц.

- [ ] **Step 3: Реализовать**

`collect`: `pages = min(query.max_pages, platform.max_pages)`; на каждой странице считать `fresh = {card.external_id for card in cards} - seen`; если пусто — `break` после записи (страница уже обработана); `seen` накапливается по всем страницам. Если `platform.search_period(query.search_period) < query.search_period`, в `notes` добавляется строка `f"{source}: смотрели за {supported} дн. — больше площадка не отдаёт"`, где `supported = platform.search_period(query.search_period)`. Строка отчёта: `f"{role} ({source}): …"`.

`run.py._collect`: то же правило — `key` собирается в `seen_keys` на проход по запросу; `len(found) < platform.page_size` остаётся вторым условием.

- [ ] **Step 4: Прогнать и закоммитить**

Run: `python -m pytest -q` — PASS.

```bash
git add hunter/corpus/collect.py hunter/run.py tests
git commit -m "feat: пагинация по возможностям площадки и стоп без новых вакансий"
```

---

### Task 9: Дедуп с нормализацией компании и названия

**Files:**
- Modify: `hunter/filters.py` (`dedupe`, новый `_normalize`)
- Test: `tests/test_filters.py`

**Interfaces:**
- Consumes: ничего из предыдущих задач.
- Produces: `dedupe(groups) -> tuple[list[Group], list[Vacancy]]` с прежней сигнатурой; сравнение по нормализованной паре.

- [ ] **Step 1: Тест**

```python
def test_dedupe_collapses_legal_form_and_quotes():
    hh = _vacancy(company='ООО «Ромашка»', name="Редактор", source="hh")
    sj = _vacancy(company="Ромашка", name="Редактор", source="superjob")
    groups, absorbed = dedupe([("a", [hh]), ("b", [sj])])
    assert [len(g) for _, g in groups] == [1, 0]
    assert absorbed == [sj]


def test_dedupe_keeps_different_titles():
    assert [len(g) for _, g in dedupe([("a", [x]), ("b", [y])])[0]] == [1, 1]
```

- [ ] **Step 2: Прогнать — падает**

Run: `python -m pytest tests/test_filters.py -q`
Expected: FAIL — две группы по одной вакансии.

- [ ] **Step 3: Реализовать нормализацию**

`_normalize(text: str) -> str`: `casefold()`, замена `ё → е`, удаление кавычек `«»"'` и пунктуации, удаление отдельных слов из набора `{"ооо", "оао", "зао", "пао", "ао", "ип", "llc", "ltd", "inc"}`, схлопывание пробелов, `strip()`. Применяется к `company` и `name` при построении ключа второго прохода; в базе и в выводе остаются исходные строки.

- [ ] **Step 4: Прогнать и закоммитить**

Run: `python -m pytest -q` — PASS.

```bash
git add hunter/filters.py tests/test_filters.py
git commit -m "feat: дедуп схлопывает организационно-правовые формы и кавычки"
```

---

## Финальная проверка

- [ ] `python -m pytest -q` — зелёный.
- [ ] `vacancies collect --role author --text 'редактор' --source superjob --pages 2` на живой сети: отчёт печатает площадку, база пополняется, повторный запуск не даёт новых.
- [ ] `vacancies fetch 'https://russia.superjob.ru/vakansii/x-<id>.html'` добавляет вакансию, `vacancies show <id>` печатает текст.
- [ ] `vacancies init` на копии существующей базы v3: строки на месте, `vacancies status` показывает воронку.
- [ ] Субагент `reviewer` ревьюит ветку целиком.
