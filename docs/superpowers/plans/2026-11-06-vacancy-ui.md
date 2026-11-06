# Локальная таблица вакансий — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Команда `vacancies ui` открывает локальную таблицу вакансий, где можно перейти на hh, поставить этап воронки и сохранить комментарий в базу.

**Architecture:** Один модуль `hunter/corpus/ui.py`: чистые функции фильтрации/сортировки/рендера HTML, поверх них `ThreadingHTTPServer` на `127.0.0.1` с двумя маршрутами — `GET /` (страница) и `POST /save` (запись через `Corpus.set_status`). Данные берутся существующим `export_rows`, новых зависимостей нет.

**Tech Stack:** Python 3.12, stdlib (`http.server`, `urllib.parse`, `html`, `webbrowser`), SQLite через `hunter.corpus.store.Corpus`, pytest.

**Spec:** `docs/superpowers/specs/2026-11-06-vacancy-ui-design.md`

## Global Constraints

- Новых зависимостей нет: `pyproject.toml` не меняется, только stdlib.
- Сервер слушает только `127.0.0.1`; доступ из сети не нужен.
- Любое значение из базы попадает в HTML только через `html.escape`.
- Запись этапа и заметки — только через `Corpus.set_status`; своей SQL-записи в `applications` нет.
- Интерфейс и сообщения — по-русски; комментарии в коде — по-русски, как в проекте.
- Тесты: `uv run pytest`. Python ≥ 3.12.

## Review Focus

- Поиск по-русски в другом регистре: «яндекс» находит «Яндекс Практикум» (Task 1).
- HTML-экранирование: название, «почему» и заметка с `<script>` или кавычками не ломают страницу (Task 2).
- Сохранение без JS: обычный POST пишет в базу и возвращает к тем же фильтрам (Task 4).
- Очистка заметки: пустое поле стирает старую заметку, а не оставляет её (Task 4).
- `back` не уводит на чужой сайт: `//evil.com` превращается в `/` (Task 4).

---

### Task 1: Фильтры и сортировка

**Files:**
- Create: `hunter/corpus/ui.py`
- Test: `tests/test_corpus_ui.py`

**Interfaces:**
- Consumes: `hunter.export.export_rows(corpus, days) -> list[sqlite3.Row]` (колонки `id, url, name, company, first_seen_at, score, why, flags, status, note, role`), `hunter.corpus.store.STATUSES`.
- Produces:
  - `@dataclass(frozen=True) Filters(days: int | None = None, min_score: int | None = None, status: str | None = None, q: str = "", sort: str = "found", desc: bool = True)`
  - `SORTS = ("found", "score", "name")`
  - `parse_filters(params: dict[str, list[str]]) -> Filters`
  - `Filters.query(self, **changes) -> str` — query string без `?`, только не-дефолтные поля, `urlencode`.
  - `select_rows(rows: list[sqlite3.Row], filters: Filters) -> list[sqlite3.Row]`

- [ ] **Step 1: Написать падающие тесты**

В `tests/test_corpus_ui.py` — фикстуры и помощники как в `tests/test_export.py`:

```python
import pytest
from hunter.corpus.extract import vacancy_card_row
from hunter.corpus.store import Corpus
from hunter.corpus.ui import Filters, parse_filters, select_rows
from hunter.export import export_rows
from tests.factories import make_vacancy


@pytest.fixture
def db(tmp_path):
    return tmp_path / "v.db"


@pytest.fixture
def corpus(db):
    with Corpus.open(db, create=True) as opened:
        yield opened


def _add(corpus, vacancy_id, *, name=None, company="Компания", role="backend"):
    corpus.note_seen(vacancy_card_row(make_vacancy(
        vacancy_id=str(vacancy_id), name=name or f"Вакансия {vacancy_id}", company=company,
    )))
    corpus.add_hit(vacancy_id, role, "python")


def _score(corpus, vacancy_id, score, why="подходит", flags=()):
    corpus._conn.execute(
        "INSERT INTO scores (key, criteria_hash, score, why, flags, scored_at) "
        "VALUES (?, 'h', ?, ?, ?, '2026-10-01T00:00:00+00:00')",
        (f"hh:{vacancy_id}", score, why, json.dumps(flags, ensure_ascii=False)),
    )
    corpus._conn.commit()


def _apply(corpus, vacancy_id, status, note=None):
    corpus._conn.execute(
        "INSERT INTO applications (vacancy_id, status, note, updated_at) "
        "VALUES (?, ?, ?, '2026-10-02T00:00:00+00:00')",
        (vacancy_id, status, note),
    )
    corpus._conn.commit()


def _age(corpus, vacancy_id, days):
    first_seen = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    corpus._conn.execute(
        "UPDATE vacancies SET first_seen_at = ? WHERE id = ?", (first_seen, vacancy_id)
    )
    corpus._conn.commit()


def test_parse_defaults_and_values():
    assert parse_filters({}) == Filters()
    assert parse_filters({
        "days": ["7"], "min_score": ["4"], "status": ["test"],
        "q": [" python "], "sort": ["score"], "desc": ["0"],
    }) == Filters(days=7, min_score=4, status="test", q="python", sort="score", desc=False)


def test_parse_ignores_unknown_values():
    assert parse_filters({"days": ["5"], "status": ["nope"], "sort": ["x"]}) == Filters()


def test_query_round_trips():
    filters = Filters(days=7, min_score=4, status="test", q="питон", sort="score", desc=False)
    assert parse_filters(parse_qs(filters.query())) == filters


def test_search_ignores_case_for_cyrillic(corpus):
    _add(corpus, 1, name="Яндекс Практикум")
    _add(corpus, 2, name="VK")
    rows = export_rows(corpus, None)
    assert [r["id"] for r in select_rows(rows, Filters(q="яндекс"))] == [1]


def test_status_new_and_exact(corpus):
    _add(corpus, 1)
    _add(corpus, 2)
    _apply(corpus, 2, "applied", "ждём")
    rows = export_rows(corpus, None)
    assert [r["id"] for r in select_rows(rows, Filters(status="new"))] == [1]
    assert [r["id"] for r in select_rows(rows, Filters(status="applied"))] == [2]


def test_min_score_excludes_unscored(corpus):
    _add(corpus, 1)
    _add(corpus, 2)
    _score(corpus, 1, 8)
    rows = export_rows(corpus, None)
    assert [r["id"] for r in select_rows(rows, Filters(min_score=5))] == [1]


def test_days_filter_uses_first_seen(corpus):
    _add(corpus, 1)
    _add(corpus, 2)
    _age(corpus, 2, 10)
    rows = export_rows(corpus, None)
    assert [r["id"] for r in select_rows(rows, Filters(days=7))] == [1]


def test_score_sort_puts_unscored_last(corpus):
    _add(corpus, 1)
    _add(corpus, 2)
    _add(corpus, 3)
    _score(corpus, 1, 2)
    _score(corpus, 2, 8)
    rows = export_rows(corpus, None)
    assert [r["score"] for r in select_rows(rows, Filters(sort="score"))] == [8, 2, None]


def test_found_sort_newest_first(corpus):
    _add(corpus, 1)
    _add(corpus, 2)
    _age(corpus, 1, 3)
    rows = export_rows(corpus, None)
    assert [r["id"] for r in select_rows(rows, Filters())] == [2, 1]
```

Импортировать `json` и `parse_qs` (`urllib.parse`).

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_corpus_ui.py -v`
Expected: FAIL — `ModuleNotFoundError: hunter.corpus.ui`.

- [ ] **Step 3: Реализовать `Filters`, `parse_filters`, `select_rows`**

Значения:
- `days` принимается только `7` или `30`, иначе `None`.
- `min_score` — целое ≥ 0, иначе `None`; вакансии без оценки при `min_score` не проходят.
- `status`: `"new"` — нет строки в `applications`; значение из `STATUSES` — точное совпадение; иначе `None` (все).
- `q`: сравнение через `casefold()` по `name + " " + company`.
- `days`: `datetime.fromisoformat(first_seen_at) >= datetime.now(timezone.utc) - timedelta(days=days)`.
- Сортировка: `found` — `first_seen_at`, `score` — по убыванию/возрасту, вакансии без оценки всегда в конце, `name` — `casefold()`; при равенстве — по `id` убыв.
- `desc` по умолчанию `True`; `Filters.query()` пропускает поля, равные дефолту. Порядок полей в query: `days, min_score, status, q, sort, desc`.

- [ ] **Step 4: Прогнать тесты**

Run: `uv run pytest tests/test_corpus_ui.py -v`
Expected: PASS, 9 тестов.

- [ ] **Step 5: Коммит**

```bash
git add hunter/corpus/ui.py tests/test_corpus_ui.py
git commit -m "feat: фильтры и сортировка локальной таблицы вакансий"
```

---

### Task 2: Страница таблицы

**Files:**
- Modify: `hunter/corpus/ui.py`
- Test: `tests/test_corpus_ui.py`

**Interfaces:**
- Consumes: `Filters`, `select_rows`, `STATUSES`, `STATUS_LABELS`, `hunter.export.NEW_LABEL`, `hunter.export.export_rows`.
- Produces: `render_page(rows: list[sqlite3.Row], *, filters: Filters, threshold: int, total: int) -> str`.

Контракт разметки, на который опираются тесты:

- 9 заголовков: `Название, Компания, Оценка, Почему, Флаги, Роль, Этап, Заметка, Найдена`; у `Название`, `Оценка`, `Найдена` заголовок — ссылка `?sort=…&desc=…`.
- Строка: `<tr id="v-7">`, название — `<a href="…" target="_blank" rel="noopener">`.
- Оценка: `<td class="score good">8</td>` при `score >= threshold`; `near` при `3 <= score < threshold`; `low` при `score < 3`; `none` и `—`, если оценки нет.
- Этап: `<select name="status">` с `<option value="">новая</option>` и восемью этапами; выбран текущий, у строки без этапа выбран пустой.
- Заметка: `<input type="text" name="note" value="…">`.
- Форма строки: `<form class="save" method="post" action="/save">`, скрытые `id` и `back` (текущий путь с query), кнопка `<button type="submit">Сохранить</button>`.
- Фильтры: `<form method="get" action="/">` с `days` (`<option value="7">`, `<option value="30">`), `min_score` (number), `status` (все + «новая» + восемь), `q` (search), скрытыми `sort` и `desc`; кнопка «Показать».
- Внизу — «Показано N из M».
- Встроенный `<script>`: перехватывает submit у `form.save`, шлёт `fetch("/save", {method: "POST", headers: {"X-Requested-With": "fetch"}, body: new URLSearchParams(new FormData(form))})`; при 204 подсвечивает строку «сохранено» на 2 секунды, при ошибке показывает текст ответа рядом с кнопкой; кнопку блокирует на время запроса.

- [ ] **Step 1: Написать падающие тесты**

```python
def _page(corpus, *, filters=None, threshold=5):
    rows = export_rows(corpus, None)
    return render_page(rows, filters=filters or Filters(), threshold=threshold, total=len(rows))


def test_page_escapes_data(corpus):
    _add(corpus, 1, name='<script>alert("x")</script>')
    _apply(corpus, 1, "selected", '<b>заметка</b>')
    page = _page(corpus)
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page
    assert "&lt;b&gt;заметка" in page


def test_page_has_columns_link_and_stages(corpus):
    _add(corpus, 7, name="Python dev")
    page = _page(corpus)
    for header in ("Название", "Компания", "Оценка", "Почему", "Флаги", "Роль", "Этап", "Заметка", "Найдена"):
        assert header in page
    assert 'href="https://hh.ru/vacancy/7"' in page
    assert 'target="_blank"' in page
    assert '<option value="">новая</option>' in page
    assert 'action="/save"' in page
    assert 'id="v-7"' in page


def test_score_class_follows_threshold(corpus):
    _add(corpus, 1)
    _add(corpus, 2)
    _add(corpus, 3)
    _add(corpus, 4)
    _score(corpus, 1, 8)
    _score(corpus, 2, 4)
    _score(corpus, 3, 2)
    page = _page(corpus)
    assert 'class="score good">8' in page
    assert 'class="score near">4' in page
    assert 'class="score low">2' in page
    assert 'class="score none">—' in page


def test_page_keeps_filters_in_form_and_row_back(corpus):
    _add(corpus, 7)
    page = _page(corpus, filters=Filters(days=7, status="new", q="python"))
    assert 'name="q" value="python"' in page
    assert 'name="back" value="/?days=7&amp;status=new&amp;q=python"' in page
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_corpus_ui.py -v`
Expected: FAIL — `ImportError: cannot import name 'render_page'`.

- [ ] **Step 3: Реализовать `render_page` и встроенные CSS/JS**

CSS — константа `_STYLE`, JS — константа `_SCRIPT`. Страница собирается склейкой (`"<style>" + _STYLE + "</style>"`), а не одной f-строкой: фигурные скобки CSS и JS в f-строку не попадают. Всё из базы — `html.escape(value, quote=True)`; `None` → пустая строка. «Почему» и «Флаги» обрезать в ячейке до ~120 символов, полный текст — в `title`.

- [ ] **Step 4: Прогнать тесты**

Run: `uv run pytest tests/test_corpus_ui.py -v`
Expected: PASS, 13 тестов.

- [ ] **Step 5: Коммит**

```bash
git add hunter/corpus/ui.py tests/test_corpus_ui.py
git commit -m "feat: страница локальной таблицы вакансий"
```

---

### Task 3: HTTP-сервер и `GET /`

**Files:**
- Modify: `hunter/corpus/ui.py`
- Test: `tests/test_corpus_ui.py`

**Interfaces:**
- Consumes: `render_page`, `parse_filters`, `select_rows`, `export_rows`, `Corpus.open_readonly`.
- Produces:
  - `make_server(db: Path, port: int = 0, *, host: str = "127.0.0.1", threshold: int = 5) -> ThreadingHTTPServer`
  - `serve(db: Path, port: int = 8765, *, open_browser: bool = True, host: str = "127.0.0.1", threshold: int = 5) -> int`
  - `make_server` не читает базу и не печатает; тесты берут настоящий порт из `server.server_address[1]`.

- [ ] **Step 1: Написать падающие тесты**

```python
import threading
from urllib.error import HTTPError
from urllib.request import urlopen

from hunter.corpus.ui import make_server


@pytest.fixture
def server(db):
    started = make_server(db, 0)
    thread = threading.Thread(target=started.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{started.server_address[1]}"
    started.shutdown()
    started.server_close()


def test_get_renders_and_filters(corpus, server):
    _add(corpus, 1, name="Яндекс Практикум")
    _add(corpus, 2, name="VK")
    assert "Яндекс Практикум" in urlopen(server + "/").read().decode()
    body = urlopen(server + "/?q=" + quote("яндекс")).read().decode()
    assert "Яндекс Практикум" in body and "VK" not in body


def test_unknown_path_is_404(server):
    with pytest.raises(HTTPError) as caught:
        urlopen(server + "/nope")
    assert caught.value.code == 404
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_corpus_ui.py -v`
Expected: FAIL — `ImportError: cannot import name 'make_server'`.

- [ ] **Step 3: Реализовать сервер**

`make_server` создаёт `ThreadingHTTPServer((host, port), Handler)`; `Handler` получает `db` и `threshold` через атрибуты подкласса или `functools.partial`. `ThreadingHTTPServer.daemon_threads = True`.

`GET /`: `urlsplit(self.path)`, `parse_qs`, база на запрос через `Corpus.open_readonly`, ответ `text/html; charset=utf-8`, в заголовках `Cache-Control: no-store`.
`GET /favicon.ico`: 204. Остальное: 404 с текстом «Нет такой страницы».
`serve`: `make_server`, печать `Таблица: http://127.0.0.1:<порт>`, `webbrowser.open` при `open_browser`, `serve_forever`; `KeyboardInterrupt` → 0; `OSError` с `EADDRINUSE` → сообщение «Порт N занят, попробуйте --port» и 2; в `finally` — `server_close()`.

- [ ] **Step 4: Прогнать тесты**

Run: `uv run pytest tests/test_corpus_ui.py -v`
Expected: PASS, 15 тестов.

- [ ] **Step 5: Коммит**

```bash
git add hunter/corpus/ui.py tests/test_corpus_ui.py
git commit -m "feat: локальный сервер таблицы вакансий"
```

---

### Task 4: Сохранение `POST /save`

**Files:**
- Modify: `hunter/corpus/ui.py`
- Test: `tests/test_corpus_ui.py`

**Interfaces:**
- Consumes: `Corpus.open`, `Corpus.set_status`, `STATUSES`, `CorpusError`.
- Produces: маршрут `POST /save`; на успех — 204 при `X-Requested-With: fetch`, иначе 303 на `back#v-<id>`; на ошибку — 400 с текстом.

- [ ] **Step 1: Написать падающие тесты**

```python
from urllib.parse import urlencode
from urllib.request import Request


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _post(server, data, *, follow=False, fetch=False):
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if fetch:
        headers["X-Requested-With"] = "fetch"
    request = Request(server + "/save", data=urlencode(data).encode(), headers=headers)
    opener = build_opener() if follow else build_opener(NoRedirect)
    return opener.open(request)


def test_save_writes_status_and_note(db, corpus, server):
    _add(corpus, 7)
    response = _post(server, {"id": 7, "status": "applied", "note": "отклик", "back": "/"}, fetch=True)
    assert response.status == 204
    with Corpus.open(db) as opened:
        application = opened.application(7)
    assert (application["status"], application["note"]) == ("applied", "отклик")


def test_empty_note_clears_it(db, corpus, server):
    _add(corpus, 7)
    corpus.set_status(7, "applied", "старая")
    _post(server, {"id": 7, "status": "applied", "note": "", "back": "/"}, fetch=True)
    with Corpus.open(db) as opened:
        assert opened.application(7)["note"] == ""


def test_no_js_redirects_to_same_view(corpus, server):
    _add(corpus, 7)
    with pytest.raises(HTTPError) as caught:
        _post(server, {"id": 7, "status": "test", "note": "", "back": "/?days=7&status=new"})
    assert caught.value.code == 303
    assert caught.value.headers["Location"] == "/?days=7&status=new#v-7"


def test_bad_status_and_unknown_id_are_400(corpus, server):
    _add(corpus, 7)
    for data in ({"id": 7, "status": "nope", "note": ""}, {"id": 999, "status": "applied", "note": ""}):
        with pytest.raises(HTTPError) as caught:
            _post(server, data, fetch=True)
        assert caught.value.code == 400


def test_back_cannot_leave_the_site(corpus, server):
    _add(corpus, 7)
    with pytest.raises(HTTPError) as caught:
        _post(server, {"id": 7, "status": "applied", "note": "", "back": "//evil.com"})
    assert caught.value.headers["Location"] == "/#v-7"
```

Импортировать `HTTPRedirectHandler`, `build_opener` из `urllib.request`.

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `uv run pytest tests/test_corpus_ui.py -v`
Expected: FAIL — 501/404 на `POST /save`.

- [ ] **Step 3: Реализовать `POST /save`**

Читать `Content-Length` (больше 64 КиБ → 413, не читать тело). `parse_qs`. Поля: `id` (int), `status` (из `STATUSES`, пустой — ошибка «Сначала выберите этап»), `note` (строка, по умолчанию `""`), `back` (по умолчанию `/`). `Corpus.open(db)`; `set_status(vacancy_id, status, note)`; `CorpusError` → 400. `back` принимается, только если начинается с `/` и не с `//`, иначе `/`; фрагмент из `back` отбрасывается. Ответ без тела, для редиректа — `Location: <back>#v-<id>`.

- [ ] **Step 4: Прогнать тесты**

Run: `uv run pytest tests/test_corpus_ui.py -v`
Expected: PASS, 20 тестов.

- [ ] **Step 5: Коммит**

```bash
git add hunter/corpus/ui.py tests/test_corpus_ui.py
git commit -m "feat: сохранение этапа и заметки из таблицы"
```

---

### Task 5: Команда `vacancies ui` и документация

**Files:**
- Modify: `hunter/corpus/cli.py` (импорт `ui`, `_cmd_ui`, `_COMMANDS`, `build_parser`)
- Modify: `hunter/corpus/GUIDE.md`
- Modify: `README.md`
- Test: `tests/test_corpus_ui.py`

**Interfaces:**
- Consumes: `hunter.corpus.ui.serve`, `hunter.config.load_config`, `hunter.paths.config()`.
- Produces: `vacancies ui [--port 8765] [--no-open]`; `_cmd_ui(args, fetch_page, sleep) -> int`.

- [ ] **Step 1: Написать падающие тесты**

```python
from hunter.corpus import ui


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(paths, "ME", tmp_path / "me")
    return tmp_path


def run(*argv):
    return main(list(argv), fetch_page=lambda url: "", sleep=lambda _: None)


def test_ui_command_passes_db_port_and_threshold(workdir, monkeypatch):
    assert run("init") == 0
    calls = {}
    monkeypatch.setattr(ui, "serve", lambda db, port, **kw: calls.update(db=db, port=port, kw=kw) or 0)
    assert run("ui", "--port", "9999", "--no-open") == 0
    assert calls["db"] == workdir / "me" / "job-hunter.db"
    assert calls["port"] == 9999
    assert calls["kw"] == {"open_browser": False, "threshold": 5}
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `uv run pytest tests/test_corpus_ui.py -v`
Expected: FAIL — `invalid choice: 'ui'`.

- [ ] **Step 3: Реализовать команду**

`_cmd_ui` читает порог: `load_config(paths.config())`, при `ConfigError` или `scoring is None` — 5; вызывает `ui.serve(args.db, args.port, open_browser=not args.no_open, threshold=threshold)`.
Парсер: `ui` с `--port` (int, 8765) и `--no-open`; справка «локальная таблица вакансий в браузере».

- [ ] **Step 4: Документация**

`hunter/corpus/GUIDE.md`, раздел после `export`: команда, адрес, что меняет (этап + заметка через `set_status`), что не меняет (вердикты, текст), порт и `--no-open`.
`README.md`: строка `vacancies ui  # локальная таблица вакансий в браузере` в шпаргалке и упоминание в таблице этапов (шаг 6 — воронка).

- [ ] **Step 5: Прогнать все тесты**

Run: `uv run pytest`
Expected: PASS, весь набор.

- [ ] **Step 6: Коммит**

```bash
git add hunter/corpus/cli.py hunter/corpus/GUIDE.md README.md tests/test_corpus_ui.py
git commit -m "feat: команда vacancies ui"
```

---

## Как проверим руками

1. `vacancies ui` → таблица открылась, название ведёт на hh, фильтры и сортировка работают.
2. Поставить этап и заметку в браузере → `vacancies funnel` и `vacancies show ID` показывают то же.
3. Отключить JS в браузере → сохранение проходит обычным POST и возвращает к тем же фильтрам.
4. Повторное сохранение той же заметки не двигает напоминания (`vacancies todo`).
