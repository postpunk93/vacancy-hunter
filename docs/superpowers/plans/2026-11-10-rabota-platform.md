# rabota.ru — третья площадка: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** добавить rabota.ru третьей площадкой — сбор карточек из выдачи и полный текст со страницы вакансии, без пересечения с hh.

**Architecture:** Выдача и страница вакансии rabota.ru — Nuxt 2, всё содержимое лежит в `window.__NUXT__` в function-режиме. Разбор payload выносится в отдельный модуль `hunter/platforms/nuxt.py`, площадка `hunter/platforms/rabota.py` читает из состояния карточки и вакансию — как superjob из `APP_STATE`. Попутно закрываются две дыры, без которых площадка ведёт себя хуже обещанного: `run.py` начинает ловить `Captcha`, `Corpus.update_detail` пишет форматы работы со страницы.

**Tech Stack:** Python 3.13, stdlib (urllib, json), httpx через существующий `hunter/fetch.py`, pytest, uv.

**Spec:** `docs/superpowers/specs/2026-11-10-rabota-platform-design.md`

## Global Constraints

- Площадка не пишет в базу сама: только возвращает `Card`/`Detail`. Запись — дело `Corpus`.
- В тестах нет сети: только фикстуры и синтетические payload.
- Кириллица в URL — всегда через `urlencode`; сырые байты rabota отвечает `400`.
- hh-синтаксис (`NAME:`, `AND`, `NOT`, кавычки) rabota не понимает: для неё только обычные слова.
- `name` площадки — `"rabota"`, он же ключ в `PLATFORMS` и префикс в `format_ref`.
- Схема базы, дедуп, оценка, дайджест и zarplata не меняются.
- `is_ad` у rabota всегда `False`: `is_promoted` — платное продвижение, не реклама.
- Фолбэк на `state.rabota.vacancy.vacancyCache` **не делаем**: на живой странице узел равен `null`, структура не проверена. Нет `vacancy.vacancy` → `ParseError`.
- Все сообщения об ошибках — по-русски, в стиле соседних модулей.

## Review Focus

1. В тексте вакансии встречается слово `return` (и скобки) — разбор payload обязан взять последний `return`, после которого тело закрывается, а не первый попавшийся. Тест — Task 1.
2. Капча приходит с кодом 200 во время ежедневного прохода — проход обязан закончиться кодом 3 с предупреждением, а не кодом 4 «непредвиденная ошибка». Тест — Task 4.
3. Незнакомый период оплаты в подписи `salary.currency` (например `руб./смена`) — `salary_mode` остаётся `None`, почасовую ставку не показываем месячным окладом. Тест — Task 2.
4. Пустая выдача (`totalVacancies == 0`, `ld+json` нет вовсе) — это `[]`, а не `ParseError`. Тест — Task 2.
5. Страница вакансии не сообщила формат работы, а у вакансии в базе он уже есть — `update_detail` не затирает известное пустым списком. Тест — Task 5.

---

### Task 1: Разбор payload `window.__NUXT__`

**Files:**
- Create: `hunter/platforms/nuxt.py`
- Test: `tests/test_platforms_nuxt.py`

**Interfaces:**
- Produces: `parse_nuxt(page_html: str, marker: str = "window.__NUXT__=") -> dict`; бросает `hunter.parse.ParseError`, если маркера нет, заголовок вызова не разобрался или ни один `return` не доходит до конца тела. Модуль ничего не знает про капчу и площадки.

Рабочий прототип разбора, проверенный на живых страницах: `/tmp/opencode/nuxt_probe.py` (функция `state`). Логику берём оттуда, оформление — под проект: класс-парсер с позицией, `ParseError` вместо `ValueError`/`assert`.

- [x] **Step 1: Написать падающие тесты**

```python
import pytest

from hunter.parse import ParseError
from hunter.platforms.nuxt import parse_nuxt


def page(body: str, args: str = "{}", params: str = "a") -> str:
    """Страница Nuxt 2 в function-режиме, как её отдаёт rabota.ru."""
    return (
        "<html><body><script>window.__NUXT__="
        f"(function({params}){{{body}}}({args}));"
        "</script></body></html>"
    )


def test_arguments_are_substituted_for_parameters_by_position():
    html = page(
        "return {first:first,second:second,third:third,"
        "yes:!0,no:!1,none:void 0,negative:-1.5,real:2.25};",
        params="first,second,third",
        args='{"x":1},[2,3],"строка"',
    )
    assert parse_nuxt(html) == {
        "first": {"x": 1},
        "second": [2, 3],
        "third": "строка",
        "yes": True,
        "no": False,
        "none": None,
        "negative": -1.5,
        "real": 2.25,
    }


def test_return_inside_a_string_does_not_end_the_body():
    html = page(r'return {t:"напишите return {x:1} в отзыве",k:"после"};')
    assert parse_nuxt(html) == {"t": "напишите return {x:1} в отзыве", "k": "после"}


def test_only_the_return_that_closes_the_body_is_taken():
    html = page("if(a){return {early:!0}}return {late:!0};")
    assert parse_nuxt(html) == {"late": True}


def test_unicode_escapes_in_strings_are_decoded():
    # r-строка: до парсера доходит буквально \u0440, а не буква «р».
    html = page(r'return {t:"\u0440\u0435\u0434\u0430\u043a\u0442\u043e\u0440"};')
    assert parse_nuxt(html) == {"t": "редактор"}


def test_keys_may_be_unquoted_and_strings_single_quoted():
    html = page("return {price:'100','quoted-key':\"v\"};")
    assert parse_nuxt(html) == {"price": "100", "quoted-key": "v"}


def test_page_without_payload_raises_parse_error():
    with pytest.raises(ParseError, match="window.__NUXT__"):
        parse_nuxt("<html>капча</html>")


def test_broken_payload_reports_position():
    with pytest.raises(ParseError, match="позици"):
        parse_nuxt(page("return {a: };"))
```

- [x] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_platforms_nuxt.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hunter.platforms.nuxt'`

- [x] **Step 3: Реализовать `parse_nuxt` в `hunter/platforms/nuxt.py`**

Шаги разбора:

1. `page_html.find(marker)`; нет — `ParseError(f"в странице нет {marker}")`.
2. Отрезать по ближайшему `</script>` после маркера.
3. Найти `(function(`; параметры — до `){`; тело — после `){`.
4. Конец тела и начало аргументов: последнее вхождение `}(` (rfind); тело без завершающей `}`; аргументы — до финальных `));`.
5. Аргументы разобрать тем же парсером значений через запятую, связать с параметрами по позиции.
6. В теле перебрать `body.rfind("return ")` в цикле от конца: на каждом — парсер с позиции после `return`, `value()`, затем пропустить пробелы и `}`; если позиция дошла до конца тела — вернуть значение. Не дошло — пробовать предыдущее вхождение. Ни одно — `ParseError(f"{marker} не разобрался: ни один return не закрывает тело")`.
7. Парсер значений — рекурсивный спуск: объекты с неквотированными ключами, массивы, строки в `'`/`"` с `\uXXXX` и `\n\t\r\b\f`, числа (int/float, отрицательные), `true`/`false`/`null`/`void 0`/`!0`/`!1`, одиночный идентификатор → значение параметра (`None`, если параметра нет). Пробелы и переводы строк между элементами — пропускать.
8. Любой непонятный символ — `ParseError` с позицией и куском текста: `f"{marker} не разобрался на позиции {pos}: {snippet!r}"`.

- [x] **Step 4: Убедиться, что тесты проходят**

Run: `uv run pytest tests/test_platforms_nuxt.py -v`
Expected: PASS, 7 passed

- [x] **Step 5: Коммит**

```bash
git add hunter/platforms/nuxt.py tests/test_platforms_nuxt.py
git commit -m "feat: разбор payload window.__NUXT__"
```

---

### Task 2: Выдача rabota → карточки

**Files:**
- Create: `hunter/platforms/rabota.py`
- Modify: `hunter/platforms/__init__.py` (`PLATFORMS`, докстринг модуля)
- Create: `tests/fixtures/rabota_search_editor.html`, `tests/fixtures/rabota_search_empty.html`, `tests/fixtures/rabota_vacancy.html`
- Test: `tests/test_platforms_rabota.py`

**Interfaces:**
- Consumes: `parse_nuxt` из Task 1.
- Produces: `class Rabota` (`name = "rabota"`, `page_size = 30`, `max_pages = 20`) с `search_url`, `search_period`, `parse_cards`, `vacancy_url`; `card_row(vacancy: dict) -> dict` — колонки таблицы `vacancies`; `PLATFORMS["rabota"]`.

Фикстуры сняты вживую 2026-11-10 и лежат в `/tmp/opencode`:

```bash
cp /tmp/opencode/r_search.html  tests/fixtures/rabota_search_editor.html
cp /tmp/opencode/r_empty.html   tests/fixtures/rabota_search_empty.html
cp /tmp/opencode/r_vac.html     tests/fixtures/rabota_vacancy.html
```

Если `/tmp/opencode` уже очищен — снять заново:

```bash
curl -sS --compressed 'https://www.rabota.ru/vacancy/?query=%D1%80%D0%B5%D0%B4%D0%B0%D0%BA%D1%82%D0%BE%D1%80&all_regions=1&period=week&schedule_ids=6' -o tests/fixtures/rabota_search_editor.html
curl -sS --compressed 'https://www.rabota.ru/vacancy/?query=zzzzzzzzzz&all_regions=1' -o tests/fixtures/rabota_search_empty.html
curl -sS --compressed 'https://www.rabota.ru/vacancy/54461414/' -o tests/fixtures/rabota_vacancy.html
```

- [x] **Step 1: Написать падающие тесты выдачи**

```python
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hunter.models import SearchQuery
from hunter.parse import ParseError
from hunter.platforms import get
from hunter.platforms.base import Captcha
from hunter.platforms.rabota import card_row

FIXTURES = Path(__file__).parent / "fixtures"


def _query(**over):
    base = dict(name="author", text="редактор копирайтер", search_period=7,
                work_format="remote", area=113, max_pages=2)
    base.update(over)
    return SearchQuery(**base)


def _cards(name):
    return get("rabota").parse_cards((FIXTURES / name).read_text(encoding="utf-8"))


def test_search_url_asks_for_all_russia_remote_and_period():
    url = get("rabota").search_url(_query())

    assert url.startswith("https://www.rabota.ru/vacancy/?")
    assert "all_regions=1" in url
    assert "period=week" in url
    assert "schedule_ids=6" in url
    assert "page=" not in url, "page=0 — параметра нет вовсе"


def test_search_url_encodes_cyrillic_and_page_is_one_based():
    url = get("rabota").search_url(_query(), page=1)

    assert "query=%D1%80%D0%B5%D0%B4%D0%B0%D0%BA%D1%82%D0%BE%D1%80" in url
    assert "page=2" in url


def test_remote_schedule_and_salary_are_only_sent_when_asked():
    without = get("rabota").search_url(_query(work_format=None))

    assert "schedule_ids" not in without
    assert "min_salary" not in without
    assert "min_salary=120000" in get("rabota").search_url(_query(salary=120000))


def test_period_rounds_up_to_platform_ladder():
    platform = get("rabota")

    assert [platform.search_period(d) for d in (1, 2, 5, 7, 14, 60)] == [1, 3, 7, 7, 30, 30]
    assert "period=month" in platform.search_url(_query(search_period=30))


def test_cards_come_from_page_state():
    cards = _cards("rabota_search_editor.html")

    assert len(cards) == 2
    card = cards[0]
    assert (card.source, card.external_id) == ("rabota", "50107553")
    assert card.name == "Редактор китайского языка/Менеджер по работе с переводчиками"
    assert card.company.startswith("Индивидуальный предприниматель")
    assert card.url == "https://www.rabota.ru/vacancy/50107553/"
    assert card.area == "Москва"
    assert card.published_at == datetime(2026, 11, 8, 15, 43, 19,
                                        tzinfo=timezone(timedelta(hours=3)))
    assert (card.salary_from, card.salary_to) == (90000, None), "to=0 значит «границы нет»"
    assert (card.currency, card.salary_mode) == ("RUR", "MONTH")
    assert card.gross is None, "pay_type пустой — налоги неизвестны"
    assert card.work_formats == () and card.is_ad is False


def test_card_row_has_every_column_of_the_table():
    row = card_row({"id": 1, "title": "Т", "company": {"name": "К"}, "places": []})

    assert row["area"] is None
    assert row["salary_from"] is None and row["currency"] is None
    assert row["work_formats"] == "[]"
    assert {"source", "external_id", "name", "company", "company_id", "url",
            "area", "published_at", "salary_from", "salary_to", "currency",
            "gross", "salary_frequency", "experience", "work_formats",
            "employment_form", "employer_trusted", "employer_category",
            "antifraud_power", "responses_count", "card_json"} <= set(row)


def test_salary_period_comes_from_the_label_after_the_slash():
    hourly = card_row({"id": 2, "title": "Т", "salary": {
        "from": 0, "to": 900, "currency": "руб./час", "pay_type": "gross"}})

    assert (hourly["salary_from"], hourly["salary_to"]) == (None, 900)
    assert (hourly["currency"], hourly["salary_mode"]) == ("RUR", "HOUR")
    assert hourly["gross"] == 1

    unknown = card_row({"id": 3, "title": "Т", "salary": {
        "from": 1000, "to": 0, "currency": "руб./смена", "pay_type": "net"}})

    assert unknown["salary_mode"] is None, "незнакомый период не показываем месяцем"
    assert (unknown["currency"], unknown["gross"]) == ("RUR", 0)


def test_empty_search_is_not_an_error():
    assert _cards("rabota_search_empty.html") == []


def test_captcha_page_is_recognised():
    with pytest.raises(Captcha):
        get("rabota").parse_cards("<html>Подтвердите, что вы не робот</html>")


def test_page_without_state_and_captcha_words_is_a_parse_error():
    with pytest.raises(ParseError):
        get("rabota").parse_cards("<html>редизайн</html>")


def test_registry_knows_rabota():
    assert get("rabota").name == "rabota"
```

- [x] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_platforms_rabota.py -v`
Expected: FAIL — `KeyError: 'rabota'` (и `ImportError` на `hunter.platforms.rabota`)

- [x] **Step 3: Реализовать константы, `_state`, `_salary`, `card_row`, `_card`, `Rabota` и зарегистрировать площадку**

`hunter/platforms/rabota.py`:

```python
BASE_URL = "https://www.rabota.ru"
SEARCH_PATH = "/vacancy/"
VACANCY_URL = BASE_URL + "/vacancy/{id}/"
# Периоды выдачи: дни, которые понимает площадка, и её строка периода.
PERIODS = ((1, "day"), (3, "three-days"), (7, "week"), (30, "month"))
REMOTE_SCHEDULE_ID = 6
_CAPTCHA_MARKERS = ("captcha", "капча", "робот", "подтвердите")
```

- `_state(page_html) -> dict`: нет строки `window.__NUXT__` — есть слово-маркер капчи → `Captcha("в ответе rabota нет window.__NUXT__ — похоже на капчу")`, иначе `ParseError("В странице rabota нет window.__NUXT__ — сайт сменил формат")`. Есть — `parse_nuxt(page_html)`, затем `state = payload.get("state")`; не словарь → `ParseError("window.__NUXT__ разобрался без state — сайт сменил формат")`.
- `_salary(salary) -> dict`: `salary` пустой → все поля `None`. `from`/`to`: `0` → `None`. `currency` и `salary_mode` — из подписи `руб./мес.`: до слеша валюта (`руб.`/`руб`→`RUR`, `$`/`USD`→`USD`, `€`/`EUR`→`EUR`, иначе `None`), после слеша период (`мес.`→`MONTH`, `час`→`HOUR`, `день`/`дн.`→`DAY`, `нед.`→`WEEK`, иначе `None`). `gross`: `pay_type == "gross"` → `1`, `"net"` → `0`, пусто → `None`. `salary_frequency` всегда `None`.
- `card_row(vacancy) -> dict`: ровно те же ключи, что у `hh.card_row` и `superjob._card`; `area` — `places[0].region.name`, иначе `places[0].name`, иначе `None`; `external_id` — `str(vacancy["id"])`; `company_id` — `company.id`; `published_at` — `publish_start_at`; `work_formats` — `"[]"`; `card_json` — `json.dumps(vacancy, ensure_ascii=False)`; `employer_trusted`, `employer_category`, `antifraud_power`, `responses_count` — `None`.
- `_card(vacancy) -> Card`: поля из `card_row`, `gross` — `None` или `bool`, `work_formats=()`, `is_ad=False`.
- `Rabota.search_url(query, page=0)`: параметры `query=query.text`, `all_regions=1`, `period=<строка из PERIODS>`, `schedule_ids=6` при `query.work_format == "remote"`, `min_salary=query.salary` при заданной, `page=page+1` при `page > 0`. Сборка — `urlencode`, как у superjob.
- `Rabota.search_period(days) -> int`: ближайший период из `(1, 3, 7, 30)` не короче запрошенного; больше 30 → 30.
- `Rabota.parse_cards(page_html)`: `state.rabota.search`; `totalVacancies == 0` → `[]`; нет `search` или `vacancies` не список → `ParseError("в состоянии rabota нет state.rabota.search.vacancies")`; карточки — `_card`, любые `KeyError/TypeError/ValueError/AttributeError` при разборе одной карточки → `ParseError` с текстом «Карточка вакансии не разобралась (…) — сайт сменил формат» (как у superjob).
- `Rabota.vacancy_url(external_id)`: `VACANCY_URL.format(id=external_id)`.

`hunter/platforms/__init__.py`: докстринг → `«Площадки поиска работы: hh (он же zarplata), superjob и rabota.»`, импорт `Rabota`, `"rabota": Rabota()` в `PLATFORMS`.

- [x] **Step 4: Убедиться, что тесты проходят**

Run: `uv run pytest tests/test_platforms_rabota.py -v`
Expected: PASS

- [x] **Step 5: Коммит**

```bash
git add hunter/platforms/rabota.py hunter/platforms/__init__.py tests/test_platforms_rabota.py tests/fixtures/rabota_search_editor.html tests/fixtures/rabota_search_empty.html
git commit -m "feat: площадка rabota — карточки из выдачи"
```

---

### Task 3: Страница вакансии, ссылки и неподдерживаемые фильтры

**Files:**
- Modify: `hunter/platforms/rabota.py`
- Modify: `tests/test_platforms_rabota.py`

**Interfaces:**
- Produces: `Rabota.parse_detail(page_html) -> Detail`, `Rabota.parse_ref(ref) -> str | None`, `Rabota.unsupported(query) -> tuple[str, ...]`.

- [x] **Step 1: Дописать падающие тесты**

```python
import json

from hunter.platforms import format_ref, parse_ref


def _nuxt_page(payload: dict) -> str:
    """Минимальная страница с состоянием: JSON — законный JS-литерал."""
    state = json.dumps(payload, ensure_ascii=False)
    return f"<script>window.__NUXT__=(function(a){{return {state};}}({{}}));</script>"


def test_vacancy_url_is_canonical():
    assert get("rabota").vacancy_url("54461414") == (
        "https://www.rabota.ru/vacancy/54461414/"
    )


def test_detail_carries_full_text_skills_and_remote_format():
    detail = get("rabota").parse_detail(
        (FIXTURES / "rabota_vacancy.html").read_text(encoding="utf-8"))

    assert detail.external_id == "54461414"
    assert detail.description.startswith("Ищем контент-менеджера")
    assert "<p>" not in detail.description
    assert detail.key_skills == ["пунктуальность", "коммуникабельность", "грамотность",
                                 "обучаемость", "корректура текста"]
    assert detail.status == "active"
    assert detail.row["work_formats"] == '["REMOTE"]'


def test_unpublished_vacancy_is_archived():
    page = _nuxt_page({"state": {"rabota": {"vacancy": {"vacancy": {
        "id": 1, "title": "Т", "status": "closed", "description": "<p>текст</p>",
        "company": {"name": "К"}, "places": [], "skills": []}}}}})

    assert get("rabota").parse_detail(page).status == "archived"


def test_captcha_page_on_vacancy_is_recognised():
    with pytest.raises(Captcha):
        get("rabota").parse_detail("<html>Подтвердите, что вы не робот</html>")


def test_vacancy_page_without_vacancy_node_is_a_parse_error():
    with pytest.raises(ParseError):
        get("rabota").parse_detail(_nuxt_page({"state": {"rabota": {"vacancy": {}}}}))


def test_ref_forms_are_understood():
    platform = get("rabota")

    assert platform.parse_ref("https://www.rabota.ru/vacancy/54461414/") == "54461414"
    assert platform.parse_ref("https://rabota.ru/vacancy/54461414") == "54461414"
    assert platform.parse_ref("https://hh.ru/vacancy/54461414") is None
    assert parse_ref("rabota:54461414") == ("rabota", "54461414")
    assert format_ref("rabota", "54461414") == "rabota:54461414"


def test_unsupported_lists_what_the_site_ignores():
    platform = get("rabota")

    assert platform.unsupported(_query()) == ()
    ignored = platform.unsupported(_query(
        area=1, only_with_salary=True,
        experience=("between1And3",), work_format="hybrid",
    ))
    assert set(ignored) == {"area", "only_with_salary", "experience", "work_format"}
```

- [x] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_platforms_rabota.py -v`
Expected: FAIL — у `Rabota` нет `parse_detail`/`parse_ref`/`unsupported`

- [x] **Step 3: Реализовать три метода**

- `parse_detail`: `state = _state(page_html)`; `vacancy = state.get("rabota", {}).get("vacancy", {}).get("vacancy"`; не словарь → `ParseError("в состоянии rabota нет state.rabota.vacancy.vacancy — сайт сменил формат")`. `row = card_row(vacancy)`, но с `work_formats = json.dumps(["REMOTE"] if (vacancy.get("operating_schedule") or {}).get("id") == REMOTE_SCHEDULE_ID else [])`. `Detail(description=html_to_markdown(vacancy.get("description") or ""), key_skills=[str(skill["name"]) for skill in vacancy.get("skills") or []], status="active" if vacancy.get("status") == "published" else "archived", raw=vacancy)`.
- `parse_ref`: `urlparse`, хост `rabota.ru` или поддомен; путь строго `/vacancy/<цифры>/` → id, иначе `None`.
- `unsupported`: `area` при `query.area != 113`; `only_with_salary` при `True`; `experience` при непустом; `work_format` при значении вне `(None, "remote")`. Зарплата и период поддерживаются — их не перечислять.

- [x] **Step 4: Убедиться, что тесты проходят**

Run: `uv run pytest tests/test_platforms_rabota.py tests/test_platforms_nuxt.py -v`
Expected: PASS

- [x] **Step 5: Коммит**

```bash
git add hunter/platforms/rabota.py tests/test_platforms_rabota.py tests/fixtures/rabota_vacancy.html
git commit -m "feat: площадка rabota — страница вакансии и ссылки"
```

---

### Task 4: Капча не роняет проход

**Files:**
- Modify: `hunter/run.py:36` (импорт), `hunter/run.py:97-101` (обработка `Blocked`)
- Test: `tests/test_run.py`

**Interfaces:**
- Consumes: `Captcha` из `hunter.platforms`.
- Produces: `_collect` считает источник заблокированным при `Captcha`, так же как при `Blocked`; `run_pass` возвращает `EXIT_BLOCKED` (3).

- [x] **Step 1: Написать падающий тест**

```python
def test_captcha_blocks_the_pass_instead_of_crashing_it():
    config = Config(
        sources=("rabota",),
        queries=(SearchQuery(name="Редактор", text="редактор"),),
    )
    store = Store(":memory:")
    sender = Sender()

    def fetch_page(url):
        return "<html>Подтвердите, что вы не робот</html>"

    code = run_pass(config=config, store=store, fetch_page=fetch_page, send=sender, now=NOW)

    text = "\n".join(sender.messages)
    assert code == 3, "капча — блокировка, а не непредвиденная ошибка"
    assert "rabota" in text and "заблокировал" in text
```

- [x] **Step 2: Убедиться, что тест падает**

Run: `uv run pytest tests/test_run.py::test_captcha_blocks_the_pass_instead_of_crashing_it -v`
Expected: FAIL — код `4` вместо `3` (`main`-овский `except Exception` тут ни при чём: `run_pass` не ловит `Captcha` и он уходит наружу как исключение — тест упадёт с `Captcha`)

- [x] **Step 3: Ловить `Captcha` вместе с `Blocked`**

В `hunter/run.py`: `from .platforms import Captcha, get`; в `_collect` заменить `except Blocked as exc:` на `except (Blocked, Captcha) as exc:`. Тело ветки не меняется: `blocked_sources.add(source)`, предупреждение `f"{source} заблокировал запросы: {exc}"`, `break`. Слова «заблокировал» для капчи — сознательно: для владельца это одно и то же событие «площадка не пустила».

- [x] **Step 4: Убедиться, что тесты проходят**

Run: `uv run pytest tests/test_run.py -v`
Expected: PASS, включая прежние тесты блокировок

- [x] **Step 5: Коммит**

```bash
git add hunter/run.py tests/test_run.py
git commit -m "fix: капча площадки завершает проход кодом 3, а не падением"
```

---

### Task 5: Формат работы со страницы вакансии доезжает до базы

**Files:**
- Modify: `hunter/corpus/store.py:407-421` (`Corpus.update_detail`)
- Test: `tests/test_corpus_store.py`

**Interfaces:**
- Consumes: `Detail.row["work_formats"]` — JSON-строка, как её кладут все площадки (`hh`, `superjob`, `rabota`).
- Produces: `update_detail` перезаписывает `vacancies.work_formats`, только если список из детали непустой; сигнатура не меняется.

- [x] **Step 1: Написать падающие тесты**

```python
def test_update_detail_writes_work_formats_from_the_vacancy_page(corpus):
    row = vacancy_card_row(make_vacancy())
    row["work_formats"] = json.dumps([])
    vacancy_id = corpus.upsert_card(row)[1]

    corpus.update_detail(vacancy_id, make_detail())

    assert json.loads(corpus.vacancy(vacancy_id)["work_formats"]) == ["REMOTE"]


def test_update_detail_does_not_erase_known_formats_with_an_empty_list(corpus):
    vacancy_id = corpus.upsert_card(vacancy_card_row(make_vacancy()))[1]
    blank = {**vacancy_card_row(make_vacancy()), "work_formats": json.dumps([])}

    corpus.update_detail(vacancy_id, make_detail(row=blank))

    assert json.loads(corpus.vacancy(vacancy_id)["work_formats"]) == ["REMOTE"]
```

(в шапку файла добавить `import json`; `make_detail` по умолчанию несёт `work_formats: ["REMOTE"]`)

- [x] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_corpus_store.py -k update_detail -v`
Expected: FAIL — первый тест видит `[]` вместо `["REMOTE"]`

- [x] **Step 3: Дописать `work_formats` в `UPDATE`**

В `update_detail` разобрать `json.loads(detail.row.get("work_formats") or "[]")`; если список непустой — `UPDATE` дополнительно пишет `work_formats` (JSON-строка исходная, не пересобранная); пустой — прежний `UPDATE` без этой колонки, чтобы не затирать то, что уже известно.

- [x] **Step 4: Убедиться, что тесты проходят**

Run: `uv run pytest tests/test_corpus_store.py -v`
Expected: PASS

- [x] **Step 5: Коммит**

```bash
git add hunter/corpus/store.py tests/test_corpus_store.py
git commit -m "fix: формат работы со страницы вакансии сохраняется в базу"
```

---

### Task 6: Справочник, пример конфига и личные запросы

**Files:**
- Modify: `hunter/corpus/GUIDE.md` (строки 40, 52-56, 63, 163, 168, 171, 195)
- Modify: `hunter/corpus/cli.py:400-407` (подсказки `collect`)
- Modify: `config.example.yaml` (комментарий к `sources`)
- Modify: `me/config.yaml` (личный, в git не идёт — коммита в этом шаге нет)

**Interfaces:**
- Produces: документация и рабочая настройка, при которой rabota получает обычные слова, а не hh-синтаксис.

- [x] **Step 1: Обновить `hunter/corpus/GUIDE.md`**

- Строка 40: добавить, что `--source rabota` идёт на rabota.ru.
- Список `--source` (52): `hh | zarplata | superjob | rabota`; отдельным предложением: у rabota нет `--area` (кроме 113), `--experience` и `--with-salary`, форматов кроме `remote`; зарплата и период поддерживаются; запрос — только обычные слова, hh-синтаксис площадка не понимает.
- Строка 63: к формам ссылок добавить `rabota:54461414` и ссылки rabota.ru (любой поддомен).
- Таблица схемы: `source` — добавить `rabota`; `published_at` — rabota отдаёт дату публикации в выдаче; `salary_frequency` — у rabota не различается, остаётся NULL.
- Строка 195: ключи `seen` — `hh:<id>`, `zarplata:<id>`, `superjob:<id>` или `rabota:<id>`.

- [x] **Step 2: Обновить `config.example.yaml`**

Комментарий у `sources`: `# hh, zarplata, superjob, rabota`.

- [x] **Step 3: Поправить подсказки `collect` в `hunter/corpus/cli.py`**

Строка 400 — `«собрать карточки из выдачи площадки под роль»`; `--text` (404) — `«поисковый запрос: hh понимает AND, OR, NOT и NAME:, остальные площадки — только обычные слова»`; `--pages` (407) — `f"страниц выдачи, от 1 до {MAX_PAGES}"` (без «по 50»: у площадок разный размер страницы).

- [x] **Step 4: Настроить `me/config.yaml`**

`sources: [hh, superjob, rabota]`.

В каждом запросе — `text_by_source` с обеими строками (`superjob` сохранить как есть или `null`, `rabota` — обычные слова). Карта запроса перекрывает умолчания целиком, поэтому ключ `superjob` обязателен в каждом: без него superjob получит hh-текст.

| Запрос | `superjob` | `rabota` |
|---|---|---|
| Автор и редактор | `null` | `автор редактор копирайтер` |
| Автор и редактор — удалёнка в тексте | `редактор удаленно` | `редактор удаленно` |
| Вайбкодер | `null` | `вайбкодер` |
| Вайбкодер — удалёнка в тексте | `null` | `вайбкодер удаленно` |
| Контент + ИИ | `null` | `редактор нейросети` |
| Контент + ИИ — удалёнка в тексте | `null` | `редактор нейросети удаленно` |
| Продвижение: SMM и реклама | `null` | `SMM менеджер` |
| Продвижение — удалёнка в тексте | `продвижение сайта удаленно` | `SMM менеджер удаленно` |

- [x] **Step 5: Проверить, что конфиг читается**

Run:

```bash
uv run python -c "
from hunter.config import load_config
c = load_config()
print('sources:', c.sources)
for q in c.queries:
    print(q.name, '| rabota:', q.text_for('rabota'))
"
```

Expected: `sources: ('hh', 'superjob', 'rabota')`; у каждого запроса `rabota:` — обычные слова без `NAME:`, `AND`, `OR`, `NOT`

- [x] **Step 6: Коммит**

```bash
git add hunter/corpus/GUIDE.md hunter/corpus/cli.py config.example.yaml
git commit -m "docs: rabota в справочнике, подсказках и примере конфига"
```

---

### Task 7: Живая проверка

**Files:** нет изменений кода.

- [x] **Step 1: Полный набор тестов**

Run: `uv run pytest`
Expected: зелёный, без сети

- [x] **Step 2: Живой проход без записи в базу и без оценки**

Run: `uv run hunter --days 7 --dry-run --no-score`
Expected: код выхода 0, в сводке есть блоки с rabota и карточки, нет предупреждений о смене формата и нет «похоже на капчу»

- [x] **Step 3: Сбор в базу по площадке**

Run: `uv run vacancies collect --source rabota --role author --text "редактор копирайтер" --days 7`
Expected: карточки записаны, отчёт без ОШИБКИ и без блокировки. Дальше проверить текст:

```bash
uv run vacancies sql "SELECT source || ':' || external_id FROM vacancies WHERE source = 'rabota' LIMIT 5"
uv run vacancies fetch rabota:<id>
uv run vacancies sql "SELECT length(description), key_skills, work_formats FROM vacancies WHERE source = 'rabota' AND external_id = '<id>'"
```

Expected: `description` длиннее тысячи символов, `key_skills` — непустой JSON-список, `work_formats` — `["REMOTE"]`

- [x] **Step 4: Сообщить владельцу результат**

Коротко: сколько карточек дала rabota на живом проходе, какие предупреждения были, все ли тесты зелёные. Затем — отдельный субагент `reviewer` по всей ветке (правило проекта).
