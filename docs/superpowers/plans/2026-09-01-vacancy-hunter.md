# Vacancy Hunter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Личный бот раз в 30 минут проверяет hh.ru и zarplata.ru по запросам из конфига и присылает в Телеграм дайджест вакансий, которых не было в прошлый раз.

**Architecture:** One-shot скрипт: один запуск — один проход, процесс завершается. Планировщик задач Windows запускает его каждые 30 минут. Данные берутся парсингом публичной выдачи (JSON встроен в страницу в `<template id="HH-Lux-InitialState">`), потому что поисковый API обоих сайтов отдаёт 403 анонимным запросам. Состояние между запусками — только SQLite.

**Tech Stack:** Python 3.14, `httpx`, `pyyaml`, `pytest`, `sqlite3` из стандартной библиотеки.

**Spec:** `docs/superpowers/specs/2026-09-01-vacancy-hunter-design.md`

## Global Constraints

- Python 3.14 (проверено на машине разработчика). Синтаксис `X | None` и `from __future__ import annotations` доступны.
- Зависимости строго: `httpx`, `pyyaml`, `pytest`. Ничего больше не добавлять. Чтение `.env` пишется руками — `python-dotenv` не ставим.
- Пакет называется `hunter`, точка входа — `python -m hunter.run`.
- **Ни один тест не ходит в сеть.** Фикстуры уже лежат в `tests/fixtures/` и закоммичены.
- Весь текст, видимый пользователю (сообщения об ошибках, логи, дайджест), — на русском.
- Инвариант проекта: `store.mark_seen(..., sent=True)` вызывается **только после** успешной отправки в Телеграм.
- Сообщение в Телеграм: `parse_mode=HTML`, `disable_web_page_preview=True`. Экранируются `&`, `<`, `>`.
- Лимиты: 4096 символов на сообщение, не более 30 вакансий за проход.
- Коммит после каждой задачи. Сообщения коммитов на английском, тело — что и почему.
- **`install_task.ps1` обязан быть сохранён в UTF-8 с BOM.** Windows PowerShell 5.1 читает скрипт без BOM в системной кодировке (здесь cp1251), кириллица в комментариях и строках рассыпается, и файл не парсится вовсе — запуск падает с «The string is missing the terminator». Проверено на этой машине: без BOM `Parser::ParseFile` даёт ошибку, с BOM — `parse OK`.
- **Консоль Windows на этой машине работает в cp1251.** Символы `₽`, `–`, `🔎`, `⚠️`, `🤖` в неё не влезают, и `print()` падает с `UnicodeEncodeError`. Поэтому `main()` первым делом переводит `stdout`/`stderr` в UTF-8 (см. задачу 10). Проверено: `python -c "print('₽')"` падает, `python -X utf8 -c "print('₽')"` работает.

## Фикстуры и их точные показатели

Проверено на реальных файлах — используйте эти числа в тестах как есть:

| Файл | Карточек | Рекламных (`@isAdv`) | Без зарплаты | `totalResults` |
|---|---|---|---|---|
| `tests/fixtures/hh_search_python_msk.html` | 51 | 1 (первая, id `114811554`) | 38 | 3651 |
| `tests/fixtures/hh_search_sorted_with_salary.html` | 50 | 0 | 0 | 87 |
| `tests/fixtures/zarplata_search_python.html` | 1 | 0 | 1 | 1 |

Формы полей в JSON (проверено):

- `vacancyId` — **int**, приводим к строке.
- `company` — объект, нужное поле `visibleName` (есть у всех 50), запасное `name`.
- `links` — `{"desktop": ..., "mobile": ...}`; ссылка ведёт на региональный поддомен вида `tomsk.hh.ru`, это нормально и работает.
- `area` — `{"@id": 1, "name": "Москва", "path": ".113.232.1."}`.
- `publicationTime` — `{"@timestamp": 1788244915, "$": "2026-09-01T09:41:55.502+03:00"}`. Берём `@timestamp` (unix-секунды, UTC) — он однозначен.
- `compensation` — либо `{"noCompensation": {}}`, либо набор из `from`, `to`, `currencyCode`, `gross`, `mode`. Любое из `from`/`to` может отсутствовать.
  - `mode` встречается как `MONTH` (47), `HOUR` (2), `SERVICE` (1). **Почасовую ставку нельзя показывать как месячный оклад.**
  - `currencyCode`: `RUR` (47), `USD` (3).
  - `gross`: `True` (23) / `False` (27).
- `workFormats` — `[{"workFormatsElement": ["REMOTE"]}]`, элементов может быть несколько или ноль.
- `@isAdv` — `True` у рекламных врезок, `None` у остальных.

## File Structure

```
vacancy-hunter/
├── pyproject.toml                 # метаданные, зависимости, настройка pytest
├── config.example.yaml            # образец конфига, коммитится
├── config.yaml                    # рабочий конфиг (в .gitignore)
├── .env                           # TG_BOT_TOKEN, TG_CHAT_ID (в .gitignore)
├── install_task.ps1               # регистрация задачи в Планировщике
├── README.md
├── hunter/
│   ├── __init__.py
│   ├── models.py                  # Vacancy, SearchQuery
│   ├── config.py                  # чтение config.yaml и .env, валидация
│   ├── sources.py                 # сборка URL поиска для hh и zarplata
│   ├── fetch.py                   # HTTP: заголовки, ретраи, распознание блокировки
│   ├── parse.py                   # HTML → list[Vacancy]
│   ├── filters.py                 # отсев рекламы, без зарплаты, дублей; сортировка
│   ├── store.py                   # SQLite: seen + meta
│   ├── digest.py                  # list[Vacancy] → тексты сообщений
│   ├── telegram.py                # sendMessage
│   └── run.py                     # оркестрация прохода, логи, коды выхода
└── tests/
    ├── factories.py               # make_vacancy() — конструктор для тестов
    ├── fixtures/                  # уже закоммичено
    ├── test_models.py
    ├── test_config.py
    ├── test_sources.py
    ├── test_parse.py
    ├── test_filters.py
    ├── test_store.py
    ├── test_digest.py
    ├── test_fetch.py
    ├── test_telegram.py
    └── test_run.py
```

Порядок задач подобран так, чтобы каждая опиралась только на уже написанное. Задачи 1–9 не требуют сети и секретов; сеть появляется в задаче 10, Телеграм — в задаче 11.

---

### Task 1: Скелет проекта и модели данных

**Files:**
- Create: `pyproject.toml`
- Create: `hunter/__init__.py`
- Create: `hunter/models.py`
- Create: `tests/factories.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: ничего.
- Produces: `SearchQuery` и `Vacancy` (dataclasses), свойства `Vacancy.key -> str` и `Vacancy.has_salary -> bool`, тестовая фабрика `make_vacancy(**overrides) -> Vacancy`. Все последующие задачи опираются на эти имена полей.

- [ ] **Step 1: Создать `pyproject.toml`**

```toml
[project]
name = "vacancy-hunter"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["httpx", "pyyaml"]

[project.optional-dependencies]
dev = ["pytest"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

- [ ] **Step 2: Создать виртуальное окружение и поставить зависимости**

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
.\.venv\Scripts\python.exe -m pip install --quiet httpx pyyaml pytest
```

Дальше во всех шагах `pytest` означает `.\.venv\Scripts\python.exe -m pytest`.

- [ ] **Step 3: Написать падающий тест**

Создайте `tests/test_models.py`:

```python
from datetime import datetime, timezone

from hunter.models import SearchQuery, Vacancy


def _vacancy(**over):
    base = dict(
        source="hh",
        vacancy_id="134991226",
        name="Senior Python Developer",
        company="Авито",
        url="https://hh.ru/vacancy/134991226",
        area="Москва",
        published_at=datetime(2026, 9, 1, 9, 41, tzinfo=timezone.utc),
        salary_from=350000,
        salary_to=450000,
        currency="RUR",
        gross=False,
        salary_mode="MONTH",
        work_formats=("REMOTE",),
        is_ad=False,
    )
    base.update(over)
    return Vacancy(**base)


def test_key_includes_source_because_ids_collide_across_sites():
    hh = _vacancy(source="hh", vacancy_id="42")
    zp = _vacancy(source="zarplata", vacancy_id="42")
    assert hh.key == "hh:42"
    assert zp.key == "zarplata:42"
    assert hh.key != zp.key


def test_has_salary_is_true_when_only_one_bound_is_known():
    assert _vacancy(salary_from=100, salary_to=None).has_salary
    assert _vacancy(salary_from=None, salary_to=100).has_salary
    assert not _vacancy(salary_from=None, salary_to=None).has_salary


def test_search_query_defaults_match_the_spec():
    q = SearchQuery(name="Python", text="python")
    assert q.area == 1
    assert q.only_with_salary is True
    assert q.search_period == 1
    assert q.max_pages == 1
    assert q.salary is None
```

- [ ] **Step 4: Запустить тест и убедиться, что он падает**

Run: `pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hunter'`

- [ ] **Step 5: Написать `hunter/__init__.py`**

Пустой файл:

```python
```

- [ ] **Step 6: Написать `hunter/models.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """Один поисковый запрос из config.yaml, уже слитый с defaults."""

    name: str
    text: str
    area: int = 1
    salary: int | None = None
    experience: str | None = None
    work_format: str | None = None
    only_with_salary: bool = True
    search_period: int = 1
    max_pages: int = 1


@dataclass(frozen=True, slots=True)
class Vacancy:
    """Вакансия, разобранная из выдачи. Поля — только то, что нужно дайджесту."""

    source: str
    vacancy_id: str
    name: str
    company: str
    url: str
    area: str
    published_at: datetime
    salary_from: int | None
    salary_to: int | None
    currency: str | None
    gross: bool | None
    salary_mode: str | None
    work_formats: tuple[str, ...]
    is_ad: bool

    @property
    def key(self) -> str:
        """Ключ для базы. Префикс источника обязателен: id у hh и zarplata
        принадлежат разным пространствам и вполне могут совпасть."""
        return f"{self.source}:{self.vacancy_id}"

    @property
    def has_salary(self) -> bool:
        return self.salary_from is not None or self.salary_to is not None
```

- [ ] **Step 7: Запустить тесты и убедиться, что они проходят**

Run: `pytest tests/test_models.py -v`
Expected: PASS, 3 теста.

- [ ] **Step 8: Вынести фабрику в `tests/factories.py`**

Тот же конструктор понадобится ещё в четырёх файлах тестов. Создайте `tests/factories.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

from hunter.models import Vacancy

_DEFAULTS = dict(
    source="hh",
    vacancy_id="134991226",
    name="Senior Python Developer",
    company="Авито",
    url="https://hh.ru/vacancy/134991226",
    area="Москва",
    published_at=datetime(2026, 9, 1, 9, 41, tzinfo=timezone.utc),
    salary_from=350000,
    salary_to=450000,
    currency="RUR",
    gross=False,
    salary_mode="MONTH",
    work_formats=("REMOTE",),
    is_ad=False,
)


def make_vacancy(**overrides) -> Vacancy:
    """Конструктор вакансии для тестов: задавайте только то, что проверяете."""
    return Vacancy(**{**_DEFAULTS, **overrides})
```

Затем в `tests/test_models.py` удалите локальную функцию `_vacancy` и замените её импортом:

```python
from tests.factories import make_vacancy as _vacancy
```

- [ ] **Step 9: Запустить тесты снова**

Run: `pytest -v`
Expected: PASS, 3 теста. Если импорт `tests.factories` не находится — добавьте пустой `tests/__init__.py`.

- [ ] **Step 10: Коммит**

```bash
git add pyproject.toml hunter tests
git commit -m "feat: add project skeleton and data models

Vacancy.key prefixes the id with its source because hh and zarplata
identifiers live in separate namespaces and can collide."
```

---

### Task 2: Чтение и валидация конфигурации

**Files:**
- Create: `hunter/config.py`
- Create: `config.example.yaml`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `SearchQuery` из `hunter.models`.
- Produces: `load_config(path: Path) -> Config`, `load_secrets(path: Path) -> tuple[str, str]`, `Config(sources: tuple[str, ...], queries: tuple[SearchQuery, ...])`, исключение `ConfigError`, константа `KNOWN_SOURCES`.

- [ ] **Step 1: Написать падающий тест**

Создайте `tests/test_config.py`:

```python
import pytest

from hunter.config import ConfigError, load_config, load_secrets


def write(tmp_path, text, name="config.yaml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_merge_into_each_query(tmp_path):
    path = write(tmp_path, """
sources: [hh, zarplata]
defaults:
  area: 113
  salary: 200000
queries:
  - name: "Python backend"
    text: "python"
  - name: "Data"
    text: "data engineer"
    area: 1
""")
    cfg = load_config(path)

    assert cfg.sources == ("hh", "zarplata")
    assert len(cfg.queries) == 2
    assert cfg.queries[0].area == 113
    assert cfg.queries[0].salary == 200000
    assert cfg.queries[1].area == 1, "запрос должен перебивать defaults"
    assert cfg.queries[1].salary == 200000, "остальные defaults остаются"


def test_missing_queries_is_an_error(tmp_path):
    path = write(tmp_path, "sources: [hh]\n")
    with pytest.raises(ConfigError, match="ни одного запроса"):
        load_config(path)


def test_unknown_source_is_rejected_with_the_list_of_valid_ones(tmp_path):
    path = write(tmp_path, """
sources: [hh, linkedin]
queries:
  - name: "x"
    text: "y"
""")
    with pytest.raises(ConfigError, match="linkedin"):
        load_config(path)


def test_typo_in_a_query_field_is_caught(tmp_path):
    path = write(tmp_path, """
queries:
  - name: "x"
    text: "y"
    experiense: between1And3
""")
    with pytest.raises(ConfigError, match="experiense"):
        load_config(path)


def test_query_without_text_is_rejected(tmp_path):
    path = write(tmp_path, """
queries:
  - name: "x"
""")
    with pytest.raises(ConfigError, match="text"):
        load_config(path)


def test_missing_file_gives_a_readable_error(tmp_path):
    with pytest.raises(ConfigError, match="не найден"):
        load_config(tmp_path / "nope.yaml")


def test_secrets_are_read_from_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("TG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TG_CHAT_ID", raising=False)
    path = write(tmp_path, '# comment\nTG_BOT_TOKEN="123:abc"\nTG_CHAT_ID=456\n', name=".env")

    token, chat_id = load_secrets(path)

    assert token == "123:abc"
    assert chat_id == "456"


def test_environment_wins_over_env_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_BOT_TOKEN", "from-env")
    monkeypatch.setenv("TG_CHAT_ID", "999")
    path = write(tmp_path, "TG_BOT_TOKEN=from-file\nTG_CHAT_ID=1\n", name=".env")

    assert load_secrets(path) == ("from-env", "999")


def test_missing_secrets_are_reported(tmp_path, monkeypatch):
    monkeypatch.delenv("TG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TG_CHAT_ID", raising=False)
    with pytest.raises(ConfigError, match="TG_BOT_TOKEN"):
        load_secrets(tmp_path / ".env")
```

- [ ] **Step 2: Запустить тесты и убедиться, что они падают**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hunter.config'`

- [ ] **Step 3: Написать `hunter/config.py`**

```python
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from .models import SearchQuery

KNOWN_SOURCES = ("hh", "zarplata")

_QUERY_FIELDS = {
    "name",
    "text",
    "area",
    "salary",
    "experience",
    "work_format",
    "only_with_salary",
    "search_period",
    "max_pages",
}


class ConfigError(Exception):
    """Конфигурация непригодна. Текст исключения показывается пользователю."""


@dataclass(frozen=True, slots=True)
class Config:
    sources: tuple[str, ...]
    queries: tuple[SearchQuery, ...]


def load_config(path: Path) -> Config:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise ConfigError(f"Файл конфигурации не найден: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Не удалось разобрать {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: ожидался словарь на верхнем уровне")

    sources = tuple(raw.get("sources") or ["hh"])
    unknown = [s for s in sources if s not in KNOWN_SOURCES]
    if unknown:
        raise ConfigError(
            f"Неизвестный источник: {', '.join(unknown)}. "
            f"Доступны: {', '.join(KNOWN_SOURCES)}"
        )

    defaults = raw.get("defaults") or {}
    entries = raw.get("queries") or []
    if not entries:
        raise ConfigError(f"{path}: не задано ни одного запроса (queries)")

    queries = []
    for number, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ConfigError(f"Запрос №{number}: ожидался словарь")
        merged = {**defaults, **entry}
        extra = sorted(set(merged) - _QUERY_FIELDS)
        if extra:
            raise ConfigError(
                f"Запрос №{number}: неизвестные поля: {', '.join(extra)}. "
                f"Допустимы: {', '.join(sorted(_QUERY_FIELDS))}"
            )
        for required in ("name", "text"):
            if not merged.get(required):
                raise ConfigError(f"Запрос №{number}: не заполнено поле {required}")
        queries.append(SearchQuery(**merged))

    return Config(sources=sources, queries=tuple(queries))


def load_secrets(path: Path) -> tuple[str, str]:
    """Читает TG_BOT_TOKEN и TG_CHAT_ID. Переменные окружения имеют приоритет
    над .env — так удобнее подменять их в Планировщике задач."""
    from_file: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            from_file[key.strip()] = value.strip().strip('"').strip("'")

    token = os.environ.get("TG_BOT_TOKEN") or from_file.get("TG_BOT_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID") or from_file.get("TG_CHAT_ID")
    missing = [
        name
        for name, value in (("TG_BOT_TOKEN", token), ("TG_CHAT_ID", chat_id))
        if not value
    ]
    if missing:
        raise ConfigError(
            f"Не заданы {', '.join(missing)}. Укажите их в файле {path} "
            f"или в переменных окружения."
        )
    return token, chat_id
```

- [ ] **Step 4: Запустить тесты и убедиться, что они проходят**

Run: `pytest tests/test_config.py -v`
Expected: PASS, 9 тестов.

- [ ] **Step 5: Создать `config.example.yaml`**

```yaml
# Скопируйте в config.yaml и правьте под себя.

sources: [hh, zarplata]

defaults:              # применяются к каждому запросу, если тот не переопределит
  area: 1              # 1 = Москва, 2 = Санкт-Петербург, 113 = вся Россия
  only_with_salary: true
  search_period: 1     # смотреть выдачу за сутки — страховка от пропусков
  max_pages: 1         # 50 вакансий на страницу

queries:
  # text уходит на hh как есть: он понимает AND, OR, NOT, кавычки
  # и префикс NAME: для поиска только по заголовку вакансии.
  - name: "Python backend"
    text: "python AND (backend OR бэкенд) NOT стажёр"
    salary: 200000
    experience: between3And6     # noExperience | between1And3 | between3And6 | moreThan6
    work_format: remote          # remote | hybrid | on_site | field_work

  - name: "Data Engineer"
    text: "data engineer"
    area: 113
```

- [ ] **Step 6: Коммит**

```bash
git add hunter/config.py config.example.yaml tests/test_config.py
git commit -m "feat: load and validate config.yaml and .env

Unknown query fields are rejected rather than ignored: a typo like
'experiense' would otherwise silently drop the filter."
```

---

### Task 3: Сборка URL поиска

**Files:**
- Create: `hunter/sources.py`
- Test: `tests/test_sources.py`

**Interfaces:**
- Consumes: `SearchQuery` из `hunter.models`.
- Produces: `SOURCES: dict[str, Source]`, `Source(name, base_url, search_path)`, `build_url(source: str, query: SearchQuery, page: int = 0) -> str`, исключение `UnknownSource`.

Различие между hh и zarplata сводится к домену и пути поиска: `https://hh.ru/search/vacancy` против `https://www.zarplata.ru/vacancies`. Имена параметров общие — движок один и тот же.

- [ ] **Step 1: Написать падающий тест**

Создайте `tests/test_sources.py`:

```python
from urllib.parse import parse_qs, urlsplit

import pytest

from hunter.models import SearchQuery
from hunter.sources import UnknownSource, build_url


def params(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def test_hh_url_uses_the_search_path_and_sorts_by_freshness():
    url = build_url("hh", SearchQuery(name="q", text="python"))

    parts = urlsplit(url)
    assert parts.netloc == "hh.ru"
    assert parts.path == "/search/vacancy"
    assert params(url)["order_by"] == "publication_time"


def test_zarplata_url_uses_its_own_path_but_the_same_parameters():
    url = build_url("zarplata", SearchQuery(name="q", text="python"))

    parts = urlsplit(url)
    assert parts.netloc == "www.zarplata.ru"
    assert parts.path == "/vacancies"
    assert params(url)["text"] == "python"
    assert params(url)["order_by"] == "publication_time"


def test_query_text_survives_spaces_and_cyrillic():
    url = build_url("hh", SearchQuery(name="q", text="python AND бэкенд"))
    assert params(url)["text"] == "python AND бэкенд"


def test_optional_filters_are_omitted_when_unset():
    url = build_url("hh", SearchQuery(name="q", text="python", only_with_salary=False))
    got = params(url)

    assert "salary" not in got
    assert "experience" not in got
    assert "work_format" not in got
    assert "only_with_salary" not in got
    assert "page" not in got


def test_optional_filters_are_included_when_set():
    url = build_url(
        "hh",
        SearchQuery(
            name="q",
            text="python",
            salary=200000,
            experience="between3And6",
            work_format="remote",
            search_period=3,
        ),
        page=2,
    )
    got = params(url)

    assert got["salary"] == "200000"
    assert got["experience"] == "between3And6"
    assert got["work_format"] == "REMOTE", "hh ждёт формат в верхнем регистре"
    assert got["only_with_salary"] == "true"
    assert got["search_period"] == "3"
    assert got["page"] == "2"


def test_unknown_source_is_rejected():
    with pytest.raises(UnknownSource, match="linkedin"):
        build_url("linkedin", SearchQuery(name="q", text="python"))
```

- [ ] **Step 2: Запустить тесты и убедиться, что они падают**

Run: `pytest tests/test_sources.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hunter.sources'`

- [ ] **Step 3: Написать `hunter/sources.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from .models import SearchQuery


@dataclass(frozen=True, slots=True)
class Source:
    name: str
    base_url: str
    search_path: str


# Оба сайта работают на одном движке: имена параметров совпадают,
# различаются только домен и путь поиска.
SOURCES: dict[str, Source] = {
    "hh": Source("hh", "https://hh.ru", "/search/vacancy"),
    "zarplata": Source("zarplata", "https://www.zarplata.ru", "/vacancies"),
}


class UnknownSource(Exception):
    pass


def build_url(source: str, query: SearchQuery, page: int = 0) -> str:
    try:
        src = SOURCES[source]
    except KeyError as exc:
        raise UnknownSource(
            f"Неизвестный источник: {source}. Доступны: {', '.join(SOURCES)}"
        ) from exc

    params: list[tuple[str, object]] = [
        ("text", query.text),
        ("area", query.area),
        ("order_by", "publication_time"),
        ("search_period", query.search_period),
        ("items_on_page", 50),
    ]
    if query.only_with_salary:
        params.append(("only_with_salary", "true"))
    if query.salary is not None:
        params.append(("salary", query.salary))
    if query.experience:
        params.append(("experience", query.experience))
    if query.work_format:
        params.append(("work_format", query.work_format.upper()))
    if page:
        params.append(("page", page))

    return f"{src.base_url}{src.search_path}?{urlencode(params)}"
```

- [ ] **Step 4: Запустить тесты и убедиться, что они проходят**

Run: `pytest tests/test_sources.py -v`
Expected: PASS, 6 тестов.

- [ ] **Step 5: Коммит**

```bash
git add hunter/sources.py tests/test_sources.py
git commit -m "feat: build search URLs for hh and zarplata

Both sites run the same engine, so only the domain and search path
differ; the query parameters are shared."
```

---

### Task 4: Разбор страницы выдачи

Это ядро проекта и единственный модуль, который сломается при редизайне сайта.

**Files:**
- Create: `hunter/parse.py`
- Test: `tests/test_parse.py`

**Interfaces:**
- Consumes: `Vacancy` из `hunter.models`.
- Produces: `extract_state(page_html: str) -> dict`, `parse_search_page(page_html: str, source: str) -> list[Vacancy]`, исключение `ParseError`.

- [ ] **Step 1: Написать падающий тест**

Обратите внимание на атрибут `style` перед `id` в теге `<template>` — регулярное выражение обязано его пережить. Создайте `tests/test_parse.py`:

```python
from datetime import timezone
from pathlib import Path

import pytest

from hunter.parse import ParseError, extract_state, parse_search_page

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parses_every_card_from_the_real_page():
    vacancies = parse_search_page(fixture("hh_search_python_msk.html"), "hh")

    assert len(vacancies) == 51
    assert sum(v.is_ad for v in vacancies) == 1
    assert sum(not v.has_salary for v in vacancies) == 38


def test_advertising_card_is_flagged_not_dropped():
    vacancies = parse_search_page(fixture("hh_search_python_msk.html"), "hh")

    first = vacancies[0]
    assert first.vacancy_id == "114811554"
    assert first.is_ad is True, "отсевом рекламы занимается filters, не parse"


def test_fields_are_filled_from_the_page():
    vacancies = parse_search_page(fixture("hh_search_sorted_with_salary.html"), "hh")
    v = vacancies[0]

    assert v.source == "hh"
    assert v.vacancy_id.isdigit()
    assert v.name
    assert v.company
    assert v.url.startswith("https://")
    assert "/vacancy/" in v.url
    assert v.published_at.tzinfo is not None
    assert v.published_at.year == 2026


def test_salary_bounds_currency_and_mode_are_captured():
    vacancies = parse_search_page(fixture("hh_search_sorted_with_salary.html"), "hh")

    assert all(v.has_salary for v in vacancies), "страница снята с only_with_salary"
    assert {v.currency for v in vacancies} == {"RUR", "USD"}
    assert {v.salary_mode for v in vacancies} == {"MONTH", "HOUR", "SERVICE"}
    assert any(v.gross is True for v in vacancies)
    assert any(v.gross is False for v in vacancies)


def test_missing_compensation_becomes_empty_bounds():
    vacancies = parse_search_page(fixture("zarplata_search_python.html"), "zarplata")

    assert len(vacancies) == 1
    v = vacancies[0]
    assert v.source == "zarplata"
    assert v.salary_from is None
    assert v.salary_to is None
    assert v.has_salary is False


def test_work_formats_are_flattened_into_a_tuple():
    vacancies = parse_search_page(fixture("hh_search_sorted_with_salary.html"), "hh")
    formats = {f for v in vacancies for f in v.work_formats}

    assert "REMOTE" in formats
    assert "ON_SITE" in formats
    assert all(isinstance(v.work_formats, tuple) for v in vacancies)


def test_page_without_the_state_template_raises_a_loud_error():
    with pytest.raises(ParseError, match="HH-Lux-InitialState"):
        parse_search_page("<html><body>Ничего интересного</body></html>", "hh")


def test_broken_json_in_the_state_raises():
    with pytest.raises(ParseError, match="JSON"):
        extract_state('<template style="display:none" id="HH-Lux-InitialState">{oops</template>')


def test_state_without_search_result_raises():
    with pytest.raises(ParseError, match="vacancySearchResult"):
        parse_search_page(
            '<template style="display:none" id="HH-Lux-InitialState">{"other": 1}</template>',
            "hh",
        )
```

- [ ] **Step 2: Запустить тесты и убедиться, что они падают**

Run: `pytest tests/test_parse.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hunter.parse'`

- [ ] **Step 3: Написать `hunter/parse.py`**

```python
from __future__ import annotations

import html as html_module
import json
import re
from datetime import datetime, timezone

from .models import Vacancy

# У тега есть атрибут style до id, поэтому [^>]* обязателен с обеих сторон.
_STATE_RE = re.compile(
    r'<template[^>]*id="HH-Lux-InitialState"[^>]*>(.*?)</template>',
    re.DOTALL,
)

_EPOCH = datetime.fromtimestamp(0, tz=timezone.utc)


class ParseError(Exception):
    """Страница не разобралась. Скорее всего сайт сменил вёрстку —
    это должно быть громкой ошибкой, а не тихим пустым списком."""


def extract_state(page_html: str) -> dict:
    match = _STATE_RE.search(page_html)
    if not match:
        raise ParseError(
            "В странице нет HH-Lux-InitialState — вёрстка изменилась "
            "или пришла заглушка вместо выдачи"
        )
    try:
        return json.loads(html_module.unescape(match.group(1)))
    except json.JSONDecodeError as exc:
        raise ParseError(f"HH-Lux-InitialState не разбирается как JSON: {exc}") from exc


def parse_search_page(page_html: str, source: str) -> list[Vacancy]:
    state = extract_state(page_html)
    result = state.get("vacancySearchResult")
    if not isinstance(result, dict) or "vacancies" not in result:
        raise ParseError("В состоянии страницы нет vacancySearchResult.vacancies")
    return [_to_vacancy(raw, source) for raw in result["vacancies"]]


def _to_vacancy(raw: dict, source: str) -> Vacancy:
    compensation = raw.get("compensation") or {}
    company = raw.get("company") or {}
    area = raw.get("area") or {}
    links = raw.get("links") or {}
    timestamp = (raw.get("publicationTime") or {}).get("@timestamp")

    return Vacancy(
        source=source,
        vacancy_id=str(raw["vacancyId"]),
        name=raw.get("name") or "(без названия)",
        company=(
            company.get("visibleName")
            or company.get("name")
            or "(работодатель не указан)"
        ),
        url=links.get("desktop") or "",
        area=area.get("name") or "",
        published_at=(
            datetime.fromtimestamp(timestamp, tz=timezone.utc)
            if timestamp
            else _EPOCH
        ),
        salary_from=compensation.get("from"),
        salary_to=compensation.get("to"),
        currency=compensation.get("currencyCode"),
        gross=compensation.get("gross"),
        salary_mode=compensation.get("mode"),
        work_formats=tuple(
            element
            for block in (raw.get("workFormats") or [])
            for element in (block.get("workFormatsElement") or [])
        ),
        is_ad=bool(raw.get("@isAdv")),
    )
```

- [ ] **Step 4: Запустить тесты и убедиться, что они проходят**

Run: `pytest tests/test_parse.py -v`
Expected: PASS, 9 тестов.

- [ ] **Step 5: Коммит**

```bash
git add hunter/parse.py tests/test_parse.py
git commit -m "feat: parse the embedded search state into Vacancy objects

A missing HH-Lux-InitialState raises instead of returning an empty list:
the worst failure for this bot is going quiet after a redesign."
```

---

### Task 5: Локальные фильтры

**Files:**
- Create: `hunter/filters.py`
- Test: `tests/test_filters.py`

**Interfaces:**
- Consumes: `Vacancy` из `hunter.models`.
- Produces: `Group = tuple[str, list[Vacancy]]`, `drop_ads(vacancies) -> list[Vacancy]`, `drop_without_salary(vacancies) -> list[Vacancy]`, `sort_by_freshness(vacancies) -> list[Vacancy]`, `dedupe(groups: list[Group]) -> list[Group]`.

- [ ] **Step 1: Написать падающий тест**

Создайте `tests/test_filters.py`:

```python
from datetime import datetime, timezone

from hunter.filters import dedupe, drop_ads, drop_without_salary, sort_by_freshness
from tests.factories import make_vacancy


def at(day):
    return datetime(2026, 9, day, 12, 0, tzinfo=timezone.utc)


def test_ads_are_dropped():
    kept = make_vacancy(vacancy_id="1", is_ad=False)
    ad = make_vacancy(vacancy_id="2", is_ad=True)

    assert drop_ads([kept, ad]) == [kept]


def test_vacancies_without_salary_are_dropped():
    with_salary = make_vacancy(vacancy_id="1")
    without = make_vacancy(vacancy_id="2", salary_from=None, salary_to=None)

    assert drop_without_salary([with_salary, without]) == [with_salary]


def test_sorted_from_freshest_to_oldest():
    old = make_vacancy(vacancy_id="1", published_at=at(1))
    new = make_vacancy(vacancy_id="2", published_at=at(3))
    mid = make_vacancy(vacancy_id="3", published_at=at(2))

    assert sort_by_freshness([old, new, mid]) == [new, mid, old]


def test_duplicate_stays_in_the_first_group_it_appeared_in():
    shared = make_vacancy(vacancy_id="100")
    only_second = make_vacancy(vacancy_id="200")

    result = dedupe([("Первый", [shared]), ("Второй", [shared, only_second])])

    assert result == [("Первый", [shared]), ("Второй", [only_second])]


def test_same_id_from_different_sources_is_not_a_duplicate():
    hh = make_vacancy(source="hh", vacancy_id="42")
    zp = make_vacancy(source="zarplata", vacancy_id="42")

    result = dedupe([("Запрос", [hh, zp])])

    assert result == [("Запрос", [hh, zp])]


def test_dedupe_keeps_empty_groups_so_the_caller_can_decide():
    shared = make_vacancy(vacancy_id="100")

    result = dedupe([("Первый", [shared]), ("Второй", [shared])])

    assert result == [("Первый", [shared]), ("Второй", [])]
```

- [ ] **Step 2: Запустить тесты и убедиться, что они падают**

Run: `pytest tests/test_filters.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hunter.filters'`

- [ ] **Step 3: Написать `hunter/filters.py`**

```python
from __future__ import annotations

from .models import Vacancy

Group = tuple[str, list[Vacancy]]


def drop_ads(vacancies: list[Vacancy]) -> list[Vacancy]:
    """Рекламные врезки помечены свежей датой публикации, но могут быть
    созданы годы назад — в дайджесте им не место."""
    return [v for v in vacancies if not v.is_ad]


def drop_without_salary(vacancies: list[Vacancy]) -> list[Vacancy]:
    """Страховка: основной отсев делает сам hh по only_with_salary."""
    return [v for v in vacancies if v.has_salary]


def sort_by_freshness(vacancies: list[Vacancy]) -> list[Vacancy]:
    return sorted(vacancies, key=lambda v: v.published_at, reverse=True)


def dedupe(groups: list[Group]) -> list[Group]:
    """Одна вакансия часто попадает в выдачу по нескольким запросам.
    Оставляем её в первой группе, где встретилась, считая от начала
    списка queries в конфиге."""
    seen: set[str] = set()
    result: list[Group] = []
    for name, vacancies in groups:
        kept = []
        for vacancy in vacancies:
            if vacancy.key in seen:
                continue
            seen.add(vacancy.key)
            kept.append(vacancy)
        result.append((name, kept))
    return result
```

- [ ] **Step 4: Запустить тесты и убедиться, что они проходят**

Run: `pytest tests/test_filters.py -v`
Expected: PASS, 6 тестов.

- [ ] **Step 5: Коммит**

```bash
git add hunter/filters.py tests/test_filters.py
git commit -m "feat: add local filters for ads, missing salary and duplicates"
```

---

### Task 6: Хранилище состояния

**Files:**
- Create: `hunter/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `Vacancy` из `hunter.models`.
- Produces: класс `Store` с методами `is_empty() -> bool`, `unseen(vacancies) -> list[Vacancy]`, `mark_seen(vacancies, *, sent: bool) -> None`, `purge_older_than(days: int) -> int`, `get_meta(key) -> str | None`, `set_meta(key, value) -> None`, `close()`, поддержка `with`. Ключи `meta`: `last_sent_at`, `last_warning_at`.

- [ ] **Step 1: Написать падающий тест**

Создайте `tests/test_store.py`:

```python
from datetime import datetime, timedelta, timezone

from hunter.store import Store
from tests.factories import make_vacancy


def test_new_database_is_empty():
    with Store(":memory:") as store:
        assert store.is_empty() is True


def test_marked_vacancies_are_no_longer_unseen():
    a = make_vacancy(vacancy_id="1")
    b = make_vacancy(vacancy_id="2")

    with Store(":memory:") as store:
        assert store.unseen([a, b]) == [a, b]
        store.mark_seen([a], sent=True)

        assert store.unseen([a, b]) == [b]
        assert store.is_empty() is False


def test_marking_twice_does_not_fail():
    a = make_vacancy(vacancy_id="1")

    with Store(":memory:") as store:
        store.mark_seen([a], sent=True)
        store.mark_seen([a], sent=True)

        assert store.unseen([a]) == []


def test_state_survives_reopening_the_file(tmp_path):
    path = tmp_path / "state.db"
    a = make_vacancy(vacancy_id="1")

    with Store(path) as store:
        store.mark_seen([a], sent=True)

    with Store(path) as store:
        assert store.unseen([a]) == []


def test_old_records_are_purged_but_recent_ones_stay(tmp_path):
    old = make_vacancy(vacancy_id="old")
    fresh = make_vacancy(vacancy_id="fresh")

    with Store(tmp_path / "state.db") as store:
        store.mark_seen([old, fresh], sent=True)
        ancient = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        store._conn.execute(
            "UPDATE seen SET first_seen_at = ? WHERE key = ?", (ancient, old.key)
        )
        store._conn.commit()

        removed = store.purge_older_than(90)

        assert removed == 1
        assert store.unseen([old]) == [old], "запись удалена, вакансия снова новая"
        assert store.unseen([fresh]) == []


def test_meta_round_trips_and_overwrites():
    with Store(":memory:") as store:
        assert store.get_meta("last_sent_at") is None

        store.set_meta("last_sent_at", "2026-09-01T10:00:00+00:00")
        assert store.get_meta("last_sent_at") == "2026-09-01T10:00:00+00:00"

        store.set_meta("last_sent_at", "2026-09-02T10:00:00+00:00")
        assert store.get_meta("last_sent_at") == "2026-09-02T10:00:00+00:00"
```

- [ ] **Step 2: Запустить тесты и убедиться, что они падают**

Run: `pytest tests/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hunter.store'`

- [ ] **Step 3: Написать `hunter/store.py`**

```python
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Vacancy

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
  key           TEXT PRIMARY KEY,
  first_seen_at TEXT NOT NULL,
  sent_at       TEXT
);
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Store:
    """Состояние между запусками: что уже отправляли и служебные отметки времени."""

    def __init__(self, path: Path | str) -> None:
        self._conn = sqlite3.connect(str(path))
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def is_empty(self) -> bool:
        return self._conn.execute("SELECT 1 FROM seen LIMIT 1").fetchone() is None

    def unseen(self, vacancies: list[Vacancy]) -> list[Vacancy]:
        known = {row[0] for row in self._conn.execute("SELECT key FROM seen")}
        return [v for v in vacancies if v.key not in known]

    def mark_seen(self, vacancies: list[Vacancy], *, sent: bool) -> None:
        now = _now().isoformat()
        self._conn.executemany(
            "INSERT INTO seen (key, first_seen_at, sent_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "sent_at = COALESCE(seen.sent_at, excluded.sent_at)",
            [(v.key, now, now if sent else None) for v in vacancies],
        )
        self._conn.commit()

    def purge_older_than(self, days: int) -> int:
        """Вакансия, снятая и переопубликованная спустя квартал, считается новой,
        а база не растёт бесконечно."""
        cutoff = (_now() - timedelta(days=days)).isoformat()
        cursor = self._conn.execute("DELETE FROM seen WHERE first_seen_at < ?", (cutoff,))
        self._conn.commit()
        return cursor.rowcount

    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        self._conn.commit()
```

- [ ] **Step 4: Запустить тесты и убедиться, что они проходят**

Run: `pytest tests/test_store.py -v`
Expected: PASS, 6 тестов.

- [ ] **Step 5: Коммит**

```bash
git add hunter/store.py tests/test_store.py
git commit -m "feat: add SQLite store for seen vacancies and metadata

The meta table backs the once-a-day warning limit and the daily
heartbeat; both need timestamps that outlive the process."
```

---

### Task 7: Формирование дайджеста

**Files:**
- Create: `hunter/digest.py`
- Test: `tests/test_digest.py`

**Interfaces:**
- Consumes: `Vacancy` из `hunter.models`, `Group` из `hunter.filters`.
- Produces: `render(groups: list[Group], now: datetime, warnings: tuple[str, ...] = ()) -> list[str]`, `format_salary(v: Vacancy) -> str`, `escape(text: str) -> str`, константы `MAX_MESSAGE_CHARS = 4096`, `MAX_VACANCIES = 30`.

- [ ] **Step 1: Написать падающий тест**

Создайте `tests/test_digest.py`:

```python
from datetime import datetime, timezone

from hunter.digest import (
    MAX_MESSAGE_CHARS,
    MAX_VACANCIES,
    escape,
    format_salary,
    render,
)
from tests.factories import make_vacancy

NOW = datetime(2026, 9, 1, 14, 30, tzinfo=timezone.utc)


def test_salary_range_is_formatted_with_spaces_and_currency():
    v = make_vacancy(salary_from=350000, salary_to=450000, currency="RUR", gross=False)
    assert format_salary(v) == "350 000–450 000 ₽ на руки"


def test_open_ended_ranges_get_a_preposition():
    lower = make_vacancy(salary_from=250000, salary_to=None, gross=True)
    upper = make_vacancy(salary_from=None, salary_to=400000, gross=None)

    assert format_salary(lower) == "от 250 000 ₽ до вычета"
    assert format_salary(upper) == "до 400 000 ₽"


def test_hourly_rate_is_not_shown_as_a_monthly_salary():
    v = make_vacancy(salary_from=1500, salary_to=None, salary_mode="HOUR", gross=False)
    assert "в час" in format_salary(v)


def test_foreign_currency_keeps_its_symbol():
    v = make_vacancy(salary_from=5000, salary_to=None, currency="USD", gross=None)
    assert format_salary(v) == "от 5 000 $"


def test_missing_salary_says_so():
    assert format_salary(make_vacancy(salary_from=None, salary_to=None)) == "з/п не указана"


def test_html_special_characters_are_escaped():
    assert escape("R&D <script>") == "R&amp;D &lt;script&gt;"


def test_digest_has_a_header_groups_and_links():
    v = make_vacancy(name="Senior Python Developer", company="Авито", area="Москва")

    messages = render([("Python backend", [v])], NOW)

    assert len(messages) == 1
    text = messages[0]
    assert "1 новая вакансия" in text
    assert "1 сен, 14:30" in text
    assert "<b>Python backend</b>" in text
    assert f'<a href="{v.url}">Senior Python Developer</a>' in text
    assert "Авито" in text
    assert "350 000–450 000 ₽ на руки" in text
    assert "Москва, удалённо" in text


def test_plural_forms_follow_russian_grammar():
    def header(count):
        vs = [make_vacancy(vacancy_id=str(i)) for i in range(count)]
        return render([("q", vs)], NOW)[0].splitlines()[0]

    assert "1 новая вакансия" in header(1)
    assert "3 новые вакансии" in header(3)
    assert "12 новых вакансий" in header(12)


def test_empty_input_produces_no_messages():
    assert render([], NOW) == []
    assert render([("q", [])], NOW) == []


def test_warnings_are_sent_even_without_vacancies():
    messages = render([], NOW, warnings=("запрос «Python» не выполнен",))

    assert len(messages) == 1
    assert "⚠️" in messages[0]
    assert "не выполнен" in messages[0]


def test_more_than_thirty_vacancies_are_truncated_with_a_tail():
    vs = [make_vacancy(vacancy_id=str(i)) for i in range(45)]

    text = "\n".join(render([("q", vs)], NOW))

    assert text.count("<a href=") == MAX_VACANCIES
    assert "и ещё 15" in text


def test_long_digest_is_split_into_messages_within_the_limit():
    vs = [
        make_vacancy(vacancy_id=str(i), name="Очень длинное название вакансии " * 6)
        for i in range(MAX_VACANCIES)
    ]

    messages = render([("q", vs)], NOW)

    assert len(messages) > 1
    assert all(len(m) <= MAX_MESSAGE_CHARS for m in messages)


def test_groups_keep_the_order_they_were_given_in():
    a = make_vacancy(vacancy_id="1", name="Первая")
    b = make_vacancy(vacancy_id="2", name="Вторая")

    text = render([("Группа Б", [b]), ("Группа А", [a])], NOW)[0]

    assert text.index("Группа Б") < text.index("Группа А")
```

- [ ] **Step 2: Запустить тесты и убедиться, что они падают**

Run: `pytest tests/test_digest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hunter.digest'`

- [ ] **Step 3: Написать `hunter/digest.py`**

```python
from __future__ import annotations

from datetime import datetime

from .filters import Group
from .models import Vacancy

MAX_MESSAGE_CHARS = 4096
MAX_VACANCIES = 30

_MONTHS = (
    "янв", "фев", "мар", "апр", "мая", "июн",
    "июл", "авг", "сен", "окт", "ноя", "дек",
)
_CURRENCY = {
    "RUR": "₽", "RUB": "₽", "USD": "$", "EUR": "€",
    "KZT": "₸", "BYR": "Br", "BYN": "Br", "UAH": "₴",
}
# Почасовую ставку нельзя показывать как месячный оклад — разница в сто раз.
_MODE = {
    "HOUR": " в час",
    "DAY": " в день",
    "WEEK": " в неделю",
    "SERVICE": " за услугу",
    "PROJECT": " за проект",
}
_WORK_FORMAT = {
    "REMOTE": "удалённо",
    "HYBRID": "гибрид",
    "ON_SITE": "в офисе",
    "FIELD_WORK": "разъездная",
}


def escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _thousands(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def format_salary(vacancy: Vacancy) -> str:
    if not vacancy.has_salary:
        return "з/п не указана"

    symbol = _CURRENCY.get(vacancy.currency or "", vacancy.currency or "")
    if vacancy.salary_from is not None and vacancy.salary_to is not None:
        amount = f"{_thousands(vacancy.salary_from)}–{_thousands(vacancy.salary_to)}"
    elif vacancy.salary_from is not None:
        amount = f"от {_thousands(vacancy.salary_from)}"
    else:
        amount = f"до {_thousands(vacancy.salary_to)}"

    period = _MODE.get(vacancy.salary_mode or "", "")
    if vacancy.gross is None:
        taxes = ""
    else:
        taxes = " до вычета" if vacancy.gross else " на руки"

    return f"{amount} {symbol}{period}{taxes}".strip()


def _plural_vacancies(count: int) -> str:
    tail = count % 100
    if 11 <= tail <= 14:
        adjective, noun = "новых", "вакансий"
    elif count % 10 == 1:
        adjective, noun = "новая", "вакансия"
    elif count % 10 in (2, 3, 4):
        adjective, noun = "новые", "вакансии"
    else:
        adjective, noun = "новых", "вакансий"
    return f"{count} {adjective} {noun}"


def _format_vacancy(vacancy: Vacancy) -> str:
    place_parts = [vacancy.area] if vacancy.area else []
    place_parts += [
        _WORK_FORMAT[f] for f in vacancy.work_formats if f in _WORK_FORMAT
    ]
    details = [vacancy.company, format_salary(vacancy)]
    if place_parts:
        details.append(", ".join(place_parts))

    return (
        f'• <a href="{escape(vacancy.url)}">{escape(vacancy.name)}</a>\n'
        f'  {escape(" · ".join(details))}'
    )


def _truncate(groups: list[Group], limit: int) -> tuple[list[Group], int]:
    kept: list[Group] = []
    used = 0
    for name, vacancies in groups:
        if used >= limit:
            break
        portion = vacancies[: limit - used]
        used += len(portion)
        kept.append((name, portion))
    remaining = sum(len(v) for _, v in groups) - used
    return kept, remaining


def _split_oversized(block: str, limit: int) -> list[str]:
    if len(block) <= limit:
        return [block]
    pieces: list[str] = []
    current = ""
    for line in block.split("\n"):
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            pieces.append(current)
        current = line[:limit]
    if current:
        pieces.append(current)
    return pieces


def _pack(blocks: list[str], limit: int) -> list[str]:
    messages: list[str] = []
    current = ""
    for block in blocks:
        for piece in _split_oversized(block, limit):
            candidate = piece if not current else f"{current}\n\n{piece}"
            if len(candidate) <= limit:
                current = candidate
                continue
            if current:
                messages.append(current)
            current = piece
    if current:
        messages.append(current)
    return messages


def render(
    groups: list[Group],
    now: datetime,
    warnings: tuple[str, ...] = (),
) -> list[str]:
    """Собирает дайджест. Возвращает список сообщений: длинный дайджест
    режется по границе группы, а слишком длинная группа — по границе строки."""
    filled = [(name, vacancies) for name, vacancies in groups if vacancies]
    total = sum(len(v) for _, v in filled)
    if not total and not warnings:
        return []

    blocks: list[str] = []
    if total:
        header = (
            f"🔎 {_plural_vacancies(total)} · "
            f"{now.day} {_MONTHS[now.month - 1]}, {now:%H:%M}"
        )
        blocks.append(header)
        shown, remaining = _truncate(filled, MAX_VACANCIES)
        for name, vacancies in shown:
            body = "\n".join(_format_vacancy(v) for v in vacancies)
            blocks.append(f"<b>{escape(name)}</b>\n{body}")
        if remaining:
            blocks.append(f"…и ещё {remaining} — сузьте запрос")

    for warning in warnings:
        blocks.append(f"⚠️ {escape(warning)}")

    return _pack(blocks, MAX_MESSAGE_CHARS)
```

- [ ] **Step 4: Запустить тесты и убедиться, что они проходят**

Run: `pytest tests/test_digest.py -v`
Expected: PASS, 13 тестов.

- [ ] **Step 5: Коммит**

```bash
git add hunter/digest.py tests/test_digest.py
git commit -m "feat: render the Telegram digest

Salary formatting carries the payment period: hh returns HOUR and
SERVICE rates alongside monthly ones, and showing an hourly rate as a
monthly salary would be off by two orders of magnitude."
```

---

### Task 8: HTTP-слой

**Files:**
- Create: `hunter/fetch.py`
- Test: `tests/test_fetch.py`

**Interfaces:**
- Consumes: ничего из проекта.
- Produces: `get_html(url, *, client=None, sleep=time.sleep, timeout=30.0) -> str`, исключения `FetchError` и `Blocked(FetchError)`, константы `HEADERS`, `RETRY_DELAYS = (2.0, 5.0, 12.0)`.

Тесты используют `httpx.MockTransport` — настоящий клиент `httpx` с подменённым транспортом, сеть не задействована.

- [ ] **Step 1: Написать падающий тест**

Создайте `tests/test_fetch.py`:

```python
import httpx
import pytest

from hunter.fetch import RETRY_DELAYS, Blocked, FetchError, get_html

URL = "https://hh.ru/search/vacancy?text=python"


def client_returning(*responses):
    """Клиент, отдающий заготовленные ответы по очереди."""
    queue = list(responses)
    calls = []

    def handler(request):
        calls.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return client, calls


def test_successful_response_returns_the_body():
    client, calls = client_returning(httpx.Response(200, text="<html>ok</html>"))

    assert get_html(URL, client=client, sleep=lambda _: None) == "<html>ok</html>"
    assert len(calls) == 1


def test_server_error_is_retried_then_succeeds():
    client, calls = client_returning(
        httpx.Response(503),
        httpx.Response(500),
        httpx.Response(200, text="ok"),
    )
    slept = []

    assert get_html(URL, client=client, sleep=slept.append) == "ok"
    assert len(calls) == 3
    assert slept == list(RETRY_DELAYS[:2]), "паузы растут между попытками"


def test_network_error_is_retried():
    client, calls = client_returning(
        httpx.ConnectError("boom"),
        httpx.Response(200, text="ok"),
    )

    assert get_html(URL, client=client, sleep=lambda _: None) == "ok"
    assert len(calls) == 2


def test_gives_up_after_all_retries():
    client, calls = client_returning(*[httpx.Response(503)] * 4)

    with pytest.raises(FetchError):
        get_html(URL, client=client, sleep=lambda _: None)
    assert len(calls) == 4, "первая попытка плюс три ретрая"


def test_forbidden_is_not_retried():
    client, calls = client_returning(httpx.Response(403), httpx.Response(200, text="ok"))

    with pytest.raises(Blocked, match="403"):
        get_html(URL, client=client, sleep=lambda _: None)
    assert len(calls) == 1, "долбиться в блокировку бессмысленно и вредно"


def test_client_error_is_not_retried():
    client, calls = client_returning(httpx.Response(404))

    with pytest.raises(FetchError, match="404"):
        get_html(URL, client=client, sleep=lambda _: None)
    assert len(calls) == 1
```

- [ ] **Step 2: Запустить тесты и убедиться, что они падают**

Run: `pytest tests/test_fetch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hunter.fetch'`

- [ ] **Step 3: Написать `hunter/fetch.py`**

```python
from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger(__name__)

# Оба сайта отдают выдачу только браузерным клиентам.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
}
RETRY_DELAYS = (2.0, 5.0, 12.0)


class FetchError(Exception):
    """Страницу не удалось получить. Проход продолжается без неё."""


class Blocked(FetchError):
    """Нас заблокировали. Ретраи только усугубят — прекращаем проход."""


def new_client(timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(follow_redirects=True, timeout=timeout, headers=HEADERS)


def get_html(
    url: str,
    *,
    client: httpx.Client | None = None,
    sleep=time.sleep,
    timeout: float = 30.0,
) -> str:
    own_client = client is None
    client = client or new_client(timeout)
    try:
        last_error: FetchError | None = None
        for delay in (0.0, *RETRY_DELAYS):
            if delay:
                log.info("Повтор через %.0f с: %s", delay, url)
                sleep(delay)
            try:
                response = client.get(url)
            except httpx.HTTPError as exc:
                last_error = FetchError(f"{url}: {exc}")
                continue

            if response.status_code == 403:
                raise Blocked(f"{url}: 403 — сайт заблокировал запрос")
            if response.status_code >= 500:
                last_error = FetchError(f"{url}: HTTP {response.status_code}")
                continue
            if response.status_code != 200:
                raise FetchError(f"{url}: HTTP {response.status_code}")
            return response.text

        raise last_error or FetchError(f"{url}: страницу получить не удалось")
    finally:
        if own_client:
            client.close()
```

- [ ] **Step 4: Запустить тесты и убедиться, что они проходят**

Run: `pytest tests/test_fetch.py -v`
Expected: PASS, 6 тестов.

- [ ] **Step 5: Коммит**

```bash
git add hunter/fetch.py tests/test_fetch.py
git commit -m "feat: add HTTP layer with retries and block detection

A 403 raises immediately instead of retrying: repeating a request the
site already refused only deepens the block."
```

---

### Task 9: Отправка в Телеграм

**Files:**
- Create: `hunter/telegram.py`
- Test: `tests/test_telegram.py`

**Interfaces:**
- Consumes: ничего из проекта.
- Produces: `send_messages(token, chat_id, texts, *, client=None, base_url=API_BASE) -> None`, исключение `TelegramError`, константа `API_BASE = "https://api.telegram.org"`.

- [ ] **Step 1: Написать падающий тест**

Создайте `tests/test_telegram.py`:

```python
import json

import httpx
import pytest

from hunter.telegram import TelegramError, send_messages


def recording_client(*status_codes):
    queue = list(status_codes) or [200]
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        code = queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx.Response(code, json={"ok": code == 200})

    return httpx.Client(transport=httpx.MockTransport(handler)), sent


def test_every_message_is_sent_with_html_and_no_preview():
    client, sent = recording_client(200)

    send_messages("123:abc", "456", ["первое", "второе"], client=client)

    assert [m["text"] for m in sent] == ["первое", "второе"]
    assert all(m["chat_id"] == "456" for m in sent)
    assert all(m["parse_mode"] == "HTML" for m in sent)
    assert all(m["disable_web_page_preview"] is True for m in sent)


def test_empty_list_sends_nothing():
    client, sent = recording_client(200)

    send_messages("123:abc", "456", [], client=client)

    assert sent == []


def test_failure_raises_and_stops():
    client, sent = recording_client(400, 200)

    with pytest.raises(TelegramError, match="400"):
        send_messages("123:abc", "456", ["первое", "второе"], client=client)

    assert len(sent) == 1, "после отказа продолжать нельзя"
```

- [ ] **Step 2: Запустить тесты и убедиться, что они падают**

Run: `pytest tests/test_telegram.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hunter.telegram'`

- [ ] **Step 3: Написать `hunter/telegram.py`**

```python
from __future__ import annotations

import httpx

API_BASE = "https://api.telegram.org"


class TelegramError(Exception):
    """Сообщение не ушло. Вызывающий обязан не помечать вакансии отправленными."""


def send_messages(
    token: str,
    chat_id: str,
    texts: list[str],
    *,
    client: httpx.Client | None = None,
    base_url: str = API_BASE,
) -> None:
    own_client = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        for text in texts:
            response = client.post(
                f"{base_url}/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
            if response.status_code != 200:
                raise TelegramError(
                    f"Телеграм ответил {response.status_code}: {response.text[:300]}"
                )
    finally:
        if own_client:
            client.close()
```

- [ ] **Step 4: Запустить тесты и убедиться, что они проходят**

Run: `pytest tests/test_telegram.py -v`
Expected: PASS, 3 теста.

- [ ] **Step 5: Коммит**

```bash
git add hunter/telegram.py tests/test_telegram.py
git commit -m "feat: send digest messages to Telegram"
```

---

### Task 10: Оркестрация прохода

Здесь сходится всё: первый запуск, предупреждения, признак жизни, коды выхода и главный инвариант проекта.

**Files:**
- Create: `hunter/run.py`
- Test: `tests/test_run.py`

**Interfaces:**
- Consumes: всё из задач 1–9.
- Produces: `main(argv: list[str] | None = None) -> int`, `run_pass(...) -> int` (тестируемое ядро), константы `PURGE_DAYS = 90`, `HEARTBEAT_HOURS = 24`, `WARNING_COOLDOWN_HOURS = 24`, `REQUEST_PAUSE_SECONDS = 1.5`. Ключи `meta`: `last_sent_at`, `last_warning_at`, `first_run_done`.

**Признак первого запуска — отдельная отметка, а не пустая таблица.** Соблазнительно считать первым запуском тот, при котором `seen` пуста, но это ломается: если первый проход не нашёл ни одной вакансии, писать в `seen` нечего, таблица остаётся пустой, и каждый следующий проход снова считается первым — бот повторно шлёт «Запущен», а первую реальную партию сворачивает в счётчик вместо дайджеста. Поэтому факт первого запуска пишется в `meta` под ключом `first_run_done`, и пишется **после** подтверждённой отправки, как и всё остальное состояние.

Коды выхода: `0` — проход завершён, `1` — отправка в Телеграм не удалась, `2` — ошибка конфигурации, `3` — сайт заблокировал запросы.

- [ ] **Step 1: Написать падающий тест**

Создайте `tests/test_run.py`:

```python
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hunter.config import Config
from hunter.fetch import Blocked, FetchError
from hunter.models import SearchQuery
from hunter.run import run_pass
from hunter.store import Store
from hunter.telegram import TelegramError

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 1, 14, 30, tzinfo=timezone.utc)

CONFIG = Config(
    sources=("hh",),
    queries=(SearchQuery(name="Python backend", text="python"),),
)


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class Sender:
    """Записывает отправленные сообщения; может изображать отказ Телеграма."""

    def __init__(self, fail=False):
        self.messages = []
        self.fail = fail

    def __call__(self, texts):
        if self.fail:
            raise TelegramError("Телеграм ответил 500")
        self.messages.extend(texts)


def page_returning(name):
    return lambda url: fixture(name)


def seeded_store():
    """База, у которой первый запуск уже состоялся: проход считается обычным."""
    store = Store(":memory:")
    store.set_meta("first_run_done", NOW.isoformat())
    return store


def no_pause(_seconds):
    """Заглушка вместо time.sleep: тесты не должны ждать по-настоящему."""


def test_first_run_seeds_the_database_without_flooding():
    store = Store(":memory:")
    sender = Sender()

    code = run_pass(
        config=CONFIG,
        store=store,
        fetch_page=page_returning("hh_search_sorted_with_salary.html"),
        send=sender,
        now=NOW,
    )

    assert code == 0
    assert len(sender.messages) == 1
    assert "Запущен" in sender.messages[0]
    assert "50" in sender.messages[0]
    assert store.is_empty() is False


def test_first_run_that_finds_nothing_still_counts_as_done():
    """Иначе пустой первый проход не записывает ничего, каждый следующий
    снова считается первым, и первая реальная партия вакансий приходит
    счётчиком вместо дайджеста."""
    store = Store(":memory:")
    sender = Sender()

    # В этой фикстуре одна вакансия, и та без зарплаты — после фильтров ноль.
    code = run_pass(
        config=CONFIG,
        store=store,
        fetch_page=page_returning("zarplata_search_python.html"),
        send=sender,
        now=NOW,
        pause=no_pause,
    )

    assert code == 0
    assert len(sender.messages) == 1
    assert "Запущен" in sender.messages[0]

    sender.messages.clear()
    code = run_pass(
        config=CONFIG,
        store=store,
        fetch_page=page_returning("hh_search_sorted_with_salary.html"),
        send=sender,
        now=NOW + timedelta(minutes=30),
        pause=no_pause,
    )

    text = "\n".join(sender.messages)
    assert code == 0
    assert "Запущен" not in text, "первый запуск уже состоялся"
    assert "<a href=" in text, "должен прийти обычный дайджест со ссылками"


def test_second_run_sends_only_what_is_new():
    store = Store(":memory:")
    run_pass(
        config=CONFIG,
        store=store,
        fetch_page=page_returning("hh_search_sorted_with_salary.html"),
        send=Sender(),
        now=NOW,
    )
    sender = Sender()

    code = run_pass(
        config=CONFIG,
        store=store,
        fetch_page=page_returning("hh_search_sorted_with_salary.html"),
        send=sender,
        now=NOW + timedelta(minutes=30),
    )

    assert code == 0
    assert sender.messages == [], "та же выдача — сообщений быть не должно"


def test_advertising_never_reaches_the_digest():
    store = seeded_store()
    sender = Sender()

    run_pass(
        config=CONFIG,
        store=store,
        fetch_page=page_returning("hh_search_python_msk.html"),
        send=sender,
        now=NOW,
    )

    text = "\n".join(sender.messages)
    assert "114811554" not in text, "рекламная врезка отсеяна"
    assert "з/п не указана" not in text, "вакансии без зарплаты отсеяны"


def test_vacancies_are_not_marked_seen_when_telegram_fails():
    store = seeded_store()
    sender = Sender(fail=True)

    code = run_pass(
        config=CONFIG,
        store=store,
        fetch_page=page_returning("hh_search_sorted_with_salary.html"),
        send=sender,
        now=NOW,
    )

    assert code == 1
    marked = store._conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0]
    assert marked == 0, (
        "главный инвариант: без подтверждённой отправки вакансии не помечаются"
    )


def test_failed_request_becomes_a_warning_and_does_not_stop_the_pass():
    config = Config(
        sources=("hh",),
        queries=(
            SearchQuery(name="Сломанный", text="broken"),
            SearchQuery(name="Рабочий", text="python"),
        ),
    )
    store = seeded_store()
    sender = Sender()

    def fetch_page(url):
        if "broken" in url:
            raise FetchError("таймаут")
        return fixture("hh_search_sorted_with_salary.html")

    # pause подменён: этот тест — единственный, который доходит до паузы
    # между запросами, и ждать полторы секунды по-настоящему незачем.
    code = run_pass(
        config=config,
        store=store,
        fetch_page=fetch_page,
        send=sender,
        now=NOW,
        pause=no_pause,
    )

    text = "\n".join(sender.messages)
    assert code == 0
    assert "⚠️" in text
    assert "Сломанный" in text
    assert "Рабочий" in text, "рабочий запрос отработал несмотря на сбой соседнего"


def test_parse_failure_is_reported_loudly():
    store = seeded_store()
    sender = Sender()

    def fetch_page(url):
        return "<html>редизайн</html>"

    code = run_pass(config=CONFIG, store=store, fetch_page=fetch_page, send=sender, now=NOW)

    text = "\n".join(sender.messages)
    assert code == 0
    assert "разбор" in text.lower() or "парс" in text.lower()


def test_block_stops_the_pass_with_its_own_exit_code():
    store = Store(":memory:")
    sender = Sender()

    def fetch_page(url):
        raise Blocked("403")

    code = run_pass(config=CONFIG, store=store, fetch_page=fetch_page, send=sender, now=NOW)

    assert code == 3


def test_warning_is_not_repeated_within_the_cooldown():
    store = seeded_store()

    def fetch_page(url):
        raise Blocked("403")

    first = Sender()
    run_pass(config=CONFIG, store=store, fetch_page=fetch_page, send=first, now=NOW)
    second = Sender()
    run_pass(
        config=CONFIG,
        store=store,
        fetch_page=fetch_page,
        send=second,
        now=NOW + timedelta(hours=1),
    )

    assert first.messages, "первое предупреждение уходит"
    assert second.messages == [], "второе в пределах суток — молчим"


def test_heartbeat_after_a_silent_day():
    store = seeded_store()
    store.set_meta("last_sent_at", (NOW - timedelta(hours=30)).isoformat())
    sender = Sender()

    run_pass(
        config=CONFIG,
        store=store,
        fetch_page=page_returning("hh_search_sorted_with_salary.html"),
        send=sender,
        now=NOW,
    )
    # Первый проход отправил вакансии, поэтому признак жизни не нужен.
    sender.messages.clear()
    store.set_meta("last_sent_at", (NOW - timedelta(hours=30)).isoformat())

    run_pass(
        config=CONFIG,
        store=store,
        fetch_page=page_returning("hh_search_sorted_with_salary.html"),
        send=sender,
        now=NOW + timedelta(minutes=30),
    )

    assert len(sender.messages) == 1
    assert "работает" in sender.messages[0]
```

- [ ] **Step 2: Запустить тесты и убедиться, что они падают**

Run: `pytest tests/test_run.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hunter.run'`

- [ ] **Step 3: Написать `hunter/run.py`**

```python
from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Config, ConfigError, load_config, load_secrets
from .digest import render
from .fetch import Blocked, FetchError, get_html, new_client
from .filters import Group, dedupe, drop_ads, drop_without_salary, sort_by_freshness
from .parse import ParseError, parse_search_page
from .sources import build_url
from .store import Store
from .telegram import TelegramError, send_messages

log = logging.getLogger("hunter")

PURGE_DAYS = 90
HEARTBEAT_HOURS = 24
WARNING_COOLDOWN_HOURS = 24
REQUEST_PAUSE_SECONDS = 1.5

EXIT_OK = 0
EXIT_TELEGRAM_FAILED = 1
EXIT_BAD_CONFIG = 2
EXIT_BLOCKED = 3

_LAST_SENT = "last_sent_at"
_LAST_WARNING = "last_warning_at"
_FIRST_RUN_DONE = "first_run_done"


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _hours_since(value: str | None, now: datetime) -> float:
    moment = _parse_time(value)
    if moment is None:
        return float("inf")
    return (now - moment).total_seconds() / 3600


def _collect(
    config: Config,
    fetch_page,
    debug_dir: Path | None,
    pause,
) -> tuple[list[Group], list[str], bool]:
    """Обходит все запросы × источники. Возвращает группы, предупреждения
    и признак блокировки."""
    groups: list[Group] = []
    warnings: list[str] = []
    first_request = True

    for query in config.queries:
        collected = []
        for source in config.sources:
            for page in range(query.max_pages):
                if not first_request:
                    pause(REQUEST_PAUSE_SECONDS)
                first_request = False

                url = build_url(source, query, page=page)
                try:
                    page_html = fetch_page(url)
                except Blocked as exc:
                    log.error("Блокировка: %s", exc)
                    return groups, [f"Сайт заблокировал запросы: {exc}"], True
                except FetchError as exc:
                    log.warning("Запрос не выполнен: %s", exc)
                    warnings.append(f"запрос «{query.name}» ({source}) не выполнен")
                    continue

                try:
                    collected.extend(parse_search_page(page_html, source))
                except ParseError as exc:
                    log.error("Разбор не удался: %s", exc)
                    if debug_dir is not None:
                        debug_dir.mkdir(parents=True, exist_ok=True)
                        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                        target = debug_dir / f"{source}-{stamp}.html"
                        target.write_text(page_html, encoding="utf-8")
                        log.error("Страница сохранена в %s", target)
                    warnings.append(
                        f"разбор выдачи «{query.name}» ({source}) не удался — "
                        f"похоже, сайт сменил вёрстку"
                    )

        groups.append((query.name, collected))

    return groups, warnings, False


def run_pass(
    *,
    config: Config,
    store: Store,
    fetch_page,
    send,
    now: datetime,
    debug_dir: Path | None = None,
    pause=time.sleep,
) -> int:
    """Один проход. Всё внешнее подаётся аргументами, поэтому тестируется
    без сети и без Телеграма."""
    removed = store.purge_older_than(PURGE_DAYS)
    if removed:
        log.info("Удалено устаревших записей: %d", removed)

    first_run = store.get_meta(_FIRST_RUN_DONE) is None
    groups, warnings, blocked = _collect(config, fetch_page, debug_dir, pause)

    if blocked:
        if _hours_since(store.get_meta(_LAST_WARNING), now) >= WARNING_COOLDOWN_HOURS:
            try:
                send(render([], now, warnings=tuple(warnings)))
            except TelegramError as exc:
                log.error("Предупреждение не отправлено: %s", exc)
                return EXIT_TELEGRAM_FAILED
            store.set_meta(_LAST_WARNING, now.isoformat())
        return EXIT_BLOCKED

    cleaned: list[Group] = []
    for name, vacancies in groups:
        vacancies = drop_ads(vacancies)
        vacancies = drop_without_salary(vacancies)
        cleaned.append((name, sort_by_freshness(vacancies)))
    cleaned = dedupe(cleaned)

    fresh: list[Group] = [(name, store.unseen(vs)) for name, vs in cleaned]
    total_new = sum(len(vs) for _, vs in fresh)
    everything = [v for _, vs in fresh for v in vs]

    log.info(
        "Проход завершён: запросов %d, новых вакансий %d, предупреждений %d",
        len(config.queries),
        total_new,
        len(warnings),
    )

    if first_run:
        message = (
            f"🤖 Запущен. Слежу за {len(config.queries)} запросами, "
            f"в базу занесено {total_new} вакансий. Дальше — только новые."
        )
        try:
            send([message])
        except TelegramError as exc:
            log.error("Стартовое сообщение не отправлено: %s", exc)
            return EXIT_TELEGRAM_FAILED
        # Состояние пишется только после подтверждённой доставки — как и в
        # обычном проходе. Отметка о первом запуске ставится безусловно, даже
        # если вакансий не нашлось, иначе следующий проход снова был бы первым.
        store.mark_seen(everything, sent=False)
        store.set_meta(_FIRST_RUN_DONE, now.isoformat())
        store.set_meta(_LAST_SENT, now.isoformat())
        return EXIT_OK

    suppress_warnings = (
        _hours_since(store.get_meta(_LAST_WARNING), now) < WARNING_COOLDOWN_HOURS
    )
    shown_warnings = () if suppress_warnings else tuple(warnings)
    messages = render(fresh, now, warnings=shown_warnings)

    if not messages:
        if _hours_since(store.get_meta(_LAST_SENT), now) >= HEARTBEAT_HOURS:
            try:
                send(["🤖 За сутки новых вакансий нет, всё работает."])
            except TelegramError as exc:
                log.error("Признак жизни не отправлен: %s", exc)
                return EXIT_TELEGRAM_FAILED
            store.set_meta(_LAST_SENT, now.isoformat())
        return EXIT_OK

    try:
        send(messages)
    except TelegramError as exc:
        log.error("Отправка не удалась, вакансии остаются новыми: %s", exc)
        return EXIT_TELEGRAM_FAILED

    store.mark_seen(everything, sent=True)
    store.set_meta(_LAST_SENT, now.isoformat())
    if shown_warnings:
        store.set_meta(_LAST_WARNING, now.isoformat())
    return EXIT_OK


def _use_utf8_console() -> None:
    """Консоль Windows по умолчанию в cp1251: ₽, тире и эмодзи в неё не влезают,
    и печать дайджеста падает с UnicodeEncodeError."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_dir / "hunter.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    root.addHandler(logging.StreamHandler(sys.stderr))


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Проверить вакансии и отправить дайджест")
    parser.add_argument("--config", type=Path, default=root / "config.yaml")
    parser.add_argument("--env", type=Path, default=root / ".env")
    parser.add_argument("--db", type=Path, default=root / "state.db")
    parser.add_argument("--log-dir", type=Path, default=root / "logs")
    parser.add_argument("--debug-dir", type=Path, default=root / "debug")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="показать дайджест в консоли, ничего не отправлять и не помечать",
    )
    args = parser.parse_args(argv)

    _use_utf8_console()
    _setup_logging(args.log_dir)

    try:
        config = load_config(args.config)
        token, chat_id = ("", "")
        if not args.dry_run:
            token, chat_id = load_secrets(args.env)
    except ConfigError as exc:
        log.error("%s", exc)
        return EXIT_BAD_CONFIG

    client = new_client()
    try:
        if args.dry_run:
            def send(texts):
                for text in texts:
                    print(text)
                    print("-" * 40)
        else:
            def send(texts):
                send_messages(token, chat_id, texts)

        with Store(args.db) as store:
            return run_pass(
                config=config,
                store=store,
                fetch_page=lambda url: get_html(url, client=client),
                send=send,
                now=datetime.now(timezone.utc).astimezone(),
                debug_dir=args.debug_dir,
            )
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Запустить тесты и убедиться, что они проходят**

Run: `pytest tests/test_run.py -v`
Expected: PASS, 10 тестов.

- [ ] **Step 5: Прогнать весь набор тестов**

Run: `pytest -v`
Expected: PASS, 71 тест, ни одного обращения в сеть.

- [ ] **Step 6: Коммит**

```bash
git add hunter/run.py tests/test_run.py
git commit -m "feat: orchestrate a single pass

mark_seen runs only after Telegram confirms delivery, so a failed send
leaves the vacancies pending for the next run instead of losing them."
```

---

### Task 11: Живая проверка, установка и документация

Первая задача, которая ходит в сеть. До неё всё проверялось на фикстурах.

**Files:**
- Create: `install_task.ps1`
- Create: `README.md`
- Create: `config.yaml` (локально, не коммитится)
- Create: `.env` (локально, не коммитится)

**Interfaces:**
- Consumes: `main()` из `hunter.run`.
- Produces: рабочую установку.

- [ ] **Step 1: Создать рабочий конфиг**

```powershell
Copy-Item config.example.yaml config.yaml
```

Отредактируйте `config.yaml` под свои запросы.

- [ ] **Step 2: Проверить сборку URL и живую выдачу без Телеграма**

```powershell
.\.venv\Scripts\python.exe -m hunter.run --dry-run --db .\dryrun.db
```

Ожидается: в консоль печатается дайджест или стартовое сообщение. Проверьте глазами:
названия вакансий осмысленные, зарплаты правдоподобные, ссылки открываются.

Если появилось «не удалось разобрать выдачу» — сайт сменил вёрстку, смотрите
сохранённый HTML в `debug/`.

- [ ] **Step 3: Проверить, что фильтр формата работы действительно сужает выдачу**

Параметр `work_format=REMOTE` — единственное место в проекте, где имя параметра
взято по аналогии, а не подтверждено наблюдением. Проверьте:

```powershell
.\.venv\Scripts\python.exe -c @"
from hunter.models import SearchQuery
from hunter.sources import build_url
from hunter.fetch import get_html
from hunter.parse import parse_search_page

q = SearchQuery(name='t', text='python', work_format='remote', only_with_salary=False)
vs = parse_search_page(get_html(build_url('hh', q)), 'hh')
vs = [v for v in vs if not v.is_ad]
remote = sum('REMOTE' in v.work_formats for v in vs)
print(f'{remote} из {len(vs)} с REMOTE')
"@
```

Ожидается: все или почти все вакансии содержат `REMOTE`.

Если доля низкая — параметр игнорируется. Тогда в `hunter/sources.py` замените
строку `params.append(("work_format", query.work_format.upper()))` на
`params.append(("schedule", "remote"))` для значения `remote`, поправьте
соответствующий тест в `tests/test_sources.py` и повторите проверку.

- [ ] **Step 4: Проверить, что zarplata отвечает на те же параметры**

```powershell
.\.venv\Scripts\python.exe -c @"
from hunter.models import SearchQuery
from hunter.sources import build_url
from hunter.fetch import get_html
from hunter.parse import parse_search_page

q = SearchQuery(name='t', text='python', area=113, only_with_salary=False)
url = build_url('zarplata', q)
print(url)
print(len(parse_search_page(get_html(url), 'zarplata')), 'вакансий')
"@
```

Ожидается: URL печатается, вакансии разбираются без исключения. Число будет
небольшим — zarplata заметно меньше hh, это нормально.

- [ ] **Step 5: Создать бота в Телеграме и записать секреты**

1. Напишите [@BotFather](https://t.me/BotFather), команда `/newbot`, получите токен.
2. Напишите своему боту любое сообщение — без этого он не может писать вам первым.
3. Узнайте свой chat_id:

```powershell
.\.venv\Scripts\python.exe -c @"
import httpx, os
token = input('Токен: ').strip()
r = httpx.get(f'https://api.telegram.org/bot{token}/getUpdates').json()
for u in r.get('result', []):
    chat = u.get('message', {}).get('chat', {})
    if chat: print(chat.get('id'), chat.get('username'))
"@
```

4. Создайте `.env`:

```
TG_BOT_TOKEN=123456:AAA-ваш-токен
TG_CHAT_ID=123456789
```

- [ ] **Step 6: Первый настоящий запуск**

```powershell
Remove-Item .\dryrun.db -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m hunter.run
```

Ожидается: в Телеграм приходит сообщение «🤖 Запущен. Слежу за N запросами…».
Проверьте `echo $LASTEXITCODE` — должно быть `0`.

- [ ] **Step 7: Написать `install_task.ps1`**

Файл сохраняется в **UTF-8 с BOM** — иначе PowerShell 5.1 не сможет его разобрать
(см. раздел Global Constraints). После записи проверьте:

```powershell
$errors = $null
$null = [System.Management.Automation.Language.Parser]::ParseFile(
    (Resolve-Path .\install_task.ps1).Path, [ref]$null, [ref]$errors)
if ($errors) { $errors[0].Message } else { "parse OK" }
```

Ожидается `parse OK`.


```powershell
# Регистрирует задачу в Планировщике: старт при входе в систему,
# повтор каждые 30 минут. pythonw.exe — чтобы не мигало окно консоли.

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
$taskName = "VacancyHunter"

if (-not (Test-Path $pythonw)) {
    throw "Не найден $pythonw. Сначала создайте окружение: py -m venv .venv"
}

$action = New-ScheduledTaskAction -Execute $pythonw `
    -Argument "-m hunter.run" -WorkingDirectory $root

# New-ScheduledTaskTrigger не умеет одновременно -AtLogOn и повтор, поэтому
# берём Repetition у одноразового триггера и переносим на логонный.
# Длительность именно 3650 дней, а не [TimeSpan]::MaxValue: максимальное
# значение (~10 млн дней) не помещается в ISO-8601-длительность схемы XML,
# где под дни отведено четыре разряда, и Register-ScheduledTask падает
# с ошибкой формата — задача не создаётся вовсе.
$trigger = New-ScheduledTaskTrigger -AtLogOn
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 30) `
    -RepetitionDuration (New-TimeSpan -Days 3650)).Repetition

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Дайджест новых вакансий с hh.ru и zarplata.ru" `
    -Force | Out-Null

Write-Host "Задача '$taskName' зарегистрирована. Проверить:"
Write-Host "  Get-ScheduledTask -TaskName $taskName"
Write-Host "  Start-ScheduledTask -TaskName $taskName"
Write-Host "Удалить:"
Write-Host "  Unregister-ScheduledTask -TaskName $taskName -Confirm:`$false"
```

- [ ] **Step 8: Зарегистрировать задачу и проверить её**

```powershell
.\install_task.ps1
Start-ScheduledTask -TaskName VacancyHunter
Start-Sleep -Seconds 20
Get-Content .\logs\hunter.log -Tail 10
```

Ожидается: в логе строка «Проход завершён: запросов N, новых вакансий M».

- [ ] **Step 9: Написать `README.md`**

````markdown
# Vacancy Hunter

Раз в полчаса проверяет hh.ru и zarplata.ru по вашим запросам и присылает
в Телеграм дайджест вакансий, которых не было в прошлый раз.

## Как устроен

Один запуск — один проход, процесс завершается. Планировщик задач Windows
запускает его каждые 30 минут. Что уже отправляли, помнит `state.db`.

Поисковый API обоих сайтов отдаёт 403 анонимным запросам, поэтому данные
берутся из публичной выдачи: в HTML страницы встроен JSON со всем состоянием
поиска.

## Установка

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install httpx pyyaml pytest
Copy-Item config.example.yaml config.yaml
```

Заполните `config.yaml` своими запросами и создайте `.env`:

```
TG_BOT_TOKEN=токен от @BotFather
TG_CHAT_ID=ваш chat_id
```

Проверьте, ничего не отправляя:

```powershell
.\.venv\Scripts\python.exe -m hunter.run --dry-run --db .\dryrun.db
```

Поставьте на расписание:

```powershell
.\install_task.ps1
```

## Настройка запросов

`config.yaml`, поле `text` уходит на hh как есть — он понимает `AND`, `OR`,
`NOT`, кавычки и префикс `NAME:` для поиска только по заголовку:

```yaml
queries:
  - name: "Python backend"
    text: "python AND (backend OR бэкенд) NOT стажёр"
    salary: 200000
    experience: between3And6     # noExperience | between1And3 | between3And6 | moreThan6
    work_format: remote          # remote | hybrid | on_site | field_work
    area: 1                      # 1 = Москва, 2 = СПб, 113 = вся Россия
```

## Коды выхода

| Код | Значение |
|---|---|
| 0 | Проход завершён |
| 1 | Телеграм не принял сообщение; вакансии остались непрочитанными и придут в следующий раз |
| 2 | Ошибка в `config.yaml` или `.env` |
| 3 | Сайт заблокировал запросы |

## Если сообщения перестали приходить

Бот сам сообщает о поломке разбора и раз в сутки подтверждает, что жив.
Если тишина полная — смотрите `logs/hunter.log`. При смене вёрстки сайта
проблемная страница сохраняется в `debug/`; почините `hunter/parse.py`,
а `tests/test_parse.py` покажет, когда парсер снова в порядке.

## Тесты

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Ни один тест не ходит в сеть: разбор проверяется на сохранённых страницах
из `tests/fixtures/`.
````

- [ ] **Step 10: Убедиться, что секреты не попадают в репозиторий**

Run: `git status --short`
Expected: `config.yaml`, `.env`, `state.db`, `dryrun.db`, `logs/`, `debug/` не значатся среди неотслеживаемых файлов.

Если `dryrun.db` виден — добавьте `*.db` в `.gitignore`.

- [ ] **Step 11: Коммит**

```bash
git add install_task.ps1 README.md .gitignore
git commit -m "feat: add Task Scheduler installer and documentation"
```

---

## Self-Review

**Покрытие спеки.** Пройдено по разделам:

| Требование спеки | Задача |
|---|---|
| Парсинг публичной выдачи, `HH-Lux-InitialState` | 4 |
| Один парсер на hh и zarplata | 3, 4 |
| One-shot запуск + Планировщик | 10, 11 |
| `config.yaml` с `defaults` и `queries` | 2 |
| Секреты в `.env` | 2 |
| Фильтры на стороне hh | 3 |
| Отсев рекламы, без зарплаты, дублей | 5 |
| Формат дайджеста, HTML, без превью | 7, 9 |
| Порядок групп и вакансий | 5, 7 |
| Лимит 4096 и обрезка на 30 | 7 |
| Таблицы `seen` и `meta`, ключ с префиксом источника | 6 |
| Очистка старше 90 дней | 6 |
| Поведение при первом запуске | 10 |
| Ретраи, блокировка, поломка разбора, отказ Телеграма | 8, 10 |
| Инвариант «`mark_seen` после отправки» | 10 |
| Признак жизни раз в сутки | 10 |
| Логи с ротацией | 10 |
| Тесты без сети | 1–10 |
| `install_task.ps1` | 11 |

Пробелов не осталось.

**Плейсхолдеры.** Проверено: «TBD», «TODO», «добавьте обработку ошибок»,
«напишите тесты для вышеописанного» и ссылок вида «как в задаче N» в плане нет.
Код каждого шага приведён целиком.

**Согласованность имён.** Сверено сквозь все задачи: `Vacancy.key`,
`Vacancy.has_salary`, `SearchQuery`, `Config`, `ConfigError`, `load_config`,
`load_secrets`, `build_url`, `UnknownSource`, `parse_search_page`,
`extract_state`, `ParseError`, `drop_ads`, `drop_without_salary`,
`sort_by_freshness`, `dedupe`, `Group`, `Store.unseen`, `Store.mark_seen`,
`Store.is_empty`, `Store.purge_older_than`, `Store.get_meta`,
`Store.set_meta`, `render`, `format_salary`, `escape`, `get_html`,
`FetchError`, `Blocked`, `new_client`, `send_messages`, `TelegramError`,
`run_pass`, `main`. Имена совпадают во всех местах использования.

**Проверка форматирования.** Ожидаемые строки из тестов задачи 7 прогнаны
через реализацию до записи плана: все пять случаев зарплаты и все формы
множественного числа (1, 3, 12, 21, 111, 45) совпадают. Тесты не провалятся
из-за опечатки в ожидаемом значении.

**Найдено при проверке.** Консоль этой машины работает в cp1251 и роняет
`print()` на символе `₽`. Без правки режим `--dry-run` падал бы на первом же
запуске. В `main()` добавлен `_use_utf8_console()`.

**Известное допущение.** Имя параметра `work_format` для hh взято по аналогии
с полем `workFormats` в ответе, а не подтверждено наблюдением — все остальные
параметры проверены живыми запросами. Задача 11, шаг 3 проверяет его и содержит
готовую замену на случай, если он не работает.
