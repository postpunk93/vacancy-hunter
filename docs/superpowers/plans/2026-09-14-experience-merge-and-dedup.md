# Experience Merge and City Dedup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Один запрос на несколько уровней опыта вместо нескольких отдельных, плюс схлопывание в дайджесте вакансий, размещённых отдельными карточками в разных городах.

**Architecture:** Точечные изменения существующего пайплайна `hunter` (models → config → sources → parse → filters → digest → run), без новых внешних зависимостей. `SearchQuery.experience` становится кортежем нескольких значений; `Vacancy` получает поле `experience`; `filters.dedupe` дополняется вторым проходом дедупликации по `(company, name)` и возвращает поглощённые вакансии отдельно, чтобы `run_pass` пометил их виденными, не показывая.

**Tech Stack:** Python 3.14, `httpx`, `pyyaml`, `pytest` — без изменений.

**Spec:** `docs/superpowers/specs/2026-09-14-experience-merge-and-dedup-design.md`

## Global Constraints

- Ни один тест не ходит в сеть — фикстуры уже лежат в `tests/fixtures/`.
- Весь текст, видимый пользователю (дайджест, ошибки), — на русском.
- `experience` в `config.yaml` принимает и строку, и список — нормализуется в `tuple[str, ...]` в `hunter/config.py`; дополнительной валидации кодов опыта не добавляется (как и раньше для одиночного значения).
- Ключ дедупликации вакансий-клонов — только `(company, name)`, без учёта зарплаты и источника (hh/zarplata дедуплицируются вместе).
- Отображение городов у схлопнутой вакансии: ≤3 уникальных города — все через запятую; >3 — первые три и «и ещё N город(ов)» со склонением.
- Поглощённые дубликаты помечаются виденными (`sent=False`) сразу после дедупликации, безусловно — независимо от того, попадёт ли представитель в этот проход или будет отложен `truncate`.
- Инвариант проекта не меняется: `store.mark_seen(..., sent=True)` — только после успешной отправки в Телеграм.
- Коммит после каждой задачи.

## File Structure

Новый файл:

| Файл | Ответственность |
|---|---|
| `hunter/text.py` | Общие текстовые хелперы, не завязанные на формат дайджеста: сейчас — `plural()`, вынесенная из `digest.py`, чтобы её мог использовать и `filters.py` без цикла импорта `filters ↔ digest` |

Изменяемые файлы:

| Файл | Что меняется |
|---|---|
| `hunter/models.py` | `SearchQuery.experience` → `tuple[str, ...]`; `Vacancy` получает поле `experience: str \| None` |
| `hunter/config.py` | Нормализация `experience` (строка/список/отсутствует) в кортеж |
| `hunter/sources.py` | `build_url` добавляет по параметру `experience` на каждое значение кортежа |
| `hunter/parse.py` | `_to_vacancy` читает `workExperience` из карточки |
| `hunter/digest.py` | Метка опыта в строке вакансии; `plural` теперь импортируется из `hunter/text.py` |
| `hunter/filters.py` | `dedupe` дополняется дедупликацией по `(company, name)`, возвращает `tuple[list[Group], list[Vacancy]]` |
| `hunter/run.py` | Распаковывает новую сигнатуру `dedupe`, помечает поглощённые вакансии виденными |
| `config.yaml`, `config.example.yaml` | Пары запросов по опыту схлопнуты в один; пример списка в example |
| `README.md` | Раздел «Настройка запросов» описывает список `experience` и дедупликацию по городам |

Тестовые файлы: новый `tests/test_text.py`; изменяются `tests/test_models.py`,
`tests/test_config.py`, `tests/test_sources.py`, `tests/test_parse.py`,
`tests/test_digest.py`, `tests/test_filters.py`, `tests/test_run.py`.

Порядок задач: 1 → 2 → 3, 4, 5 (независимы друг от друга после задачи 1,
но перечислены по порядку модулей в пайплайне) → 6 → 7 → 8. Задача 6
(общий текстовый хелпер + дедупликация) — самая объёмная, задача 7 на неё
опирается.

---

### Task 1: Модели — поле `experience` у `SearchQuery` и `Vacancy`

**Files:**
- Modify: `hunter/models.py`
- Modify: `tests/factories.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: ничего нового.
- Produces: `SearchQuery.experience: tuple[str, ...]` (по умолчанию `()`), `Vacancy.experience: str | None`. Все последующие задачи используют эти имена и типы как есть.

- [ ] **Step 1: Написать падающие тесты**

В `tests/test_models.py` замените последний тест на:

```python
def test_search_query_defaults_match_the_spec():
    q = SearchQuery(name="Python", text="python")
    assert q.area == 1
    assert q.only_with_salary is True
    assert q.search_period == 1
    assert q.max_pages == 1
    assert q.salary is None
    assert q.experience == ()
```

И добавьте в конец файла:

```python
def test_vacancy_carries_the_raw_experience_code():
    assert _vacancy(experience="between1And3").experience == "between1And3"
```

- [ ] **Step 2: Запустить тесты и убедиться, что падают**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_models.py -v`
Expected: FAIL — `TypeError: SearchQuery.__init__() got an unexpected keyword argument 'experience'` (второй тест) и `AttributeError` / `TypeError` для `_vacancy(experience=...)`, так как `make_vacancy` пока не знает про `experience`.

- [ ] **Step 3: Обновить `hunter/models.py`**

В `SearchQuery` замените строку

```python
    experience: str | None = None
```

на

```python
    experience: tuple[str, ...] = ()
```

В `Vacancy` добавьте новое поле сразу после `work_formats`:

```python
    work_formats: tuple[str, ...]
    experience: str | None
    is_ad: bool
```

- [ ] **Step 4: Обновить `tests/factories.py`**

В `_DEFAULTS` добавьте ключ рядом с `work_formats`:

```python
    work_formats=("REMOTE",),
    experience="between1And3",
    is_ad=False,
```

- [ ] **Step 5: Запустить тесты и убедиться, что проходят**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 6: Прогнать весь набор тестов**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: остальные модули пока не знают о новом обязательном поле `Vacancy.experience` только там, где вакансии создаются напрямую (не через `make_vacancy`) — это `hunter/parse.py` (задача 4) и `tests/test_parse.py`. Ожидаются падения в `test_parse.py` (`_to_vacancy` не передаёт `experience` → `TypeError`) — это нормально, чинится в задаче 4. Остальные модули (`filters`, `digest`, `run`) используют только `make_vacancy`, поэтому их тесты остаются зелёными.

- [ ] **Step 7: Commit**

```bash
git add hunter/models.py tests/factories.py tests/test_models.py
git commit -m "$(cat <<'EOF'
feat: model experience as a tuple, add Vacancy.experience

Prepares SearchQuery/Vacancy for combining per-level experience queries
into one request and showing the level per vacancy in the digest.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Конфиг — `experience` строкой или списком

**Files:**
- Modify: `hunter/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `SearchQuery.experience: tuple[str, ...]` (Task 1).
- Produces: `load_config()` кладёт в `SearchQuery.experience` нормализованный кортеж независимо от формы значения в YAML.

- [ ] **Step 1: Написать падающие тесты**

Добавьте в `tests/test_config.py`:

```python
def test_experience_as_a_single_string_becomes_a_one_item_tuple(tmp_path):
    path = write(tmp_path, """
queries:
  - name: "x"
    text: "y"
    experience: between1And3
""")
    cfg = load_config(path)
    assert cfg.queries[0].experience == ("between1And3",)


def test_experience_as_a_list_becomes_a_tuple(tmp_path):
    path = write(tmp_path, """
queries:
  - name: "x"
    text: "y"
    experience: [noExperience, between1And3]
""")
    cfg = load_config(path)
    assert cfg.queries[0].experience == ("noExperience", "between1And3")


def test_experience_defaults_to_an_empty_tuple_when_not_set(tmp_path):
    path = write(tmp_path, """
queries:
  - name: "x"
    text: "y"
""")
    cfg = load_config(path)
    assert cfg.queries[0].experience == ()
```

- [ ] **Step 2: Запустить тесты и убедиться, что падают**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_config.py -v`
Expected: FAIL на первых двух — `load_config` сейчас передаёт `experience` в `SearchQuery` как есть (строку или список), а не кортеж.

- [ ] **Step 3: Обновить `load_config` в `hunter/config.py`**

В цикле `for number, entry in enumerate(entries, start=1):`, после проверки `name`/`text` и перед `queries.append(SearchQuery(**merged))`, добавьте:

```python
        experience = merged.get("experience")
        if experience is None:
            merged["experience"] = ()
        elif isinstance(experience, str):
            merged["experience"] = (experience,)
        else:
            merged["experience"] = tuple(experience)

        queries.append(SearchQuery(**merged))
```

- [ ] **Step 4: Запустить тесты и убедиться, что проходят**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add hunter/config.py tests/test_config.py
git commit -m "$(cat <<'EOF'
feat: accept experience as a string or a list in config.yaml

Normalizes both forms into a tuple so one query can filter by several
experience levels at once.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: URL — несколько значений `experience` в одном запросе

**Files:**
- Modify: `hunter/sources.py`
- Test: `tests/test_sources.py`

**Interfaces:**
- Consumes: `SearchQuery.experience: tuple[str, ...]` (Task 1).
- Produces: `build_url` добавляет одну пару `experience=<value>` на каждый элемент кортежа; поведение при пустом кортеже не меняется (параметр не добавляется).

- [ ] **Step 1: Написать падающие тесты**

В `tests/test_sources.py` замените вызов в `test_optional_filters_are_included_when_set`, заменив строку

```python
            experience="between3And6",
```

на

```python
            experience=("between3And6",),
```

(сама остальная часть теста не меняется — с одним значением поведение то же самое).

Добавьте в конец файла:

```python
def test_multiple_experience_values_all_appear_in_the_url():
    url = build_url(
        "hh",
        SearchQuery(name="q", text="python", experience=("noExperience", "between1And3")),
    )

    values = parse_qs(urlsplit(url).query)["experience"]
    assert values == ["noExperience", "between1And3"]
```

- [ ] **Step 2: Запустить тесты и убедиться, что падают**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_sources.py -v`
Expected: FAIL — с кортежем `("between3And6",)` текущий код кладёт в URL `experience=%28%27between3And6%27%2C%29` (repr кортежа через `urlencode`), а не `between3And6`; новый тест падает с `KeyError: 'experience'` или неверным списком значений (текущий код итерирует по строке `query.experience`, если бы это была строка — но теперь это кортеж, так что `if query.experience:` истинно, а `params.append(("experience", query.experience))` кладёт весь кортеж одним значением).

- [ ] **Step 3: Обновить `build_url` в `hunter/sources.py`**

Замените

```python
    if query.experience:
        params.append(("experience", query.experience))
```

на

```python
    if query.experience:
        for value in query.experience:
            params.append(("experience", value))
```

- [ ] **Step 4: Запустить тесты и убедиться, что проходят**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_sources.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add hunter/sources.py tests/test_sources.py
git commit -m "$(cat <<'EOF'
feat: emit one experience= param per configured level

hh and zarplata both accept repeated experience= query params, so one
request can now cover several experience levels at once.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Парсинг — `workExperience` из карточки вакансии

**Files:**
- Modify: `hunter/parse.py`
- Test: `tests/test_parse.py`

**Interfaces:**
- Consumes: `Vacancy.experience: str | None` (Task 1).
- Produces: `parse_search_page()` заполняет `Vacancy.experience` значением поля `workExperience` карточки (или `None`, если поля нет).

- [ ] **Step 1: Написать падающий тест**

Добавьте в `tests/test_parse.py`:

```python
def test_experience_code_is_captured():
    vacancies = parse_search_page(fixture("hh_search_sorted_with_salary.html"), "hh")

    assert {v.experience for v in vacancies} == {
        "noExperience", "between1And3", "between3And6", "moreThan6",
    }
```

- [ ] **Step 2: Запустить тесты и убедиться, что падают**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_parse.py -v`
Expected: FAIL — все тесты в файле падают с `TypeError: Vacancy.__init__() missing 1 required positional argument: 'experience'`, поскольку `_to_vacancy` пока не передаёт это поле (оно стало обязательным в Task 1).

- [ ] **Step 3: Обновить `_to_vacancy` в `hunter/parse.py`**

В вызове `Vacancy(...)` добавьте после `work_formats=...,`:

```python
        work_formats=tuple(
            element
            for block in (raw.get("workFormats") or [])
            for element in (block.get("workFormatsElement") or [])
        ),
        experience=raw.get("workExperience"),
        is_ad=bool(raw.get("@isAdv")),
```

- [ ] **Step 4: Запустить тесты и убедиться, что проходят**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_parse.py -v`
Expected: PASS

- [ ] **Step 5: Прогнать весь набор тестов**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS везде — начиная с этой задачи `Vacancy` снова полностью собирается что через `make_vacancy`, что через `_to_vacancy`.

- [ ] **Step 6: Commit**

```bash
git add hunter/parse.py tests/test_parse.py
git commit -m "$(cat <<'EOF'
feat: capture workExperience from the vacancy card

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Дайджест — метка опыта в строке вакансии

**Files:**
- Modify: `hunter/digest.py`
- Test: `tests/test_digest.py`

**Interfaces:**
- Consumes: `Vacancy.experience: str | None` (Task 1).
- Produces: `_format_vacancy()` вставляет распознанную метку опыта между зарплатой и городом; нераспознанный код или `None` — молча опускается.

- [ ] **Step 1: Написать падающие тесты**

Добавьте в `tests/test_digest.py`:

```python
def test_experience_label_is_shown_for_known_codes():
    v = make_vacancy(experience="between1And3")
    text = render([("q", [v])], NOW)[0]
    assert "1–3 года" in text


def test_unknown_or_missing_experience_is_omitted_silently():
    known = make_vacancy(vacancy_id="1", experience="moreThan6")
    unknown = make_vacancy(vacancy_id="2", experience="weirdCode")
    missing = make_vacancy(vacancy_id="3", experience=None)

    text = render([("q", [known, unknown, missing])], NOW)[0]

    assert "более 6 лет" in text
    assert "weirdCode" not in text
```

- [ ] **Step 2: Запустить тесты и убедиться, что падают**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_digest.py -v`
Expected: FAIL — оба новых теста, метка опыта нигде не выводится.

- [ ] **Step 3: Обновить `hunter/digest.py`**

Добавьте таблицу меток рядом с `_WORK_FORMAT`:

```python
_EXPERIENCE = {
    "noExperience": "без опыта",
    "between1And3": "1–3 года",
    "between3And6": "3–6 лет",
    "moreThan6": "более 6 лет",
}
```

В `_format_vacancy` вставьте метку опыта между зарплатой и местом:

```python
def _format_vacancy(vacancy: Vacancy) -> str:
    place_parts = [vacancy.area] if vacancy.area else []
    place_parts += [
        _WORK_FORMAT[f] for f in vacancy.work_formats if f in _WORK_FORMAT
    ]
    details = [vacancy.company, format_salary(vacancy)]
    experience_label = _EXPERIENCE.get(vacancy.experience or "")
    if experience_label:
        details.append(experience_label)
    if place_parts:
        details.append(", ".join(place_parts))

    return (
        f'• <a href="{escape(vacancy.url)}">{escape(vacancy.name)}</a>\n'
        f'  {escape(" · ".join(details))}'
    )
```

- [ ] **Step 4: Запустить тесты и убедиться, что проходят**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_digest.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add hunter/digest.py tests/test_digest.py
git commit -m "$(cat <<'EOF'
feat: show experience level per vacancy in the digest

One query can now cover several experience levels, so the level moved
from the block name into each vacancy's detail line.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Общий текстовый хелпер + дедупликация вакансий по городам

**Files:**
- Create: `hunter/text.py`
- Test: `tests/test_text.py`
- Modify: `hunter/digest.py`
- Modify: `tests/test_digest.py`
- Modify: `hunter/filters.py`
- Modify: `tests/test_filters.py`

**Interfaces:**
- Consumes: `Vacancy` (`company`, `name`, `area`, `.key`) из Task 1; ничего из Task 2–5.
- Produces: `hunter.text.plural(count, one, few, many) -> str` (тот же контракт, что был у `hunter.digest.plural` — `hunter.digest.plural` остаётся рабочим импортом для внешнего кода). `filters.dedupe(groups: list[Group]) -> tuple[list[Group], list[Vacancy]]` — второй элемент это плоский список поглощённых вакансий. Эту новую сигнатуру использует Task 7.

`filters.py` не может импортировать `plural` из `digest.py` напрямую: `digest.py` уже импортирует `Group` из `filters.py`, обратный импорт дал бы цикл. Поэтому `plural` сначала переезжает в новый модуль `hunter/text.py`, а `digest.py` берёт её оттуда.

- [ ] **Step 1: Написать падающий тест на новый модуль**

Создайте `tests/test_text.py`:

```python
from hunter.text import plural


def test_plural_is_a_reusable_general_helper():
    assert plural(1, "запросом", "запросами", "запросами") == "1 запросом"
    assert plural(2, "запросом", "запросами", "запросами") == "2 запросами"
    assert plural(12, "запросом", "запросами", "запросами") == "12 запросами"
```

Удалите этот же тест (`test_plural_is_a_reusable_general_helper`) из `tests/test_digest.py` — он переехал.

- [ ] **Step 2: Запустить тест и убедиться, что падает**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_text.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hunter.text'`

- [ ] **Step 3: Создать `hunter/text.py` и перенести `plural` из `digest.py`**

Создайте `hunter/text.py`:

```python
from __future__ import annotations


def plural(count: int, one: str, few: str, many: str) -> str:
    """Число + русское согласование с ним: `plural(1, "запросом", "запросами",
    "запросами")` → «1 запросом», `plural(3, ...)` → «3 запросами»."""
    tail = count % 100
    if 11 <= tail <= 14:
        word = many
    elif count % 10 == 1:
        word = one
    elif count % 10 in (2, 3, 4):
        word = few
    else:
        word = many
    return f"{count} {word}"
```

В `hunter/digest.py` удалите определение функции `plural` (весь блок с её докстрокой) и добавьте импорт в начало файла, рядом с остальными:

```python
from .filters import Group
from .models import Vacancy
from .text import plural
```

- [ ] **Step 4: Запустить тесты и убедиться, что проходят**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_text.py tests/test_digest.py -v`
Expected: PASS — `hunter.digest.plural` по-прежнему доступен (это теперь просто имя, импортированное в модуль), существующие тесты, использующие `render()` и завязанные на склонение, не меняются.

- [ ] **Step 5: Commit**

```bash
git add hunter/text.py hunter/digest.py tests/test_text.py tests/test_digest.py
git commit -m "$(cat <<'EOF'
refactor: extract plural() into hunter/text.py

filters.py needs it for the upcoming city-count formatting in dedupe(),
and importing it from digest.py would create a cycle (digest already
imports Group from filters).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6: Написать падающие тесты на дедупликацию по компании и названию**

В `tests/test_filters.py` обновите три существующих теста под новую сигнатуру
(`dedupe` теперь возвращает кортеж) и уберите случайное совпадение
компании/названия там, где тест должен проверять только id-дедупликацию:

```python
def test_duplicate_stays_in_the_first_group_it_appeared_in():
    shared = make_vacancy(vacancy_id="100")
    only_second = make_vacancy(
        vacancy_id="200", company="Другая компания", name="Другая вакансия"
    )

    result, absorbed = dedupe([("Первый", [shared]), ("Второй", [shared, only_second])])

    assert result == [("Первый", [shared]), ("Второй", [only_second])]
    assert absorbed == []


def test_same_id_from_different_sources_is_not_a_duplicate():
    hh = make_vacancy(source="hh", vacancy_id="42", company="Авито")
    zp = make_vacancy(source="zarplata", vacancy_id="42", company="Яндекс")

    result, absorbed = dedupe([("Запрос", [hh, zp])])

    assert result == [("Запрос", [hh, zp])]
    assert absorbed == []


def test_dedupe_keeps_empty_groups_so_the_caller_can_decide():
    shared = make_vacancy(vacancy_id="100")

    result, absorbed = dedupe([("Первый", [shared]), ("Второй", [shared])])

    assert result == [("Первый", [shared]), ("Второй", [])]
    assert absorbed == []
```

И добавьте в конец файла новые тесты на дедупликацию по городам:

```python
def test_same_company_and_name_across_cities_are_merged_into_one():
    moscow = make_vacancy(vacancy_id="1", company="Roobinium", name="SalesMan", area="Москва")
    kazan = make_vacancy(vacancy_id="2", company="Roobinium", name="SalesMan", area="Казань")

    result, absorbed = dedupe([("Запрос", [moscow, kazan])])

    assert result == [("Запрос", [moscow])]
    assert absorbed == [kazan]


def test_merged_area_lists_up_to_three_cities_in_full():
    a = make_vacancy(vacancy_id="1", company="X", name="Y", area="Москва")
    b = make_vacancy(vacancy_id="2", company="X", name="Y", area="Казань")
    c = make_vacancy(vacancy_id="3", company="X", name="Y", area="Самара")

    result, absorbed = dedupe([("Запрос", [a, b, c])])

    assert result[0][1][0].area == "Москва, Казань, Самара"
    assert len(absorbed) == 2


def test_merged_area_is_capped_at_three_cities_plus_a_counter():
    cities = ["Москва", "Казань", "Самара", "Курск", "Тверь"]
    vacancies = [
        make_vacancy(vacancy_id=str(i), company="X", name="Y", area=city)
        for i, city in enumerate(cities)
    ]

    result, absorbed = dedupe([("Запрос", vacancies)])

    assert result[0][1][0].area == "Москва, Казань, Самара и ещё 2 города"
    assert len(absorbed) == 4


def test_a_single_repeated_city_does_not_get_a_counter():
    a = make_vacancy(vacancy_id="1", company="X", name="Y", area="Москва")
    b = make_vacancy(vacancy_id="2", company="X", name="Y", area="Москва")

    result, absorbed = dedupe([("Запрос", [a, b])])

    assert result[0][1][0].area == "Москва"
    assert absorbed == [b]
```

- [ ] **Step 7: Запустить тесты и убедиться, что падают**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_filters.py -v`
Expected: FAIL — три обновлённых теста падают, потому что `dedupe` пока
возвращает `list[Group]`, а не кортеж (`ValueError: too many values to
unpack` или похожее); четыре новых теста падают тем же образом.

- [ ] **Step 8: Переписать `dedupe` в `hunter/filters.py`**

Добавьте импорты в начало файла:

```python
from __future__ import annotations

from dataclasses import replace

from .models import Vacancy
from .text import plural

Group = tuple[str, list[Vacancy]]
```

Замените всю функцию `dedupe` на:

```python
def _merge_areas(areas: list[str]) -> str:
    """Список городов вакансии-клона для отображения: без повторов и
    пустых значений; при 4+ городах — первые три и "и ещё N"."""
    unique = list(dict.fromkeys(a for a in areas if a))
    if len(unique) <= 1:
        return unique[0] if unique else ""
    if len(unique) <= 3:
        return ", ".join(unique)
    head = ", ".join(unique[:3])
    return f"{head} и ещё {plural(len(unique) - 3, 'город', 'города', 'городов')}"


def dedupe(groups: list[Group]) -> tuple[list[Group], list[Vacancy]]:
    """Схлопывает дубли в два прохода. Первый — по `vacancy.key` (буквально
    одна и та же карточка, попавшая в выдачу нескольких запросов): остаётся
    в первой группе, где встретилась, считая от начала списка queries
    в конфиге. Второй — по `(company, name)`: одна и та же вакансия,
    размещённая отдельными карточками в разных городах (частый случай для
    удалённой работы) или на разных источниках; представитель — первый
    встреченный, его `area` заменяется на объединённый список городов всех
    дублей. Второй элемент результата — плоский список поглощённых вакансий:
    вызывающий обязан пометить их виденными сам, иначе они всплывут по
    отдельности в следующем проходе."""
    seen_ids: set[str] = set()
    seen_content: set[tuple[str, str]] = set()
    kept: list[list[Vacancy]] = [[] for _ in groups]
    areas: dict[tuple[str, str], list[str]] = {}
    absorbed: list[Vacancy] = []

    for group_index, (_, vacancies) in enumerate(groups):
        for vacancy in vacancies:
            if vacancy.key in seen_ids:
                continue
            seen_ids.add(vacancy.key)

            content_key = (vacancy.company, vacancy.name)
            if content_key in seen_content:
                areas[content_key].append(vacancy.area)
                absorbed.append(vacancy)
                continue

            seen_content.add(content_key)
            areas[content_key] = [vacancy.area]
            kept[group_index].append(vacancy)

    result: list[Group] = []
    for group_index, (name, _) in enumerate(groups):
        merged = []
        for vacancy in kept[group_index]:
            city_areas = areas[(vacancy.company, vacancy.name)]
            if len(city_areas) > 1:
                vacancy = replace(vacancy, area=_merge_areas(city_areas))
            merged.append(vacancy)
        result.append((name, merged))

    return result, absorbed
```

- [ ] **Step 9: Запустить тесты и убедиться, что проходят**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_filters.py -v`
Expected: PASS

- [ ] **Step 10: Прогнать весь набор тестов**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: FAIL только в `tests/test_run.py` — там `run_pass` ещё вызывает
`dedupe` по старой сигнатуре (`cleaned = dedupe(cleaned)`), получит кортеж
вместо списка и сломается на первом же обращении к `cleaned` как к списку
групп. Это ожидаемо, чинится в задаче 7.

- [ ] **Step 11: Commit**

```bash
git add hunter/filters.py tests/test_filters.py
git commit -m "$(cat <<'EOF'
feat: merge vacancies re-posted separately per city

dedupe() now also collapses entries with the same (company, name),
regardless of source, and folds their cities into the kept vacancy's
area. Absorbed duplicates come back separately so the caller can mark
them seen without showing them.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: `run_pass` — пометка поглощённых дублей виденными

**Files:**
- Modify: `hunter/run.py`
- Test: `tests/test_run.py`

**Interfaces:**
- Consumes: `filters.dedupe(groups) -> tuple[list[Group], list[Vacancy]]` (Task 6).
- Produces: без изменений публичного контракта `run_pass` — но поглощённые дубликаты теперь попадают в базу как виденные (`sent=False`), не показываясь в дайджесте и не учитываясь в счётчике новых вакансий.

- [ ] **Step 1: Написать падающий тест**

Добавьте в `tests/test_run.py` (после существующих импортов, вверху файла,
рядом с `page_returning`, добавьте вспомогательные функции для сборки
синтетической страницы с несколькими карточками одной вакансии):

```python
import json


def _card(vacancy_id, area, timestamp=1700000000):
    return {
        "vacancyId": vacancy_id,
        "name": "SalesMan",
        "company": {"visibleName": "Roobinium"},
        "area": {"name": area},
        "links": {"desktop": f"https://hh.ru/vacancy/{vacancy_id}"},
        "publicationTime": {"@timestamp": timestamp},
        "compensation": {
            "from": 40000, "to": 300000, "currencyCode": "RUR",
            "gross": False, "mode": "MONTH",
        },
    }


def _page_with(cards):
    payload = json.dumps({"vacancySearchResult": {"vacancies": cards}})
    return f'<template style="display:none" id="HH-Lux-InitialState">{payload}</template>'
```

И тест в конец файла:

```python
def test_vacancy_clones_across_cities_are_merged_and_absorbed_marked_seen():
    store = seeded_store()
    sender = Sender()
    cards = [_card(1, "Москва"), _card(2, "Казань"), _card(3, "Самара")]

    code = run_pass(
        config=CONFIG,
        store=store,
        fetch_page=lambda url: _page_with(cards),
        send=sender,
        now=NOW,
    )

    text = "\n".join(sender.messages)
    assert code == 0
    assert text.count("<a href=") == 1, "три карточки одной вакансии — одна строка в дайджесте"
    assert "Москва, Казань, Самара" in text

    marked = store._conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0]
    assert marked == 3, "поглощённые дубли тоже должны быть помечены виденными"
```

- [ ] **Step 2: Запустить тесты и убедиться, что падают**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_run.py -v`
Expected: FAIL — весь файл падает уже на импорте/первом вызове `run_pass`,
поскольку `cleaned = dedupe(cleaned)` присваивает `cleaned` кортеж
`(list[Group], list[Vacancy])` вместо `list[Group]`, и следующая строка
(`fresh = [(name, store.unseen(vs)) for name, vs in cleaned]`) падает
с `ValueError: too many values to unpack`.

- [ ] **Step 3: Обновить `run_pass` в `hunter/run.py`**

Замените

```python
    cleaned = dedupe(cleaned)

    fresh: list[Group] = [(name, store.unseen(vs)) for name, vs in cleaned]
```

на

```python
    cleaned, duplicates = dedupe(cleaned)
    # Вакансии-клоны в разных городах не должны всплывать по отдельности
    # в следующем проходе — представитель уже несёт объединённый список
    # городов, поглощённые в дайджест не попадают вовсе.
    mark_seen(duplicates, sent=False)

    fresh: list[Group] = [(name, store.unseen(vs)) for name, vs in cleaned]
```

- [ ] **Step 4: Запустить тесты и убедиться, что проходят**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_run.py -v`
Expected: PASS

- [ ] **Step 5: Прогнать весь набор тестов**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS — весь набор снова зелёный.

- [ ] **Step 6: Commit**

```bash
git add hunter/run.py tests/test_run.py
git commit -m "$(cat <<'EOF'
feat: mark city-clone duplicates seen without sending them

Keeps them from resurfacing one city at a time in later passes, while
keeping the shown-after-send invariant for the actual digest content.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Конфиги и README

**Files:**
- Modify: `config.yaml`
- Modify: `config.example.yaml`
- Modify: `README.md`

**Interfaces:**
- Consumes: поведение из задач 1–7 (список `experience`, один блок на ключ, дедупликация по городам).
- Produces: ничего программного — это задача только на документацию и рабочий конфиг пользователя. Проверяется вручную и через `--dry-run`.

- [ ] **Step 1: Схлопнуть пары запросов в `config.yaml`**

Замените содержимое `queries:` в `config.yaml` (сейчас 8 записей, попарно
по опыту на каждое ключевое слово) на 4 записи:

```yaml
queries:
  - name: "AI"
    text: "AI"
    experience: [noExperience, between1And3]

  - name: "Копирайтинг"
    text: "копирайтинг"
    experience: [noExperience, between1And3]

  - name: "SEO"
    text: "SEO"
    experience: [noExperience, between1And3]

  - name: "Контент"
    text: "контент"
    experience: [noExperience, between1And3]
```

- [ ] **Step 2: Обновить `config.example.yaml`**

Замените блок `queries:` на:

```yaml
queries:
  # text уходит на hh как есть: он понимает AND, OR, NOT, кавычки
  # и префикс NAME: для поиска только по заголовку вакансии.
  # experience принимает одно значение или список — список означает один
  # запрос сразу на несколько уровней опыта.
  - name: "Python backend"
    text: "python AND (backend OR бэкенд) NOT стажёр"
    salary: 200000
    experience: between3And6     # noExperience | between1And3 | between3And6 | moreThan6
    work_format: remote          # remote | hybrid | on_site | field_work

  - name: "Data Engineer"
    text: "data engineer"
    area: 113
    experience: [noExperience, between1And3]
```

- [ ] **Step 3: Обновить `README.md`**

В разделе «Настройка запросов» замените пример и текст вокруг него на:

```markdown
## Настройка запросов

`config.yaml`, поле `text` уходит на hh как есть — он понимает `AND`, `OR`,
`NOT`, кавычки и префикс `NAME:` для поиска только по заголовку:

```yaml
queries:
  - name: "Python backend"
    text: "python AND (backend OR бэкенд) NOT стажёр"
    salary: 200000
    experience: [noExperience, between1And3]  # можно и одним значением: between3And6
    work_format: remote          # remote | hybrid | on_site | field_work
    area: 1                      # 1 = Москва, 2 = СПб, 113 = вся Россия
```

`experience` списком — это один запрос сразу на несколько уровней опыта,
а не несколько отдельных: в отчёте они попадают в один блок по имени
запроса, уровень опыта указывается у каждой вакансии отдельно.

Вакансия, размещённая отдельными карточками в нескольких городах (частый
случай для удалённой работы), схлопывается в дайджесте в одну строку —
по совпадению компании и названия, вне зависимости от источника; города
перечисляются через запятую.
```

- [ ] **Step 4: Проверить конфиг через `--dry-run`**

Run:

```powershell
.\.venv\Scripts\python.exe -m hunter.run --dry-run --db .\dryrun.db
```

Expected: процесс завершается кодом 0, конфиг с 4 запросами и списком
`experience` проходит валидацию `load_config` без ошибок. Наличие или
отсутствие вакансий в выводе зависит от реальной сети/выдачи сайтов —
это не проверяется этим шагом, важно только отсутствие `ConfigError`.

- [ ] **Step 5: Commit**

```bash
git add config.yaml config.example.yaml README.md
git commit -m "$(cat <<'EOF'
docs: merge per-experience query pairs, document the list syntax

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```
