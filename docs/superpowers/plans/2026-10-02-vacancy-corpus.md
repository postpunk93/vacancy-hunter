# Корпус вакансий для анализа в сессии Claude Code — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** CLI `vacancies` (collect / fetch / mark / show / sql / init), который
собирает вакансии hh в SQLite-корпус, докачивает полный текст выборочно и
даёт сессии Claude Code в отдельной папке читать и размечать корпус.

**Architecture:** Новый пакет `hunter/corpus/`: `markdown.py` (HTML → Markdown),
`extract.py` (карточка выдачи и страница вакансии → строки базы), `store.py`
(SQLite с миграциями), `collect.py` (сбор и докачка с отчётами), `show.py`
(вывод), `cli.py` (argparse), `GUIDE.md` (инструкция для сессии, подключается
`@`-импортом). Сеть, разбор состояния страницы и построение URL берутся из
существующих `hunter.fetch`, `hunter.parse`, `hunter.sources`. Бот не меняется,
кроме подкласса `NotFound` в `hunter/fetch.py`.

**Tech Stack:** Python ≥ 3.12, stdlib (`sqlite3`, `html.parser`, `argparse`),
`httpx` (уже в зависимостях), pytest, uv (установка как tool), hatchling
(только сборка).

**Spec:** `docs/superpowers/specs/2026-10-02-vacancy-corpus-design.md`

## Global Constraints

- Новых runtime-зависимостей нет: только `httpx`, `pyyaml` и stdlib. `hatchling` — только `build-system`.
- Все сообщения пользователю и комментарии — по-русски, в стиле существующего кода (комментарий объясняет «почему»).
- Бот (`hunter/run.py`, `digest.py`, `filters.py`, `store.py`, `telegram.py`) и `state.db` не меняются. Единственная правка общего кода — `NotFound(FetchError)` в `hunter/fetch.py`.
- Ключ вакансии — целое `id` hh; `url` всегда `https://hh.ru/vacancy/<id>`.
- Коды выхода: 0 — успех; 1 — частичная неудача / ошибка разбора / ошибка SQL / нет вакансии в `show`; 2 — неверные аргументы или база недоступна (`CorpusError`); 3 — блокировка (403) или капча.
- Пауза между HTTP-запросами — `--delay` (по умолчанию 2 с) × случайный множитель 0.5–1.5; перед первым запросом паузы нет.
- `collect` по умолчанию: `--area 113`, `--days 30` (допустимо 1, 3, 7, 30), `--pages 5` (допустимо 1–40).
- `status`: `active` | `archived` (из `vacancy.status.archived` на странице) | `deleted` (HTTP 404).
- `antifraud_power` извлекается только из карточки выдачи; у вакансий, добавленных по ссылке, он NULL (страница вакансии этого свойства не содержит). Свойство есть, а силы нет — `0.0`.
- База по умолчанию — `./vacancies.db`; глобальный `--db PATH` ставится **до** подкоманды. Команды, кроме `init`, не создают базу, а падают с кодом 2.
- Тесты запускаются так: `.venv/bin/python -m pytest` (из корня репозитория).
- Каждый коммит заканчивается строкой `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. Сессия запущена в папке без `vacancies.db` — понятная ошибка с подсказкой `vacancies init`, код 2, лишний файл базы не появляется (тест в Task 7).
2. Ссылки в реальном виде: поддомены (`tomsk.hh.ru`), `m.hh.ru`, query-string, слэш в конце, zarplata — все дают id; чужие домены и не-вакансии отклоняются до любого запроса (тест в Task 7).
3. Рекламная карточка дублирует обычную на другой странице выдачи — в отчёте считается один раз (тест в Task 5).
4. Описания со вложенными списками, таблицами, `&nbsp;` и мусорными пробелами читаются как нормальный Markdown, без ведущих пробелов и тройных пустых строк (тесты в Task 2).
5. `vacancies sql`, вернувший ноль строк или запрос без результата, печатает понятную строку, а не падает (тесты в Task 6).

---

## File Structure

| Файл | Ответственность |
|---|---|
| `hunter/fetch.py` (изменить) | + `NotFound` для HTTP 404 |
| `hunter/corpus/__init__.py` | пустой маркер пакета |
| `hunter/corpus/markdown.py` | `html_to_markdown(html) -> str` |
| `hunter/corpus/extract.py` | `Captcha`, `search_cards`, `card_row`, `Detail`, `parse_detail`, `VACANCY_URL` |
| `hunter/corpus/store.py` | `Corpus` (SQLite, миграции, upsert, вердикты, read-only запросы), `CorpusError`, `VERDICTS`, `SCHEMA_VERSION` |
| `hunter/corpus/collect.py` | `collect`, `fetch_details`, `CollectReport`, `FetchReport`, `MANUAL_QUERY` |
| `hunter/corpus/show.py` | `render_vacancy`, `render_table` |
| `hunter/corpus/cli.py` | `main`, `build_parser`, `parse_vacancy_ref`, обработчики подкоманд, `init` |
| `hunter/corpus/GUIDE.md` | инструкция для сессии Claude в папке поиска работы |
| `pyproject.toml` (изменить) | `build-system`, `[project.scripts] vacancies` |
| `tests/factories.py` (изменить) | `state_page`, `search_card`, `search_page`, `detail_vacancy`, `vacancy_page` |
| `tests/fixtures/hh_vacancy_page.html` | урезанная реальная страница вакансии |
| `tests/test_corpus_*.py` | тесты по модулю на файл |
| `docs/backlog.md` (изменить) | пункт про zarplata, отдающую 1 карточку |

---

### Task 1: `NotFound` для HTTP 404

**Files:**
- Modify: `hunter/fetch.py`
- Test: `tests/test_fetch.py`

**Interfaces:**
- Produces: `hunter.fetch.NotFound(FetchError)` — бросается `get_html` на HTTP 404 без повторов.

- [ ] **Step 1: Write the failing test** — дописать в конец `tests/test_fetch.py` (импорт `NotFound` добавить в строку `from hunter.fetch import ...`):

```python
def test_not_found_is_its_own_error_and_is_not_retried():
    client, calls = client_returning(httpx.Response(404))

    with pytest.raises(NotFound) as caught:
        get_html(URL, client=client, sleep=lambda _: None)

    assert isinstance(caught.value, FetchError), "бот ловит FetchError — 404 должен остаться им"
    assert len(calls) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_fetch.py -v`
Expected: ImportError — `cannot import name 'NotFound'`.

- [ ] **Step 3: Implement** — в `hunter/fetch.py` после класса `Blocked`:

```python
class NotFound(FetchError):
    """404: страницы нет — вакансию удалили. Повтор ничего не изменит."""
```

и в `get_html` перед проверкой `>= 500`:

```python
            if response.status_code == 404:
                raise NotFound(f"{url}: 404 — страницы нет")
```

- [ ] **Step 4: Run all tests**

Run: `.venv/bin/python -m pytest`
Expected: всё зелёное (бот ловит `FetchError`, поведение не изменилось).

- [ ] **Step 5: Commit**

```bash
git add hunter/fetch.py tests/test_fetch.py
git commit -m "feat(fetch): distinguish 404 as NotFound

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: HTML → Markdown

**Files:**
- Create: `hunter/corpus/__init__.py` (пустой), `hunter/corpus/markdown.py`
- Test: `tests/test_corpus_markdown.py`

**Interfaces:**
- Produces: `hunter.corpus.markdown.html_to_markdown(html: str) -> str`

- [ ] **Step 1: Write the failing tests** — `tests/test_corpus_markdown.py`:

```python
from hunter.corpus.markdown import html_to_markdown


def test_paragraphs_become_blank_line_separated():
    assert html_to_markdown("<p>Первый</p><p>Второй</p>") == "Первый\n\nВторой"


def test_bold_heading_and_bullet_list():
    html = "<p><strong>Требования:</strong></p><ul><li>Python</li><li>SQL</li></ul>"
    assert html_to_markdown(html) == "**Требования:**\n\n- Python\n- SQL"


def test_ordered_list_is_numbered():
    assert html_to_markdown("<ol><li>a</li><li>b</li></ol>") == "1. a\n2. b"


def test_nested_list_is_indented_without_blank_lines():
    html = "<ul><li>a<ul><li>b</li></ul></li><li>c</li></ul>"
    assert html_to_markdown(html) == "- a\n  - b\n- c"


def test_entities_and_line_breaks():
    assert html_to_markdown("Опыт&nbsp;от 3 лет<br>C&amp;C++") == "Опыт от 3 лет\nC&C++"


def test_whitespace_is_collapsed_and_lines_are_not_indented():
    assert html_to_markdown("<p>\n   много   пробелов \n</p>") == "много пробелов"


def test_unknown_markup_keeps_its_text_readable():
    html = "<table><tr><td>Зарплата</td><td>300к</td></tr></table><p><em>офис</em></p>"
    assert html_to_markdown(html) == "Зарплата 300к\n\n*офис*"


def test_list_after_paragraph_has_single_blank_line():
    assert html_to_markdown("<p>Задачи</p><ul><li>писать код</li></ul>") == "Задачи\n\n- писать код"


def test_empty_input():
    assert html_to_markdown("") == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_corpus_markdown.py -v`
Expected: ModuleNotFoundError `hunter.corpus`.

- [ ] **Step 3: Implement** — пустой `hunter/corpus/__init__.py` и `hunter/corpus/markdown.py`:

```python
from __future__ import annotations

import re
from html.parser import HTMLParser

_BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "table", "tr"}
_BOLD_TAGS = {"strong", "b"}
_ITALIC_TAGS = {"em", "i"}
_CELL_TAGS = {"td", "th"}


class _Converter(HTMLParser):
    """Описания вакансий на hh — простая разметка редактора: абзацы,
    списки, жирный. Всё прочее превращаем в текст, не теряя слов."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.lists: list[list] = []  # [тег, номер последнего пункта]
        self.line_start = True

    def _newline(self, text: str) -> None:
        self.parts.append(text)
        self.line_start = True

    def handle_starttag(self, tag, attrs):
        if tag in _BLOCK_TAGS:
            self._newline("\n\n")
        elif tag == "br":
            self._newline("\n")
        elif tag in ("ul", "ol"):
            # Вложенный список начинается с пункта — пункт сам даёт перевод
            # строки, лишняя пустая строка разорвала бы родительский пункт.
            if not self.lists:
                self._newline("\n\n")
            self.lists.append([tag, 0])
        elif tag == "li":
            indent = "  " * max(len(self.lists) - 1, 0)
            if self.lists and self.lists[-1][0] == "ol":
                self.lists[-1][1] += 1
                marker = f"{self.lists[-1][1]}. "
            else:
                marker = "- "
            self._newline(f"\n{indent}{marker}")
        elif tag in _BOLD_TAGS:
            self.parts.append("**")
        elif tag in _ITALIC_TAGS:
            self.parts.append("*")
        elif tag in _CELL_TAGS and not self.line_start:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in _BLOCK_TAGS:
            self._newline("\n\n")
        elif tag in ("ul", "ol"):
            if self.lists:
                self.lists.pop()
            if not self.lists:
                self._newline("\n\n")
        elif tag in _BOLD_TAGS:
            self.parts.append("**")
        elif tag in _ITALIC_TAGS:
            self.parts.append("*")

    def handle_data(self, data):
        text = re.sub(r"\s+", " ", data)
        if self.line_start:
            text = text.lstrip()
        if text:
            self.parts.append(text)
            self.line_start = False


def html_to_markdown(html: str) -> str:
    converter = _Converter()
    converter.feed(html)
    converter.close()
    lines = [line.rstrip() for line in "".join(converter.parts).split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_corpus_markdown.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add hunter/corpus/__init__.py hunter/corpus/markdown.py tests/test_corpus_markdown.py
git commit -m "feat(corpus): convert vacancy description HTML to Markdown

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Разбор карточки выдачи и страницы вакансии

**Files:**
- Create: `hunter/corpus/extract.py`, `tests/fixtures/hh_vacancy_page.html`
- Modify: `tests/factories.py`
- Test: `tests/test_corpus_extract.py`

**Interfaces:**
- Consumes: `hunter.parse.extract_state(page_html) -> dict`, `hunter.parse.ParseError`, `hunter.models.UNKNOWN_VACANCY_NAME`, `UNKNOWN_COMPANY`, `html_to_markdown` (Task 2).
- Produces:
  - `VACANCY_URL = "https://hh.ru/vacancy/{id}"`
  - `class Captcha(Exception)` — 200 без `HH-Lux-InitialState`
  - `search_cards(page_html: str) -> list[dict]` — бросает `Captcha` / `ParseError`
  - `card_row(raw: dict) -> dict` — ключи: `id`, `name`, `company`, `company_id`, `url`, `area`, `published_at`, `salary_from`, `salary_to`, `currency`, `gross`, `salary_frequency`, `experience`, `work_formats` (JSON-строка), `employment_form`, `employer_trusted`, `employer_category`, `antifraud_power`, `responses_count`, `card_json`. Бросает `KeyError`/`TypeError`/`ValueError`/`AttributeError` на битой карточке.
  - `@dataclass(frozen=True) Detail(vacancy_id: int, row: dict, description: str, key_skills: list[str], status: str, raw: dict)` — `row` с теми же ключами, что у `card_row` (`antifraud_power`, `responses_count`, `card_json` = None)
  - `parse_detail(page_html: str) -> Detail` — бросает `Captcha` / `ParseError`
  - в `tests/factories.py`: `state_page(state) -> str`, `search_card(vacancy_id, **overrides) -> dict`, `search_page(cards) -> str`, `detail_vacancy(vacancy_id, **overrides) -> dict`, `vacancy_page(vacancy_id, **overrides) -> str`

- [ ] **Step 1: Снять фикстуру реальной страницы вакансии** (сеть, один запрос выдачи и один страницы). Выполнить из корня репозитория:

```bash
.venv/bin/python - <<'EOF'
import html, json
from pathlib import Path
from hunter.fetch import new_client, get_html
from hunter.parse import extract_state
from hunter.sources import build_url
from hunter.models import SearchQuery

client = new_client()
cards = extract_state(get_html(build_url("hh", SearchQuery(name="f", text="python backend", area=1)), client=client))["vacancySearchResult"]["vacancies"]
for card in cards:
    state = extract_state(get_html(f"https://hh.ru/vacancy/{card['vacancyId']}", client=client))
    vacancy = state["vacancyView"]["vacancyFull"]["vacancy"]
    if vacancy.get("keySkills") and "<li>" in (vacancy.get("description") or ""):
        break
vacancy.pop("employerManager", None)  # имена рекрутеров в репозитории не нужны
small = {"vacancyView": {"vacancyFull": {"vacancy": vacancy}}}
payload = html.escape(json.dumps(small, ensure_ascii=False))
Path("tests/fixtures/hh_vacancy_page.html").write_text(
    f'<html><body><template id="HH-Lux-InitialState">{payload}</template></body></html>',
    encoding="utf-8",
)
print("saved", vacancy["vacancyId"])
EOF
```

Expected: `saved <id>`, файл в десятки КБ, а не 700.

- [ ] **Step 2: Дописать фабрики** в конец `tests/factories.py` (импорты `html as html_module` и `json` — в начало файла):

```python
def state_page(state: dict) -> str:
    """Страница с HH-Lux-InitialState — как её отдаёт hh, только без вёрстки."""
    payload = html_module.escape(json.dumps(state, ensure_ascii=False))
    return f'<html><body><template id="HH-Lux-InitialState">{payload}</template></body></html>'


def search_card(vacancy_id: int, **overrides) -> dict:
    card = {
        "vacancyId": vacancy_id,
        "name": f"Вакансия {vacancy_id}",
        "company": {"id": 1, "visibleName": "Компания", "@trusted": True, "@category": "COMPANY"},
        "compensation": {"from": 300000, "currencyCode": "RUR", "gross": False, "frequency": "MONTHLY"},
        "area": {"name": "Москва"},
        "publicationTime": {"$": "2026-10-01T10:00:00.000+03:00"},
        "workExperience": "between3And6",
        "workFormats": [{"workFormatsElement": ["REMOTE"]}],
        "employmentForm": "FULL",
        "responsesCount": 5,
    }
    card.update(overrides)
    return card


def search_page(cards: list[dict]) -> str:
    return state_page({"vacancySearchResult": {"vacancies": cards}})


def detail_vacancy(vacancy_id: int, **overrides) -> dict:
    vacancy = {
        "vacancyId": vacancy_id,
        "name": f"Вакансия {vacancy_id}",
        "description": "<p>Пишем на <strong>Python</strong></p>",
        "keySkills": ["Python", "SQL"],
        "status": {"archived": False},
        "company": {"id": 1, "visibleName": "Компания", "trusted": True, "category": "COMPANY"},
        "area": {"name": "Москва"},
        "compensation": {"to": 280000, "currencyCode": "RUR", "gross": False, "frequency": "TWICE_PER_MONTH"},
        "publicationTimeIso": "2026-10-02T14:49:07.426+03:00",
        "workExperience": "between3And6",
        "employmentForm": "FULL",
        "workFormats": ["ON_SITE"],
    }
    vacancy.update(overrides)
    return vacancy


def vacancy_page(vacancy_id: int, **overrides) -> str:
    return state_page({"vacancyView": {"vacancyFull": {"vacancy": detail_vacancy(vacancy_id, **overrides)}}})
```

- [ ] **Step 3: Write the failing tests** — `tests/test_corpus_extract.py`:

```python
import json
import re
from pathlib import Path

import pytest

from hunter.corpus.extract import Captcha, card_row, parse_detail, search_cards
from hunter.parse import ParseError, extract_state
from tests.factories import detail_vacancy, search_card, search_page, state_page, vacancy_page

FIXTURES = Path(__file__).parent / "fixtures"


def real_cards(name):
    state = extract_state((FIXTURES / name).read_text(encoding="utf-8"))
    return state["vacancySearchResult"]["vacancies"]


def test_card_row_extracts_quality_signals_from_a_real_card():
    raw = next(c for c in real_cards("hh_search_sorted_with_salary.html") if c["vacancyId"] == 136823260)

    row = card_row(raw)

    assert row["id"] == 136823260
    assert row["url"] == "https://hh.ru/vacancy/136823260", "не региональный поддомен"
    assert row["employer_category"] == "PRIVATE_INDIVIDUAL"
    assert row["employer_trusted"] == 0
    assert row["antifraud_power"] == pytest.approx(0.8174265494414773)
    assert row["salary_frequency"] == "PER_PROJECT"
    assert row["responses_count"] == 19
    assert row["published_at"] == "2026-09-01T09:41:55.502+03:00"
    assert json.loads(row["work_formats"]) == ["REMOTE"]
    assert json.loads(row["card_json"])["vacancyId"] == 136823260


def test_card_without_salary_or_antifraud():
    raw = real_cards("zarplata_search_python.html")[0]

    row = card_row(raw)

    assert row["salary_from"] is None and row["salary_to"] is None
    assert row["currency"] is None
    assert row["antifraud_power"] is None
    assert row["employer_trusted"] == 1


def test_antifraud_without_power_counts_as_flagged():
    raw = search_card(1, vacancyProperties={"properties": [{"property": [
        {"propertyType": "HH_ANTIFRAUD_SUSPICIOUS_VACANCY", "parameters": []},
    ]}]})

    assert card_row(raw)["antifraud_power"] == 0.0


def test_card_without_id_is_an_error():
    raw = search_card(1)
    del raw["vacancyId"]

    with pytest.raises(KeyError):
        card_row(raw)


def test_search_cards_returns_raw_cards():
    assert [c["vacancyId"] for c in search_cards(search_page([search_card(1), search_card(2)]))] == [1, 2]


def test_page_without_state_is_a_captcha():
    with pytest.raises(Captcha):
        search_cards("<html><body>Подтвердите, что вы не робот</body></html>")


def test_state_without_search_result_is_a_parse_error():
    with pytest.raises(ParseError):
        search_cards(state_page({"somethingElse": {}}))


def test_parse_detail_builds_description_and_row():
    detail = parse_detail(vacancy_page(42))

    assert detail.vacancy_id == 42
    assert detail.description == "Пишем на **Python**"
    assert detail.key_skills == ["Python", "SQL"]
    assert detail.status == "active"
    assert detail.row["url"] == "https://hh.ru/vacancy/42"
    assert detail.row["employer_trusted"] == 1
    assert detail.row["salary_to"] == 280000
    assert json.loads(detail.row["work_formats"]) == ["ON_SITE"]
    assert detail.row["card_json"] is None
    assert detail.row["antifraud_power"] is None


def test_archived_vacancy_status():
    assert parse_detail(vacancy_page(42, status={"archived": True})).status == "archived"


def test_detail_page_without_vacancy_is_a_parse_error():
    with pytest.raises(ParseError):
        parse_detail(state_page({"vacancyView": {}}))


def test_detail_page_without_state_is_a_captcha():
    with pytest.raises(Captcha):
        parse_detail("<html>captcha</html>")


def test_real_vacancy_page():
    detail = parse_detail((FIXTURES / "hh_vacancy_page.html").read_text(encoding="utf-8"))

    assert detail.vacancy_id > 0
    assert detail.description
    assert re.search(r"<[a-z]", detail.description) is None, "в описании остались теги"
    assert detail.key_skills and all(isinstance(s, str) for s in detail.key_skills)
    assert detail.status in ("active", "archived")
    assert detail.row["name"]
    assert isinstance(json.loads(detail.row["work_formats"]), list)
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_corpus_extract.py -v`
Expected: ModuleNotFoundError `hunter.corpus.extract`.

- [ ] **Step 5: Implement** — `hunter/corpus/extract.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass

from ..models import UNKNOWN_COMPANY, UNKNOWN_VACANCY_NAME
from ..parse import ParseError, extract_state
from .markdown import html_to_markdown

# Один адрес для вакансий hh и zarplata: у zarplata те же id (кросспостинг hh).
VACANCY_URL = "https://hh.ru/vacancy/{id}"

_STATE_MARKER = 'id="HH-Lux-InitialState"'
_ANTIFRAUD = "HH_ANTIFRAUD_SUSPICIOUS_VACANCY"


class Captcha(Exception):
    """Ответ 200, но без состояния страницы — капча или заглушка.
    По смыслу это блокировка, как 403: продолжать нельзя."""


def _page_state(page_html: str) -> dict:
    # Нет маркера вовсе — нам подсунули другую страницу. Маркер есть, но JSON
    # не разобрался — это уже смена формата, ParseError из extract_state.
    if _STATE_MARKER not in page_html:
        raise Captcha("в ответе нет HH-Lux-InitialState — похоже на капчу")
    return extract_state(page_html)


def search_cards(page_html: str) -> list[dict]:
    result = _page_state(page_html).get("vacancySearchResult")
    if not isinstance(result, dict) or not isinstance(result.get("vacancies"), list):
        raise ParseError("В состоянии страницы нет vacancySearchResult.vacancies")
    return result["vacancies"]


def _flag(value) -> int | None:
    return None if value is None else int(bool(value))


def _identity(raw: dict, company: dict) -> dict:
    vacancy_id = int(raw["vacancyId"])
    return {
        "id": vacancy_id,
        "name": raw.get("name") or UNKNOWN_VACANCY_NAME,
        "company": company.get("visibleName") or company.get("name") or UNKNOWN_COMPANY,
        "company_id": company.get("id"),
        "url": VACANCY_URL.format(id=vacancy_id),
        "area": (raw.get("area") or {}).get("name"),
    }


def _salary(compensation: dict) -> dict:
    return {
        "salary_from": compensation.get("from"),
        "salary_to": compensation.get("to"),
        "currency": compensation.get("currencyCode"),
        "gross": _flag(compensation.get("gross")),
        "salary_frequency": compensation.get("frequency"),
    }


def _antifraud_power(raw: dict) -> float | None:
    for block in (raw.get("vacancyProperties") or {}).get("properties") or []:
        for prop in block.get("property") or []:
            if prop.get("propertyType") != _ANTIFRAUD:
                continue
            for params in prop.get("parameters") or []:
                for param in params.get("parameter") or []:
                    if param.get("key") == "antifraudPessimizePower":
                        return float(param["value"])
            # Свойство есть, силы нет — всё равно помечена антифродом.
            return 0.0
    return None


def card_row(raw: dict) -> dict:
    """Карточка из выдачи → колонки vacancies, кроме отметок времени
    и полей страницы вакансии."""
    company = raw.get("company") or {}
    return {
        **_identity(raw, company),
        "published_at": (raw.get("publicationTime") or {}).get("$"),
        **_salary(raw.get("compensation") or {}),
        "experience": raw.get("workExperience"),
        "work_formats": json.dumps([
            element
            for block in (raw.get("workFormats") or [])
            for element in (block.get("workFormatsElement") or [])
        ]),
        "employment_form": raw.get("employmentForm"),
        "employer_trusted": _flag(company.get("@trusted")),
        "employer_category": company.get("@category"),
        "antifraud_power": _antifraud_power(raw),
        "responses_count": raw.get("responsesCount"),
        "card_json": json.dumps(raw, ensure_ascii=False),
    }


@dataclass(frozen=True, slots=True)
class Detail:
    """Страница вакансии: полный текст плюс общие поля — на случай,
    если вакансию добавляют по ссылке и в выдаче её не было."""

    vacancy_id: int
    row: dict
    description: str
    key_skills: list[str]
    status: str
    raw: dict


def parse_detail(page_html: str) -> Detail:
    state = _page_state(page_html)
    try:
        raw = state["vacancyView"]["vacancyFull"]["vacancy"]
        vacancy_id = int(raw["vacancyId"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ParseError(
            f"На странице нет vacancyView.vacancyFull.vacancy ({exc!r}) — сайт сменил формат"
        ) from exc
    # На странице вакансии у company нет префикса @, а workFormats — плоский список.
    company = raw.get("company") or {}
    row = {
        **_identity(raw, company),
        "published_at": raw.get("publicationTimeIso"),
        **_salary(raw.get("compensation") or {}),
        "experience": raw.get("workExperience"),
        "work_formats": json.dumps(list(raw.get("workFormats") or [])),
        "employment_form": raw.get("employmentForm"),
        "employer_trusted": _flag(company.get("trusted")),
        "employer_category": company.get("category"),
        "antifraud_power": None,
        "responses_count": None,
        "card_json": None,
    }
    return Detail(
        vacancy_id=vacancy_id,
        row=row,
        description=html_to_markdown(raw.get("description") or ""),
        key_skills=[str(skill) for skill in raw.get("keySkills") or []],
        status="archived" if (raw.get("status") or {}).get("archived") else "active",
        raw=raw,
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_corpus_extract.py -v`
Expected: 12 passed.

- [ ] **Step 7: Commit**

```bash
git add hunter/corpus/extract.py tests/factories.py tests/test_corpus_extract.py tests/fixtures/hh_vacancy_page.html
git commit -m "feat(corpus): extract corpus rows from search cards and vacancy pages

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Хранилище корпуса

**Files:**
- Create: `hunter/corpus/store.py`
- Test: `tests/test_corpus_store.py`

**Interfaces:**
- Consumes: `Detail`, `parse_detail` (Task 3); фабрики `search_card`, `vacancy_page`.
- Produces:
  - `class CorpusError(Exception)`; `VERDICTS = ("yes", "no", "maybe")`; `SCHEMA_VERSION: int`
  - `Corpus.open(path, *, create=False, clock=...) -> Corpus` — миграции; без `create` нет файла → `CorpusError`
  - `Corpus.open_readonly(path) -> Corpus` — `mode=ro`; файла нет или версия схемы ≠ `SCHEMA_VERSION` → `CorpusError`
  - контекстный менеджер; `close()`
  - `exists(vacancy_id: int) -> bool`
  - `upsert_card(row: dict) -> bool` — True, если вакансия новая
  - `add_hit(vacancy_id: int, role: str, query: str) -> None`
  - `has_detail(vacancy_id: int) -> bool` — полный текст скачан или `status='deleted'`
  - `save_detail(detail: Detail) -> None`
  - `mark_deleted(vacancy_id: int) -> bool` — False, если строки нет
  - `set_verdict(vacancy_id: int, verdict: str, note: str | None = None) -> None` — нет вакансии → `CorpusError`
  - `vacancy(vacancy_id) -> sqlite3.Row | None`, `hits(vacancy_id) -> list[sqlite3.Row]`, `verdict(vacancy_id) -> sqlite3.Row | None`
  - `query(sql: str) -> tuple[list[str], list[tuple]]`
  - `clock` — вызываемое без аргументов, возвращает ISO-строку

- [ ] **Step 1: Write the failing tests** — `tests/test_corpus_store.py`:

```python
import sqlite3

import pytest

from hunter.corpus.extract import card_row, parse_detail
from hunter.corpus.store import SCHEMA_VERSION, Corpus, CorpusError
from tests.factories import search_card, vacancy_page


class FakeClock:
    def __init__(self):
        self.now = "2026-10-01T00:00:00+00:00"

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def corpus(tmp_path, clock):
    with Corpus.open(tmp_path / "v.db", create=True, clock=clock) as opened:
        yield opened


def test_new_database_gets_current_schema(tmp_path):
    Corpus.open(tmp_path / "v.db", create=True).close()

    conn = sqlite3.connect(tmp_path / "v.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 1
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"vacancies", "hits", "verdicts"} <= tables


def test_missing_database_is_not_created_implicitly(tmp_path):
    with pytest.raises(CorpusError, match="vacancies init"):
        Corpus.open(tmp_path / "v.db")
    assert not (tmp_path / "v.db").exists()


def test_database_from_newer_code_is_refused(tmp_path):
    conn = sqlite3.connect(tmp_path / "v.db")
    conn.execute("PRAGMA user_version = 99")
    conn.close()

    with pytest.raises(CorpusError, match="новее|более новой"):
        Corpus.open(tmp_path / "v.db")


def test_upsert_reports_new_then_existing_and_refreshes_card(corpus, clock):
    assert corpus.upsert_card(card_row(search_card(1, responsesCount=5))) is True
    clock.now = "2026-10-05T00:00:00+00:00"
    assert corpus.upsert_card(card_row(search_card(1, responsesCount=40))) is False

    row = corpus.vacancy(1)
    assert row["responses_count"] == 40
    assert row["first_seen_at"] == "2026-10-01T00:00:00+00:00"
    assert row["last_seen_at"] == "2026-10-05T00:00:00+00:00"


def test_recollect_keeps_description_and_verdict(corpus):
    corpus.upsert_card(card_row(search_card(1)))
    corpus.save_detail(parse_detail(vacancy_page(1)))
    corpus.set_verdict(1, "yes", "огонь")

    corpus.upsert_card(card_row(search_card(1)))

    assert corpus.vacancy(1)["description"] == "Пишем на **Python**"
    assert corpus.verdict(1)["verdict"] == "yes"


def test_save_detail_creates_missing_row_from_the_page(corpus):
    corpus.save_detail(parse_detail(vacancy_page(7)))

    row = corpus.vacancy(7)
    assert row["name"] == "Вакансия 7"
    assert row["card_json"] is None
    assert row["status"] == "active"
    assert row["key_skills"] == '["Python", "SQL"]'
    assert row["detail_fetched_at"] is not None


def test_save_detail_does_not_overwrite_card_fields(corpus):
    corpus.upsert_card(card_row(search_card(1, name="Из выдачи")))
    corpus.save_detail(parse_detail(vacancy_page(1, name="Со страницы")))

    assert corpus.vacancy(1)["name"] == "Из выдачи"


def test_has_detail_after_fetch_and_after_deletion(corpus):
    corpus.upsert_card(card_row(search_card(1)))
    corpus.upsert_card(card_row(search_card(2)))
    assert not corpus.has_detail(1)

    corpus.save_detail(parse_detail(vacancy_page(1)))
    assert corpus.mark_deleted(2) is True

    assert corpus.has_detail(1)
    assert corpus.has_detail(2)
    assert corpus.vacancy(2)["status"] == "deleted"


def test_mark_deleted_unknown_vacancy(corpus):
    assert corpus.mark_deleted(999) is False
    assert not corpus.has_detail(999)


def test_add_hit_is_idempotent(corpus):
    corpus.upsert_card(card_row(search_card(1)))
    corpus.add_hit(1, "backend", "python")
    corpus.add_hit(1, "backend", "python")
    corpus.add_hit(1, "data", "dwh")

    assert [(h["role"], h["query"]) for h in corpus.hits(1)] == [("backend", "python"), ("data", "dwh")]


def test_verdict_is_overwritten(corpus):
    corpus.upsert_card(card_row(search_card(1)))
    corpus.set_verdict(1, "maybe", "посмотреть стек")
    corpus.set_verdict(1, "no")

    verdict = corpus.verdict(1)
    assert verdict["verdict"] == "no"
    assert verdict["note"] is None


def test_verdict_for_unknown_vacancy(corpus):
    with pytest.raises(CorpusError, match="vacancies fetch 5"):
        corpus.set_verdict(5, "yes")


def test_readonly_queries_and_refuses_writes(tmp_path):
    with Corpus.open(tmp_path / "v.db", create=True) as corpus:
        corpus.upsert_card(card_row(search_card(1)))

    with Corpus.open_readonly(tmp_path / "v.db") as readonly:
        columns, rows = readonly.query("SELECT id, name FROM vacancies")
        assert columns == ["id", "name"]
        assert rows == [(1, "Вакансия 1")]
        with pytest.raises(sqlite3.OperationalError):
            readonly.query("DELETE FROM vacancies")

    with Corpus.open(tmp_path / "v.db") as corpus:
        assert corpus.exists(1)


def test_readonly_missing_or_outdated_database(tmp_path):
    with pytest.raises(CorpusError, match="vacancies init"):
        Corpus.open_readonly(tmp_path / "nope.db")

    sqlite3.connect(tmp_path / "old.db").execute("CREATE TABLE x (a)").connection.close()
    with pytest.raises(CorpusError, match="vacancies init"):
        Corpus.open_readonly(tmp_path / "old.db")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_corpus_store.py -v`
Expected: ModuleNotFoundError `hunter.corpus.store`.

- [ ] **Step 3: Implement** — `hunter/corpus/store.py`:

```python
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .extract import Detail

VERDICTS = ("yes", "no", "maybe")

# Индекс — номер версии, с которой шаг переводит базу на следующую.
# Менять старые шаги нельзя: у владельца уже есть базы, созданные ими.
_MIGRATIONS = (
    """
    CREATE TABLE vacancies (
      id                INTEGER PRIMARY KEY,
      name              TEXT NOT NULL,
      company           TEXT NOT NULL,
      company_id        INTEGER,
      url               TEXT NOT NULL,
      area              TEXT,
      published_at      TEXT,
      salary_from       INTEGER,
      salary_to         INTEGER,
      currency          TEXT,
      gross             INTEGER,
      salary_frequency  TEXT,
      experience        TEXT,
      work_formats      TEXT,
      employment_form   TEXT,
      employer_trusted  INTEGER,
      employer_category TEXT,
      antifraud_power   REAL,
      responses_count   INTEGER,
      description       TEXT,
      key_skills        TEXT,
      status            TEXT,
      detail_fetched_at TEXT,
      card_json         TEXT,
      detail_json       TEXT,
      first_seen_at     TEXT NOT NULL,
      last_seen_at      TEXT NOT NULL
    );
    CREATE TABLE hits (
      vacancy_id     INTEGER NOT NULL REFERENCES vacancies(id),
      role           TEXT NOT NULL,
      query          TEXT NOT NULL,
      first_found_at TEXT NOT NULL,
      PRIMARY KEY (vacancy_id, role, query)
    );
    CREATE TABLE verdicts (
      vacancy_id INTEGER PRIMARY KEY REFERENCES vacancies(id),
      verdict    TEXT NOT NULL CHECK (verdict IN ('yes','no','maybe')),
      note       TEXT,
      updated_at TEXT NOT NULL
    );
    """,
)
SCHEMA_VERSION = len(_MIGRATIONS)

# Колонки, которые приходят из карточки выдачи (или со страницы, если
# карточки не было) и обновляются при каждом повторном сборе.
_CARD_COLUMNS = (
    "name", "company", "company_id", "url", "area", "published_at",
    "salary_from", "salary_to", "currency", "gross", "salary_frequency",
    "experience", "work_formats", "employment_form",
    "employer_trusted", "employer_category", "antifraud_power",
    "responses_count", "card_json",
)


class CorpusError(Exception):
    """База недоступна или несовместима — сообщение для человека."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _migrate(conn: sqlite3.Connection) -> None:
    version = _version(conn)
    if version > SCHEMA_VERSION:
        raise CorpusError(
            f"База создана более новой версией vacancy-hunter (схема v{version}, "
            f"код знает v{SCHEMA_VERSION}). Обновите код."
        )
    for number in range(version, SCHEMA_VERSION):
        conn.executescript(_MIGRATIONS[number])
        conn.execute(f"PRAGMA user_version = {number + 1}")
        conn.commit()


def _missing(path: Path) -> CorpusError:
    return CorpusError(
        f"Нет базы {path}. Запустите команду в папке поиска работы "
        f"или создайте базу: vacancies init"
    )


class Corpus:
    """Корпус вакансий. Каждый метод записи коммитит сразу: прерванный
    сбор продолжается с места остановки, ничего не теряется."""

    def __init__(self, conn: sqlite3.Connection, clock: Callable[[], str] = _now_iso) -> None:
        conn.row_factory = sqlite3.Row
        self._conn = conn
        self._clock = clock

    @classmethod
    def open(cls, path: Path | str, *, create: bool = False,
             clock: Callable[[], str] = _now_iso) -> Corpus:
        path = Path(path)
        # Без явного init не создаём: команда, запущенная не в той папке,
        # иначе молча заведёт там пустую базу.
        if not create and not path.exists():
            raise _missing(path)
        conn = sqlite3.connect(str(path), timeout=30)
        try:
            _migrate(conn)
        except BaseException:
            conn.close()
            raise
        return cls(conn, clock)

    @classmethod
    def open_readonly(cls, path: Path | str) -> Corpus:
        path = Path(path)
        if not path.exists():
            raise _missing(path)
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
        version = _version(conn)
        if version != SCHEMA_VERSION:
            conn.close()
            raise CorpusError(
                f"Схема базы v{version}, код ждёт v{SCHEMA_VERSION}. "
                f"Обновите схему: vacancies init (данные сохранятся)"
            )
        return cls(conn)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Corpus:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def exists(self, vacancy_id: int) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM vacancies WHERE id = ?", (vacancy_id,)
        ).fetchone() is not None

    def upsert_card(self, row: dict) -> bool:
        is_new = not self.exists(row["id"])
        now = self._clock()
        columns = ("id", *_CARD_COLUMNS, "first_seen_at", "last_seen_at")
        values = (row["id"], *(row[c] for c in _CARD_COLUMNS), now, now)
        updates = ", ".join(f"{c} = excluded.{c}" for c in (*_CARD_COLUMNS, "last_seen_at"))
        self._conn.execute(
            f"INSERT INTO vacancies ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' * len(columns))}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}",
            values,
        )
        self._conn.commit()
        return is_new

    def add_hit(self, vacancy_id: int, role: str, query: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO hits (vacancy_id, role, query, first_found_at) "
            "VALUES (?, ?, ?, ?)",
            (vacancy_id, role, query, self._clock()),
        )
        self._conn.commit()

    def has_detail(self, vacancy_id: int) -> bool:
        row = self._conn.execute(
            "SELECT detail_fetched_at IS NOT NULL OR status = 'deleted' "
            "FROM vacancies WHERE id = ?",
            (vacancy_id,),
        ).fetchone()
        return bool(row and row[0])

    def save_detail(self, detail: Detail) -> None:
        # Поля карточки у существующей строки не трогаем: выдача — основной
        # источник метаданных, страница лишь дополняет текстом.
        if not self.exists(detail.vacancy_id):
            self.upsert_card(detail.row)
        self._conn.execute(
            "UPDATE vacancies SET description = ?, key_skills = ?, status = ?, "
            "detail_json = ?, detail_fetched_at = ? WHERE id = ?",
            (
                detail.description,
                json.dumps(detail.key_skills, ensure_ascii=False),
                detail.status,
                json.dumps(detail.raw, ensure_ascii=False),
                self._clock(),
                detail.vacancy_id,
            ),
        )
        self._conn.commit()

    def mark_deleted(self, vacancy_id: int) -> bool:
        cursor = self._conn.execute(
            "UPDATE vacancies SET status = 'deleted', detail_fetched_at = ? WHERE id = ?",
            (self._clock(), vacancy_id),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def set_verdict(self, vacancy_id: int, verdict: str, note: str | None = None) -> None:
        if verdict not in VERDICTS:
            raise CorpusError(f"Вердикт должен быть одним из: {', '.join(VERDICTS)}")
        if not self.exists(vacancy_id):
            raise CorpusError(
                f"Вакансии {vacancy_id} нет в базе. Сначала: vacancies fetch {vacancy_id}"
            )
        self._conn.execute(
            "INSERT INTO verdicts (vacancy_id, verdict, note, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(vacancy_id) DO UPDATE SET verdict = excluded.verdict, "
            "note = excluded.note, updated_at = excluded.updated_at",
            (vacancy_id, verdict, note, self._clock()),
        )
        self._conn.commit()

    def vacancy(self, vacancy_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM vacancies WHERE id = ?", (vacancy_id,)
        ).fetchone()

    def hits(self, vacancy_id: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT role, query, first_found_at FROM hits WHERE vacancy_id = ? "
            "ORDER BY role, query",
            (vacancy_id,),
        ).fetchall()

    def verdict(self, vacancy_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT verdict, note, updated_at FROM verdicts WHERE vacancy_id = ?",
            (vacancy_id,),
        ).fetchone()

    def query(self, sql: str) -> tuple[list[str], list[tuple]]:
        cursor = self._conn.execute(sql)
        columns = [d[0] for d in cursor.description or []]
        return columns, [tuple(row) for row in cursor.fetchall()]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_corpus_store.py -v`
Expected: 14 passed.

- [ ] **Step 5: Commit**

```bash
git add hunter/corpus/store.py tests/test_corpus_store.py
git commit -m "feat(corpus): SQLite corpus store with migrations and verdicts

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Сбор выдачи и докачка полного текста

**Files:**
- Create: `hunter/corpus/collect.py`
- Test: `tests/test_corpus_collect.py`

**Interfaces:**
- Consumes: `Corpus` (Task 4); `search_cards`, `card_row`, `parse_detail`, `Captcha`, `VACANCY_URL` (Task 3); `hunter.fetch.Blocked`, `FetchError`, `NotFound` (Task 1); `hunter.sources.build_url(source, query, page)`; `hunter.models.SearchQuery`; `hunter.parse.ParseError`.
- Produces:
  - `FetchPage = Callable[[str], str]`
  - `MANUAL_QUERY = "manual"`, `ITEMS_PER_PAGE = 50`, `EXIT_OK = 0`, `EXIT_PARTIAL = 1`, `EXIT_BLOCKED = 3`
  - `CollectReport(role, cards, new, pages, error, blocked)` + свойства `existing`, `exit_code`, метод `summary() -> str`
  - `FetchReport(fetched, skipped, deleted, failed: list[tuple[int, str]], not_reached, blocked)` + `exit_code`, `summary() -> str`
  - `collect(corpus, fetch_page, query: SearchQuery, role: str, wait=...) -> CollectReport`
  - `fetch_details(corpus, fetch_page, ids: list[int], *, role=None, refresh=False, wait=...) -> FetchReport`
  - `wait` — вызываемое без аргументов; вызывается перед каждым запросом, кроме первого.

- [ ] **Step 1: Write the failing tests** — `tests/test_corpus_collect.py`:

```python
import pytest

from hunter.corpus.collect import (
    EXIT_BLOCKED, EXIT_OK, EXIT_PARTIAL, collect, fetch_details,
)
from hunter.corpus.store import Corpus
from hunter.fetch import Blocked, FetchError, NotFound
from hunter.models import SearchQuery
from hunter.sources import build_url
from tests.factories import search_card, search_page, vacancy_page

QUERY = SearchQuery(name="backend", text="python", area=113, only_with_salary=False,
                    search_period=30, max_pages=3)
CAPTCHA = "<html><body>Вы не робот?</body></html>"


def page_url(n):
    return build_url("hh", QUERY, n)


def vacancy_url(vacancy_id):
    return f"https://hh.ru/vacancy/{vacancy_id}"


def full_page(start, count=50):
    return search_page([search_card(i) for i in range(start, start + count)])


class Site:
    """Заготовленные ответы по URL; исключение в словаре — бросается."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        item = self.pages[url]
        if isinstance(item, Exception):
            raise item
        return item


class Waits:
    def __init__(self):
        self.count = 0

    def __call__(self):
        self.count += 1


@pytest.fixture
def corpus(tmp_path):
    with Corpus.open(tmp_path / "v.db", create=True) as opened:
        yield opened


def test_collect_stops_on_short_page_and_records_hits(corpus):
    site = Site({page_url(0): full_page(1), page_url(1): full_page(100, 10)})
    waits = Waits()

    report = collect(corpus, site, QUERY, "backend", wait=waits)

    assert site.calls == [page_url(0), page_url(1)]
    assert (report.cards, report.new, report.existing, report.pages) == (60, 60, 0, 2)
    assert report.exit_code == EXIT_OK
    assert waits.count == 1, "пауза между страницами, но не перед первой"
    assert [(h["role"], h["query"]) for h in corpus.hits(100)] == [("backend", "python")]


def test_collect_respects_max_pages(corpus):
    site = Site({page_url(n): full_page(n * 100) for n in range(5)})

    report = collect(corpus, site, QUERY, "backend")

    assert len(site.calls) == 3
    assert report.pages == 3


def test_collect_empty_result(corpus):
    report = collect(corpus, Site({page_url(0): search_page([])}), QUERY, "backend")

    assert (report.cards, report.pages, report.exit_code) == (0, 1, EXIT_OK)


def test_ad_duplicate_on_next_page_is_counted_once(corpus):
    second = search_page([search_card(1, **{"@isAdv": True}), search_card(500), search_card(501)])
    site = Site({page_url(0): full_page(1), page_url(1): second})

    report = collect(corpus, site, QUERY, "backend")

    assert report.cards == 52
    assert report.new == 52


def test_second_collect_counts_existing(corpus):
    pages = {page_url(0): full_page(1, 10)}
    collect(corpus, Site(pages), QUERY, "backend")

    report = collect(corpus, Site(pages), QUERY, "backend")

    assert (report.cards, report.new, report.existing) == (10, 0, 10)


def test_block_stops_collect_and_keeps_what_was_saved(corpus):
    site = Site({page_url(0): full_page(1), page_url(1): Blocked("403")})

    report = collect(corpus, site, QUERY, "backend")

    assert report.exit_code == EXIT_BLOCKED
    assert report.cards == 50
    assert corpus.exists(1)
    assert "блокировка" in report.summary()


def test_captcha_counts_as_block(corpus):
    report = collect(corpus, Site({page_url(0): CAPTCHA}), QUERY, "backend")

    assert report.exit_code == EXIT_BLOCKED


def test_network_failure_stops_with_partial_code(corpus):
    report = collect(corpus, Site({page_url(0): FetchError("timeout")}), QUERY, "backend")

    assert report.exit_code == EXIT_PARTIAL
    assert "timeout" in report.summary()


def test_broken_card_rejects_the_whole_page(corpus):
    broken = search_card(2)
    del broken["vacancyId"]
    site = Site({page_url(0): search_page([search_card(1), broken])})

    report = collect(corpus, site, QUERY, "backend")

    assert report.exit_code == EXIT_PARTIAL
    assert not corpus.exists(1), "страница пишется целиком или никак"


def test_fetch_details_saves_text_and_pauses_between_requests(corpus):
    site = Site({vacancy_url(1): vacancy_page(1), vacancy_url(2): vacancy_page(2)})
    waits = Waits()

    report = fetch_details(corpus, site, [1, 2], wait=waits)

    assert report.fetched == [1, 2]
    assert report.exit_code == EXIT_OK
    assert waits.count == 1
    assert corpus.vacancy(2)["description"] == "Пишем на **Python**"


def test_fetch_details_skips_known_text_unless_refresh(corpus):
    fetch_details(corpus, Site({vacancy_url(1): vacancy_page(1)}), [1])

    site = Site({vacancy_url(1): vacancy_page(1, description="<p>Новое</p>")})
    assert fetch_details(corpus, site, [1]).skipped == [1]
    assert site.calls == []

    assert fetch_details(corpus, site, [1], refresh=True).fetched == [1]
    assert corpus.vacancy(1)["description"] == "Новое"


def test_fetch_details_marks_deleted_vacancies(corpus):
    fetch_details(corpus, Site({vacancy_url(1): vacancy_page(1)}), [1])
    site = Site({vacancy_url(1): NotFound("404"), vacancy_url(9): NotFound("404")})

    report = fetch_details(corpus, site, [1, 9], refresh=True)

    assert report.deleted == [1, 9]
    assert report.exit_code == EXIT_OK
    assert corpus.vacancy(1)["status"] == "deleted"
    assert not corpus.exists(9), "строку для неизвестной удалённой вакансии не заводим"


def test_one_failure_does_not_stop_the_rest(corpus):
    site = Site({vacancy_url(1): FetchError("timeout"), vacancy_url(2): vacancy_page(2)})

    report = fetch_details(corpus, site, [1, 2])

    assert report.fetched == [2]
    assert report.failed == [(1, "timeout")]
    assert report.exit_code == EXIT_PARTIAL


@pytest.mark.parametrize("answer", [Blocked("403"), CAPTCHA])
def test_block_stops_fetching_and_lists_the_rest(corpus, answer):
    site = Site({vacancy_url(1): vacancy_page(1), vacancy_url(2): answer})

    report = fetch_details(corpus, site, [1, 2, 3])

    assert report.fetched == [1]
    assert report.not_reached == [2, 3]
    assert report.exit_code == EXIT_BLOCKED
    assert vacancy_url(3) not in site.calls


def test_role_tags_fetched_and_already_known_vacancies(corpus):
    fetch_details(corpus, Site({vacancy_url(1): vacancy_page(1)}), [1])

    fetch_details(corpus, Site({vacancy_url(2): vacancy_page(2)}), [1, 2], role="backend")

    assert [(h["role"], h["query"]) for h in corpus.hits(1)] == [("backend", "manual")]
    assert [(h["role"], h["query"]) for h in corpus.hits(2)] == [("backend", "manual")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_corpus_collect.py -v`
Expected: ModuleNotFoundError `hunter.corpus.collect`.

- [ ] **Step 3: Implement** — `hunter/corpus/collect.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..fetch import Blocked, FetchError, NotFound
from ..models import SearchQuery
from ..parse import ParseError
from ..sources import build_url
from ..text import plural
from .extract import VACANCY_URL, Captcha, card_row, parse_detail, search_cards
from .store import Corpus

FetchPage = Callable[[str], str]

ITEMS_PER_PAGE = 50  # совпадает с items_on_page в sources.build_url
MANUAL_QUERY = "manual"

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_BLOCKED = 3

_CARD_ERRORS = (KeyError, TypeError, ValueError, AttributeError)


def _no_wait() -> None:
    pass


@dataclass
class CollectReport:
    role: str
    cards: int = 0
    new: int = 0
    pages: int = 0
    error: str | None = None
    blocked: str | None = None

    @property
    def existing(self) -> int:
        return self.cards - self.new

    @property
    def exit_code(self) -> int:
        if self.blocked:
            return EXIT_BLOCKED
        return EXIT_PARTIAL if self.error else EXIT_OK

    def summary(self) -> str:
        lines = [
            f"{self.role}: {plural(self.cards, 'карточка', 'карточки', 'карточек')}, "
            f"новых {self.new}, уже были {self.existing} "
            f"(страниц выдачи: {self.pages})"
        ]
        if self.blocked:
            lines.append(f"ОСТАНОВЛЕНО — блокировка: {self.blocked}")
        if self.error:
            lines.append(f"ОШИБКА: {self.error}")
        return "\n".join(lines)


def collect(corpus: Corpus, fetch_page: FetchPage, query: SearchQuery, role: str,
            wait: Callable[[], None] = _no_wait) -> CollectReport:
    report = CollectReport(role)
    counted: set[int] = set()
    for page in range(query.max_pages):
        if page:
            wait()
        url = build_url("hh", query, page)
        try:
            cards = search_cards(fetch_page(url))
            # Сначала разбираем всю страницу, потом пишем: битая карточка
            # не оставит полстраницы в базе.
            rows = [card_row(raw) for raw in cards]
        except (Blocked, Captcha) as exc:
            report.blocked = str(exc)
            break
        except FetchError as exc:
            report.error = str(exc)
            break
        except ParseError as exc:
            report.error = f"{url}: {exc}"
            break
        except _CARD_ERRORS as exc:
            report.error = (
                f"{url}: карточка не разобралась "
                f"({exc.__class__.__name__}: {exc}) — сайт сменил формат"
            )
            break

        for row in rows:
            is_new = corpus.upsert_card(row)
            corpus.add_hit(row["id"], role, query.text)
            # Рекламная карточка повторяет обычную — в отчёте одна вакансия.
            if row["id"] not in counted:
                counted.add(row["id"])
                report.cards += 1
                report.new += is_new
        report.pages += 1
        if len(cards) < ITEMS_PER_PAGE:
            break
    return report


@dataclass
class FetchReport:
    fetched: list[int] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)
    deleted: list[int] = field(default_factory=list)
    failed: list[tuple[int, str]] = field(default_factory=list)
    not_reached: list[int] = field(default_factory=list)
    blocked: str | None = None

    @property
    def exit_code(self) -> int:
        if self.blocked:
            return EXIT_BLOCKED
        return EXIT_PARTIAL if self.failed else EXIT_OK

    def summary(self) -> str:
        lines = [
            f"скачано {len(self.fetched)}, уже были {len(self.skipped)}, "
            f"удалены на hh {len(self.deleted)}, ошибок {len(self.failed)}"
        ]
        lines += [f"  {vacancy_id}: {error}" for vacancy_id, error in self.failed]
        if self.blocked:
            lines.append(f"ОСТАНОВЛЕНО — блокировка: {self.blocked}")
            lines.append("не обработаны: " + " ".join(map(str, self.not_reached)))
        return "\n".join(lines)


def fetch_details(corpus: Corpus, fetch_page: FetchPage, ids: list[int], *,
                  role: str | None = None, refresh: bool = False,
                  wait: Callable[[], None] = _no_wait) -> FetchReport:
    report = FetchReport()
    requested = False
    for index, vacancy_id in enumerate(ids):
        if not refresh and corpus.has_detail(vacancy_id):
            report.skipped.append(vacancy_id)
            if role and corpus.exists(vacancy_id):
                corpus.add_hit(vacancy_id, role, MANUAL_QUERY)
            continue

        if requested:
            wait()
        requested = True
        try:
            detail = parse_detail(fetch_page(VACANCY_URL.format(id=vacancy_id)))
        except (Blocked, Captcha) as exc:
            report.blocked = str(exc)
            report.not_reached = list(ids[index:])
            break
        except NotFound:
            corpus.mark_deleted(vacancy_id)
            report.deleted.append(vacancy_id)
            continue
        except (FetchError, ParseError) as exc:
            report.failed.append((vacancy_id, str(exc)))
            continue

        corpus.save_detail(detail)
        if role:
            corpus.add_hit(detail.vacancy_id, role, MANUAL_QUERY)
        report.fetched.append(vacancy_id)
    return report
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_corpus_collect.py -v`
Expected: 16 passed.

- [ ] **Step 5: Commit**

```bash
git add hunter/corpus/collect.py tests/test_corpus_collect.py
git commit -m "feat(corpus): collect search results and fetch vacancy texts

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Вывод вакансии и таблицы

**Files:**
- Create: `hunter/corpus/show.py`
- Test: `tests/test_corpus_show.py`

**Interfaces:**
- Consumes: `Corpus` (Task 4) — строки `sqlite3.Row` из `vacancy()`, `hits()`, `verdict()`; `hunter.text.plural`.
- Produces:
  - `render_vacancy(vacancy: sqlite3.Row, hits: list[sqlite3.Row], verdict: sqlite3.Row | None) -> str`
  - `render_table(columns: list[str], rows: list[tuple], max_width: int = 60) -> str`

- [ ] **Step 1: Write the failing tests** — `tests/test_corpus_show.py`:

```python
import pytest

from hunter.corpus.extract import card_row, parse_detail
from hunter.corpus.show import render_table, render_vacancy
from hunter.corpus.store import Corpus
from tests.factories import search_card, vacancy_page


@pytest.fixture
def corpus(tmp_path):
    with Corpus.open(tmp_path / "v.db", create=True) as opened:
        yield opened


def rendered(corpus, vacancy_id):
    return render_vacancy(corpus.vacancy(vacancy_id), corpus.hits(vacancy_id), corpus.verdict(vacancy_id))


def test_full_vacancy(corpus):
    corpus.upsert_card(card_row(search_card(1, name="Python backend")))
    corpus.save_detail(parse_detail(vacancy_page(1)))
    corpus.add_hit(1, "backend", "python AND backend")

    text = rendered(corpus, 1)

    assert text.startswith("## Python backend — Компания")
    assert "https://hh.ru/vacancy/1" in text
    assert "от 300 000 RUR, на руки, выплаты MONTHLY" in text
    assert "формат: REMOTE" in text
    assert "Откликов: 5" in text
    assert "Роли: backend (python AND backend)" in text
    assert "Вердикт: нет" in text
    assert "Навыки: Python, SQL" in text
    assert text.endswith("Пишем на **Python**")
    assert "Сигналы" not in text


def test_quality_signals(corpus):
    raw = search_card(1, company={"id": 2, "visibleName": "Иван", "@trusted": False,
                                  "@category": "PRIVATE_INDIVIDUAL"},
                      vacancyProperties={"properties": [{"property": [{
                          "propertyType": "HH_ANTIFRAUD_SUSPICIOUS_VACANCY",
                          "parameters": [{"parameter": [{"key": "antifraudPessimizePower", "value": "0.8"}]}],
                      }]}]})
    corpus.upsert_card(card_row(raw))

    assert "Сигналы: работодатель не проверен hh; частное лицо; антифрод hh 0.80" in rendered(corpus, 1)


def test_without_text_hints_fetch(corpus):
    corpus.upsert_card(card_row(search_card(3, compensation={"noCompensation": {}})))

    text = rendered(corpus, 3)

    assert "Зарплата: не указана" in text
    assert "vacancies fetch 3" in text


def test_deleted_vacancy(corpus):
    corpus.upsert_card(card_row(search_card(3)))
    corpus.mark_deleted(3)

    text = rendered(corpus, 3)

    assert "удалена" in text
    assert "vacancies fetch" not in text


def test_verdict_with_note(corpus):
    corpus.upsert_card(card_row(search_card(1)))
    corpus.set_verdict(1, "maybe", "стек близко, домен скучный")

    assert "Вердикт: maybe — стек близко, домен скучный" in rendered(corpus, 1)


def test_table_truncates_and_escapes():
    table = render_table(["id", "text"], [(1, "а" * 100), (2, None), (3, "x|y\nz")], max_width=10)

    lines = table.split("\n")
    assert lines[0] == "| id | text |"
    assert lines[2] == "| 1 | ааааааааа… |"
    assert lines[3] == "| 2 |  |"
    assert lines[4] == "| 3 | x\\|y z |"
    assert lines[-1] == "(3 строки)"


def test_table_with_no_rows():
    assert render_table(["id"], []).endswith("(0 строк)")


def test_statement_without_result_columns():
    assert render_table([], []) == "(запрос ничего не вернул)"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_corpus_show.py -v`
Expected: ModuleNotFoundError `hunter.corpus.show`.

- [ ] **Step 3: Implement** — `hunter/corpus/show.py`:

```python
from __future__ import annotations

import json
from sqlite3 import Row

from ..text import plural


def _money(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def _salary(v: Row) -> str:
    if v["salary_from"] is None and v["salary_to"] is None:
        return "не указана"
    parts = []
    if v["salary_from"] is not None:
        parts.append(f"от {_money(v['salary_from'])}")
    if v["salary_to"] is not None:
        parts.append(f"до {_money(v['salary_to'])}")
    text = " ".join(parts)
    if v["currency"]:
        text += f" {v['currency']}"
    if v["gross"] is not None:
        text += ", до вычета налогов" if v["gross"] else ", на руки"
    if v["salary_frequency"]:
        text += f", выплаты {v['salary_frequency']}"
    return text


def _signals(v: Row) -> list[str]:
    signals = []
    if v["employer_trusted"] == 0:
        signals.append("работодатель не проверен hh")
    if v["employer_category"] == "PRIVATE_INDIVIDUAL":
        signals.append("частное лицо")
    if v["antifraud_power"] is not None:
        signals.append(f"антифрод hh {v['antifraud_power']:.2f}")
    return signals


def _body(v: Row) -> str:
    if v["description"]:
        return v["description"]
    if v["status"] == "deleted":
        return "_Вакансия удалена с hh, текста нет._"
    return f"_Полного текста нет — `vacancies fetch {v['id']}`._"


def render_vacancy(vacancy: Row, hits: list[Row], verdict: Row | None) -> str:
    v = vacancy
    meta = [f"id {v['id']}"]
    if v["area"]:
        meta.append(v["area"])
    if v["published_at"]:
        meta.append(f"опубликована {v['published_at'][:10]}")
    formats = ", ".join(json.loads(v["work_formats"] or "[]")) or "—"

    lines = [f"## {v['name']} — {v['company']}", "", v["url"], ""]
    lines.append("- " + " · ".join(meta))
    lines.append(f"- Зарплата: {_salary(v)}")
    lines.append(
        f"- Опыт: {v['experience'] or '—'} · формат: {formats} · "
        f"занятость: {v['employment_form'] or '—'}"
    )
    if v["responses_count"] is not None:
        lines.append(f"- Откликов: {v['responses_count']}")
    signals = _signals(v)
    if signals:
        lines.append("- Сигналы: " + "; ".join(signals))
    if hits:
        lines.append("- Роли: " + "; ".join(f"{h['role']} ({h['query']})" for h in hits))
    if verdict:
        note = f" — {verdict['note']}" if verdict["note"] else ""
        lines.append(f"- Вердикт: {verdict['verdict']}{note}")
    else:
        lines.append("- Вердикт: нет")
    if v["status"]:
        lines.append(f"- Статус на hh: {v['status']}")
    skills = json.loads(v["key_skills"] or "[]")
    if skills:
        lines.append("- Навыки: " + ", ".join(skills))
    lines += ["", _body(v)]
    return "\n".join(lines)


def _cell(value, max_width: int) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\n", " ").replace("|", "\\|")
    return text if len(text) <= max_width else text[: max_width - 1] + "…"


def render_table(columns: list[str], rows: list[tuple], max_width: int = 60) -> str:
    if not columns:
        return "(запрос ничего не вернул)"
    lines = [
        "| " + " | ".join(columns) + " |",
        "|" + "|".join("---" for _ in columns) + "|",
    ]
    lines += ["| " + " | ".join(_cell(value, max_width) for value in row) + " |" for row in rows]
    lines.append(f"({plural(len(rows), 'строка', 'строки', 'строк')})")
    return "\n".join(lines)
```

Примечание к тесту на усечение: `max_width=10`, ячейка из 100 «а» → 9 «а» + «…» (10 символов). Строка `"x|y\nz"` → `x\|y z` (6 символов, не усекается).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_corpus_show.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add hunter/corpus/show.py tests/test_corpus_show.py
git commit -m "feat(corpus): render vacancies and SQL results as Markdown

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: CLI `vacancies` и установка

**Files:**
- Create: `hunter/corpus/cli.py`
- Modify: `pyproject.toml`, `uv.lock` (перегенерируется)
- Test: `tests/test_corpus_cli.py`

**Interfaces:**
- Consumes: всё из Tasks 3–6; `hunter.fetch.get_html`, `new_client`; `hunter.models.SearchQuery`.
- Produces:
  - `main(argv: list[str] | None = None, *, fetch_page: FetchPage | None = None, sleep=time.sleep) -> int`
  - `build_parser() -> argparse.ArgumentParser` (подкоманды: `init`, `collect`, `fetch`, `mark`, `show`, `sql`)
  - `parse_vacancy_ref(value: str) -> int` — `ValueError` на мусоре
  - `GUIDE_PATH: Path` — `hunter/corpus/GUIDE.md` (сам файл — Task 8)
  - консольная команда `vacancies`

- [ ] **Step 1: Write the failing tests** — `tests/test_corpus_cli.py`:

```python
import json

import pytest

from hunter.corpus.cli import main, parse_vacancy_ref
from hunter.fetch import Blocked
from tests.factories import vacancy_page


@pytest.mark.parametrize("ref", [
    "123", " 123 ",
    "https://hh.ru/vacancy/123",
    "https://tomsk.hh.ru/vacancy/123?query=python&hhtmFrom=vacancy_search_list",
    "https://m.hh.ru/vacancy/123",
    "http://hh.ru/vacancy/123/",
    "https://www.zarplata.ru/vacancy/123",
    "https://tomsk.zarplata.ru/vacancy/123",
])
def test_vacancy_refs(ref):
    assert parse_vacancy_ref(ref) == 123


@pytest.mark.parametrize("ref", [
    "abc", "", "https://evil.com/vacancy/1", "https://nothh.ru/vacancy/1",
    "https://hh.ru/employer/1", "https://hh.ru/vacancy/abc", "ftp://hh.ru/vacancy/1",
])
def test_rejected_refs(ref):
    with pytest.raises(ValueError):
        parse_vacancy_ref(ref)


class Site:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        item = self.pages[url]
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def run(*argv, site=None):
    return main(list(argv), fetch_page=site, sleep=lambda _: None)


def test_init_creates_database_and_claude_md(workdir, capsys):
    assert run("init") == 0

    assert (workdir / "vacancies.db").exists()
    claude_md = (workdir / "CLAUDE.md").read_text(encoding="utf-8")
    first_line = claude_md.splitlines()[0]
    assert first_line.startswith("@") and first_line.endswith("hunter/corpus/GUIDE.md")


def test_init_never_overwrites(workdir):
    (workdir / "CLAUDE.md").write_text("моё", encoding="utf-8")
    run("init")
    run("init")

    assert (workdir / "CLAUDE.md").read_text(encoding="utf-8") == "моё"


@pytest.mark.parametrize("argv", [
    ("show", "1"), ("mark", "1", "yes"), ("sql", "SELECT 1"), ("fetch", "1"),
    ("collect", "--role", "r", "--text", "t"),
])
def test_commands_outside_job_folder_fail_clearly(workdir, capsys, argv):
    assert run(*argv, site=Site({})) == 2

    assert "vacancies init" in capsys.readouterr().err
    assert not (workdir / "vacancies.db").exists()


def test_fetch_show_mark_sql_round_trip(workdir, capsys):
    run("init")
    site = Site({"https://hh.ru/vacancy/42": vacancy_page(42)})

    assert run("fetch", "https://tomsk.hh.ru/vacancy/42?from=x", "--role", "backend", "--delay", "0", site=site) == 0
    assert run("mark", "42", "yes", "нравится стек") == 0
    capsys.readouterr()

    assert run("show", "42") == 0
    shown = capsys.readouterr().out
    assert "Пишем на **Python**" in shown
    assert "Вердикт: yes — нравится стек" in shown
    assert "Роли: backend (manual)" in shown

    assert run("sql", "SELECT vacancy_id, verdict FROM verdicts") == 0
    assert "| 42 | yes |" in capsys.readouterr().out

    assert run("sql", "SELECT id, name FROM vacancies", "--json") == 0
    assert json.loads(capsys.readouterr().out) == [{"id": 42, "name": "Вакансия 42"}]


def test_sql_cannot_write(workdir, capsys):
    run("init")
    run("fetch", "42", "--delay", "0", site=Site({"https://hh.ru/vacancy/42": vacancy_page(42)}))

    assert run("sql", "DELETE FROM vacancies") == 1
    assert "Ошибка SQL" in capsys.readouterr().err
    run("sql", "SELECT count(*) AS n FROM vacancies")
    assert "| 1 |" in capsys.readouterr().out


def test_show_unknown_vacancy(workdir, capsys):
    run("init")

    assert run("show", "7") == 1
    assert "vacancies fetch 7" in capsys.readouterr().err


def test_mark_unknown_vacancy(workdir, capsys):
    run("init")

    assert run("mark", "7", "no") == 2
    assert "vacancies fetch 7" in capsys.readouterr().err


def test_bad_ref_is_rejected_before_any_request(workdir, capsys):
    run("init")
    site = Site({})

    assert run("fetch", "42", "https://example.com/x", site=site) == 2
    assert site.calls == []


def test_collect_page_limit(workdir, capsys):
    run("init")

    assert run("collect", "--role", "r", "--text", "t", "--pages", "0") == 2
    assert run("collect", "--role", "r", "--text", "t", "--pages", "41") == 2


def test_collect_block_exits_3(workdir, capsys):
    run("init")

    class Blocking:
        def __call__(self, url):
            raise Blocked("403")

    assert run("collect", "--role", "r", "--text", "t", "--delay", "0", site=Blocking()) == 3
    assert "блокировка" in capsys.readouterr().out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_corpus_cli.py -v`
Expected: ModuleNotFoundError `hunter.corpus.cli`.

- [ ] **Step 3: Implement** — `hunter/corpus/cli.py`:

```python
from __future__ import annotations

import argparse
import json
import logging
import random
import re
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from ..fetch import get_html, new_client
from ..models import SearchQuery
from .collect import FetchPage, collect, fetch_details
from .show import render_table, render_vacancy
from .store import VERDICTS, Corpus, CorpusError

EXIT_USAGE = 2
GUIDE_PATH = Path(__file__).with_name("GUIDE.md")

EXPERIENCE = ("noExperience", "between1And3", "between3And6", "moreThan6")
WORK_FORMATS = ("remote", "hybrid", "on_site", "field_work")
MAX_PAGES = 40  # hh не отдаёт дальше 2000 вакансий: 40 страниц по 50

_SITES = ("hh.ru", "zarplata.ru")
_VACANCY_PATH = re.compile(r"^/vacancy/(\d+)/?$")

CLAUDE_TEMPLATE = """{import_line}

## Обо мне и договорённости

"""


def parse_vacancy_ref(value: str) -> int:
    """id или ссылка на вакансию hh/zarplata любого поддомена."""
    value = value.strip()
    if value.isdigit():
        return int(value)
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    on_site = any(host == site or host.endswith("." + site) for site in _SITES)
    match = _VACANCY_PATH.match(parsed.path)
    if parsed.scheme in ("http", "https") and on_site and match:
        return int(match.group(1))
    raise ValueError(f"Не похоже на вакансию hh или zarplata: {value!r}")


def _parse_refs(refs: list[str]) -> list[int]:
    # Порядок сохраняем, повторы убираем: Claude может передать один id дважды.
    return list(dict.fromkeys(parse_vacancy_ref(ref) for ref in refs))


def _make_wait(delay: float, sleep):
    def wait() -> None:
        if delay > 0:
            sleep(delay * random.uniform(0.5, 1.5))
    return wait


@contextmanager
def _page_fetcher(fetch_page: FetchPage | None):
    if fetch_page is not None:
        yield fetch_page
        return
    client = new_client()
    try:
        yield lambda url: get_html(url, client=client)
    finally:
        client.close()


def _import_line() -> str:
    guide = GUIDE_PATH.resolve()
    try:
        return "@~/" + guide.relative_to(Path.home()).as_posix()
    except ValueError:
        return "@" + guide.as_posix()


def _cmd_init(args, fetch_page, sleep) -> int:
    existed = args.db.exists()
    with Corpus.open(args.db, create=True):
        pass
    print(f"База {args.db}: {'уже была, схема проверена' if existed else 'создана'}")

    import_line = _import_line()
    claude_md = Path.cwd() / "CLAUDE.md"
    if claude_md.exists():
        print(f"{claude_md} уже есть — не трогаю. В нём должна быть строка:\n{import_line}")
    else:
        claude_md.write_text(CLAUDE_TEMPLATE.format(import_line=import_line), encoding="utf-8")
        print(f"Создан {claude_md}")
    return 0


def _cmd_collect(args, fetch_page, sleep) -> int:
    if not 1 <= args.pages <= MAX_PAGES:
        print(f"--pages: от 1 до {MAX_PAGES} — дальше hh выдачу не отдаёт", file=sys.stderr)
        return EXIT_USAGE
    query = SearchQuery(
        name=args.role,
        text=args.text,
        area=args.area,
        salary=args.salary,
        experience=tuple(args.experience),
        work_format=args.work_format,
        only_with_salary=args.with_salary,
        search_period=args.days,
        max_pages=args.pages,
    )
    with Corpus.open(args.db) as corpus, _page_fetcher(fetch_page) as fetch:
        report = collect(corpus, fetch, query, args.role, wait=_make_wait(args.delay, sleep))
    print(report.summary())
    return report.exit_code


def _cmd_fetch(args, fetch_page, sleep) -> int:
    try:
        ids = _parse_refs(args.refs)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return EXIT_USAGE
    with Corpus.open(args.db) as corpus, _page_fetcher(fetch_page) as fetch:
        report = fetch_details(
            corpus, fetch, ids,
            role=args.role, refresh=args.refresh, wait=_make_wait(args.delay, sleep),
        )
    print(report.summary())
    return report.exit_code


def _cmd_mark(args, fetch_page, sleep) -> int:
    try:
        vacancy_id = parse_vacancy_ref(args.ref)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return EXIT_USAGE
    with Corpus.open(args.db) as corpus:
        corpus.set_verdict(vacancy_id, args.verdict, args.note)
    print(f"{vacancy_id}: {args.verdict}" + (f" — {args.note}" if args.note else ""))
    return 0


def _cmd_show(args, fetch_page, sleep) -> int:
    try:
        ids = _parse_refs(args.refs)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return EXIT_USAGE
    blocks, missing = [], []
    with Corpus.open(args.db) as corpus:
        for vacancy_id in ids:
            vacancy = corpus.vacancy(vacancy_id)
            if vacancy is None:
                missing.append(vacancy_id)
                continue
            blocks.append(render_vacancy(vacancy, corpus.hits(vacancy_id), corpus.verdict(vacancy_id)))
    if blocks:
        print("\n\n---\n\n".join(blocks))
    for vacancy_id in missing:
        print(f"Вакансии {vacancy_id} нет в базе — vacancies fetch {vacancy_id}", file=sys.stderr)
    return 1 if missing else 0


def _cmd_sql(args, fetch_page, sleep) -> int:
    with Corpus.open_readonly(args.db) as corpus:
        try:
            columns, rows = corpus.query(args.query)
        except sqlite3.Error as exc:
            print(f"Ошибка SQL: {exc}", file=sys.stderr)
            return 1
    if args.json:
        print(json.dumps([dict(zip(columns, row)) for row in rows], ensure_ascii=False, indent=1))
    else:
        print(render_table(columns, rows))
    return 0


_COMMANDS = {
    "init": _cmd_init,
    "collect": _cmd_collect,
    "fetch": _cmd_fetch,
    "mark": _cmd_mark,
    "show": _cmd_show,
    "sql": _cmd_sql,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vacancies",
        description="Корпус вакансий hh для анализа в сессии Claude Code",
    )
    parser.add_argument("--db", type=Path, default=Path("vacancies.db"),
                        help="файл базы (по умолчанию ./vacancies.db)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="создать vacancies.db и заготовку CLAUDE.md в текущей папке")

    p = sub.add_parser("collect", help="собрать карточки из выдачи hh под роль")
    p.add_argument("--role", required=True, help="метка роли, например backend")
    p.add_argument("--text", required=True, help="поисковый запрос hh (AND, OR, NOT, NAME:)")
    p.add_argument("--area", type=int, default=113, help="113 — Россия, 1 — Москва, 2 — СПб")
    p.add_argument("--days", type=int, choices=(1, 3, 7, 30), default=30)
    p.add_argument("--pages", type=int, default=5, help=f"страниц по 50, от 1 до {MAX_PAGES}")
    p.add_argument("--salary", type=int)
    p.add_argument("--experience", action="append", choices=EXPERIENCE, default=[])
    p.add_argument("--work-format", choices=WORK_FORMATS)
    p.add_argument("--with-salary", action="store_true", help="только с указанной зарплатой")
    p.add_argument("--delay", type=float, default=2.0, help="пауза между запросами, с")

    p = sub.add_parser("fetch", help="скачать полный текст вакансий по id или ссылкам")
    p.add_argument("refs", nargs="+", metavar="ID|URL")
    p.add_argument("--role", help="пометить вакансии ролью (query = manual)")
    p.add_argument("--refresh", action="store_true", help="перекачать уже скачанные")
    p.add_argument("--delay", type=float, default=2.0, help="пауза между запросами, с")

    p = sub.add_parser("mark", help="поставить вердикт вакансии")
    p.add_argument("ref", metavar="ID|URL")
    p.add_argument("verdict", choices=VERDICTS)
    p.add_argument("note", nargs="?", help="комментарий")

    p = sub.add_parser("show", help="показать вакансии как Markdown")
    p.add_argument("refs", nargs="+", metavar="ID|URL")

    p = sub.add_parser("sql", help="запрос к базе только на чтение")
    p.add_argument("query")
    p.add_argument("--json", action="store_true", help="полный вывод списком объектов")
    return parser


def main(argv: list[str] | None = None, *, fetch_page: FetchPage | None = None,
         sleep=time.sleep) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    try:
        return _COMMANDS[args.command](args, fetch_page, sleep)
    except CorpusError as exc:
        print(exc, file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_corpus_cli.py -v`
Expected: все прошли. Если `test_commands_outside_job_folder_fail_clearly[collect…]` падает из-за проверки `--pages` — не должен: `--pages 5` по умолчанию валиден, ошибка придёт из `Corpus.open`.

- [ ] **Step 5: Packaging** — в `pyproject.toml` добавить (без изменения существующих секций):

```toml
[project.scripts]
vacancies = "hunter.corpus.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["hunter"]
```

Run: `uv lock`
Expected: `uv.lock` обновлён (проект стал собираемым пакетом), без новых runtime-зависимостей.

Run: `.venv/bin/python -m pytest`
Expected: весь набор зелёный.

- [ ] **Step 6: Commit**

```bash
git add hunter/corpus/cli.py tests/test_corpus_cli.py pyproject.toml uv.lock
git commit -m "feat(corpus): vacancies CLI with init, collect, fetch, mark, show, sql

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: GUIDE.md, проверка синхронизации, установка и бэклог

**Files:**
- Create: `hunter/corpus/GUIDE.md`
- Modify: `docs/backlog.md`, `README.md`
- Test: `tests/test_corpus_guide.py`

**Interfaces:**
- Consumes: `build_parser()`, `GUIDE_PATH` (Task 7); `Corpus` (Task 4).

- [ ] **Step 1: Write the failing test** — `tests/test_corpus_guide.py`:

```python
import argparse
import sqlite3

from hunter.corpus.cli import GUIDE_PATH, build_parser
from hunter.corpus.store import Corpus


def guide():
    return GUIDE_PATH.read_text(encoding="utf-8")


def test_every_subcommand_is_documented():
    sub = next(a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    missing = [name for name in sub.choices if f"vacancies {name}" not in guide()]
    assert not missing, f"в GUIDE.md не описаны подкоманды: {missing}"


def test_every_column_is_documented(tmp_path):
    Corpus.open(tmp_path / "v.db", create=True).close()
    conn = sqlite3.connect(tmp_path / "v.db")
    text = guide()
    missing = [
        f"{table}.{column}"
        for table in ("vacancies", "hits", "verdicts")
        for (_, column, *_rest) in conn.execute(f"PRAGMA table_info({table})")
        if f"`{column}`" not in text
    ]
    assert not missing, f"в GUIDE.md не описаны колонки: {missing}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_corpus_guide.py -v`
Expected: FileNotFoundError — `GUIDE.md` нет.

- [ ] **Step 3: Write `hunter/corpus/GUIDE.md`**:

````markdown
# Корпус вакансий: как с ним работать

Эта папка — рабочее место поиска работы. Вакансии лежат в `vacancies.db`
(SQLite), доступ — командой `vacancies` (из репозитория vacancy-hunter,
установлена через `uv tool install --editable`). Все команды выполняются
из этой папки; другая база — глобальный ключ до подкоманды:
`vacancies --db путь.db show 123`.

## Цикл работы

1. **Профиль.** Владелец рассказывает о себе → `profile.md` (опыт, сильные
   стороны, чего хочет и чего точно нет, деньги, формат, город).
2. **Примеры.** Владелец присылает ссылки: 5–10 «нравится» и 3–5 «похоже,
   но нет». Для каждой: `vacancies fetch <ссылка>`, затем
   `vacancies mark <id> yes|no "почему"` — со слов владельца.
3. **Портрет** → `portrait.md`: роли; признаки «да» и «нет» по каждой;
   команды `vacancies collect …` под каждую роль (чтобы сбор был
   повторяемым — записывай их туда дословно).
4. **Сбор.** Выполнить команды `collect` из портрета.
5. **Отбор.** Отсечь по метаданным через `vacancies sql`, докачать полный
   текст выборки (`vacancies fetch`), прочитать (`vacancies show`),
   предложить владельцу кандидатов с обоснованием. Вердикты — после его
   ответа. Уточнить портрет. Повторять, пока выборки не станут точными.
6. **Результат:** `resumes/<роль>.md` под каждую роль и `strategy.md`.

## Правила

- **Вердикты ставит владелец.** Ты предлагаешь «подходит / нет, потому что…»,
  `vacancies mark` вызываешь только после его явного ответа.
- **Полный текст — выборками, не всё подряд.** Не больше ~40 вакансий за
  один `fetch`. Сначала отсекай по метаданным.
- **Код выхода 3 — блокировка или капча hh.** Остановись, сообщи владельцу,
  не повторяй. Повтор только по его команде и не раньше чем через час.
- Код 1 — частичная неудача (в выводе список); код 2 — неверные аргументы
  или нет базы (`vacancies init`).
- В базу пишут только `collect`, `fetch`, `mark`. `vacancies sql` открывает
  базу только на чтение.

## Команды

### `vacancies init`
Создаёт `vacancies.db` и заготовку `CLAUDE.md` в текущей папке. Существующие
файлы не перезаписывает; у старой базы обновляет схему.

### `vacancies collect --role R --text T [опции]`
Собирает карточки из выдачи hh (без полного текста) и помечает их ролью.

```
vacancies collect --role backend --text "python AND (backend OR бэкенд) NOT стажёр" \
  --area 113 --days 30 --pages 5 --work-format remote --experience between3And6
```

- `--text` — язык запросов hh: `AND`, `OR`, `NOT`, кавычки, `NAME:` (поиск по названию).
- `--area` 113 — Россия (по умолчанию), 1 — Москва, 2 — СПб.
- `--days` 1 | 3 | 7 | 30 (по умолчанию 30). `--pages` 1–40 по 50 карточек (по умолчанию 5).
- `--salary N`, `--with-salary`, `--experience` (повторяемый), `--work-format remote|hybrid|on_site|field_work`.
- `--delay` — пауза между запросами, с (по умолчанию 2).

Повторный сбор обновляет метаданные и число откликов, полный текст и
вердикты не трогает.

### `vacancies fetch ID|URL … [--role R] [--refresh]`
Скачивает полный текст. Принимает id и ссылки hh/zarplata (любой
поддомен). Вакансию, которой нет в базе, добавляет. Уже скачанные
пропускает без `--refresh`. `--role` помечает вакансии ролью с
`query = 'manual'`.

### `vacancies mark ID yes|no|maybe ["комментарий"]`
Вердикт владельца; повторный вызов перезаписывает.

### `vacancies show ID …`
Вакансия как Markdown: метаданные, сигналы, роли, вердикт, навыки, описание.

### `vacancies sql "SELECT …" [--json]`
Запрос только на чтение. Таблица с усечением длинных ячеек; `--json` —
полный вывод.

## Схема

### `vacancies` — одна строка на вакансию
| Колонка | Смысл |
|---|---|
| `id` | id вакансии hh (у zarplata тот же — это кросспостинг hh) |
| `name`, `company`, `company_id` | название, работодатель |
| `url` | всегда `https://hh.ru/vacancy/<id>` |
| `area` | город |
| `published_at` | дата публикации, ISO 8601 |
| `salary_from`, `salary_to`, `currency` | вилка; NULL — не указана |
| `gross` | 1 — до вычета налогов, 0 — на руки, NULL — неизвестно |
| `salary_frequency` | частота выплат: `MONTHLY`, `TWICE_PER_MONTH`, `PER_PROJECT`, … |
| `experience` | `noExperience`, `between1And3`, `between3And6`, `moreThan6` |
| `work_formats` | JSON-массив: `REMOTE`, `HYBRID`, `ON_SITE`, `FIELD_WORK` |
| `employment_form` | `FULL`, `PART`, `PROJECT`, … |
| `employer_trusted` | 1 — работодатель проверен hh, 0 — нет |
| `employer_category` | `COMPANY`, `PRIVATE_INDIVIDUAL` (частное лицо), `AGENCY`, … |
| `antifraud_power` | hh пометил вакансию подозрительной: сила пессимизации 0–1; NULL — не помечена или добавлена по ссылке (страница вакансии этого не показывает) |
| `responses_count` | откликов на момент **последнего сбора** — снимок, не живое число |
| `description` | полный текст в Markdown; NULL — не скачан |
| `key_skills` | JSON-массив навыков от работодателя |
| `status` | `active`, `archived`, `deleted` (удалена с hh); NULL — текст не качали |
| `detail_fetched_at` | когда скачан полный текст |
| `card_json` | сырая карточка выдачи; NULL у добавленных по ссылке |
| `detail_json` | сырые данные страницы вакансии |
| `first_seen_at`, `last_seen_at` | первый и последний сбор, ISO 8601 UTC |

### `hits` — по какому запросу и роли вакансия нашлась
`vacancy_id`, `role`, `query` (текст запроса или `manual`), `first_found_at`.
Одна вакансия может найтись под несколькими ролями.

### `verdicts` — оценка владельца
`vacancy_id`, `verdict` (`yes` | `no` | `maybe`), `note`, `updated_at`.

## Полезные запросы

Кандидаты без вердикта, с полным текстом:
```sql
SELECT v.id, v.name, v.company, v.salary_from, v.salary_to
FROM vacancies v LEFT JOIN verdicts d ON d.vacancy_id = v.id
WHERE d.vacancy_id IS NULL AND v.description IS NOT NULL;
```

Кому докачать текст в роли (отсев по метаданным):
```sql
SELECT v.id FROM vacancies v JOIN hits h ON h.vacancy_id = v.id
WHERE h.role = 'backend' AND v.description IS NULL
  AND v.antifraud_power IS NULL AND v.employer_category = 'COMPANY'
  AND (v.salary_to IS NULL OR v.salary_to >= 250000)
ORDER BY v.published_at DESC LIMIT 40;
```

Частота навыков по роли:
```sql
SELECT s.value AS skill, count(*) AS n
FROM vacancies v JOIN hits h ON h.vacancy_id = v.id, json_each(v.key_skills) s
WHERE h.role = 'backend'
GROUP BY s.value ORDER BY n DESC LIMIT 30;
```

Вилки по ролям (только рубли):
```sql
SELECT h.role, count(*) AS n, min(v.salary_from), max(v.salary_to), avg(v.salary_to)
FROM vacancies v JOIN hits h ON h.vacancy_id = v.id
WHERE v.currency = 'RUR' GROUP BY h.role;
```

Вакансии на стыке ролей:
```sql
SELECT vacancy_id, group_concat(DISTINCT role) AS roles
FROM hits GROUP BY vacancy_id HAVING count(DISTINCT role) > 1;
```

Поле, которого нет в колонках, — из сырой карточки:
```sql
SELECT id, json_extract(card_json, '$.employmentForm') FROM vacancies LIMIT 5;
```

## Файлы папки

- `profile.md` — о владельце.
- `portrait.md` — роли, признаки «да»/«нет», команды `collect`.
- `resumes/<роль>.md` — резюме под роль.
- `strategy.md` — стратегия поиска.
````

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest`
Expected: весь набор зелёный, включая 2 теста гайда.

- [ ] **Step 5: Установка и живая проверка** (сеть, несколько запросов к hh):

```bash
uv tool install --editable .
SMOKE=$(mktemp -d) && cd "$SMOKE"
vacancies init && head -1 CLAUDE.md
vacancies collect --role smoke --text "python" --area 1 --days 1 --pages 1
ID=$(vacancies sql "SELECT id FROM vacancies LIMIT 1" --json | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['id'])")
vacancies fetch "$ID" && vacancies show "$ID" | head -20
vacancies mark "$ID" maybe "smoke" && vacancies sql "SELECT * FROM verdicts"
cd - && rm -rf "$SMOKE"
```

Expected: `CLAUDE.md` начинается с `@~/projects/personal/vacancy-hunter/hunter/corpus/GUIDE.md`; `collect` — до 50 карточек, код 0; `show` печатает описание в Markdown без тегов; вердикт виден в `sql`.

- [ ] **Step 6: Бэклог и README.** В `docs/backlog.md` перед разделом «Что уже рассмотрено…» добавить:

```markdown
## 3. zarplata отдаёт одну карточку на запрос

**Что сейчас.** Выдача zarplata.ru для анонимного клиента вживую
(2026-10-02) содержит 1 вакансию там, где hh по тому же запросу отдаёт 50.
Фикстура `zarplata_search_python.html` тоже с одной карточкой.

**Почему важно.** Источник `zarplata` в боте почти ничего не добавляет,
но тратит запросы. При этом id вакансий zarplata совпадают с hh
(кросспостинг), так что дубли и так схлопываются.

**Что можно сделать.** Выяснить, отдаёт ли zarplata больше при другом
`items_on_page`/пагинации; если нет — убрать zarplata из `sources`
по умолчанию.

**Где менять:** `hunter/sources.py`, `config.example.yaml`.

---
```

В `README.md` добавить раздел (в конец):

```markdown
## Корпус вакансий для анализа в Claude Code

`vacancies` — отдельная команда для сбора вакансий в SQLite и разбора их
в сессии Claude Code (подбор ролей, резюме, стратегия поиска).

    uv tool install --editable ~/projects/personal/vacancy-hunter
    mkdir ~/job-search && cd ~/job-search && vacancies init

Дальше откройте Claude Code в этой папке: `CLAUDE.md` подключает
`hunter/corpus/GUIDE.md` с командами, схемой и порядком работы.
```

- [ ] **Step 7: Commit**

```bash
git add hunter/corpus/GUIDE.md tests/test_corpus_guide.py docs/backlog.md README.md
git commit -m "docs(corpus): session guide kept in sync with CLI and schema

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
