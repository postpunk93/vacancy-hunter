from __future__ import annotations

import errno
import html
import json
import sqlite3
import sys
import webbrowser
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from ..export import export_rows
from .store import STATUSES, STATUS_LABELS, Corpus, CorpusError

SORTS = ("found", "score", "name")

# Тело POST /save больше этого не читаем: одна строка формы — это сотни байт.
_MAX_BODY = 64 * 1024


@dataclass(frozen=True)
class Filters:
    days: int | None = None
    min_score: int | None = None
    status: str | None = None
    q: str = ""
    sort: str = "found"
    desc: bool = True

    def query(self, **changes) -> str:
        """Query string без «?»: только поля, отличные от значений по умолчанию."""
        current = replace(self, **changes)
        pairs: list[tuple[str, str]] = []
        if current.days is not None:
            pairs.append(("days", str(current.days)))
        if current.min_score is not None:
            pairs.append(("min_score", str(current.min_score)))
        if current.status is not None:
            pairs.append(("status", current.status))
        if current.q:
            pairs.append(("q", current.q))
        if current.sort != "found":
            pairs.append(("sort", current.sort))
        if not current.desc:
            pairs.append(("desc", "0"))
        return urlencode(pairs)


def _first(params: dict[str, list[str]], key: str) -> str:
    values = params.get(key) or []
    return values[0] if values else ""


def parse_filters(params: dict[str, list[str]]) -> Filters:
    days = None
    raw_days = _first(params, "days")
    if raw_days in ("7", "30"):
        days = int(raw_days)

    min_score = None
    raw_min = _first(params, "min_score").strip()
    if raw_min:
        try:
            parsed = int(raw_min)
        except ValueError:
            parsed = -1
        if parsed >= 0:
            min_score = parsed

    status = _first(params, "status")
    if status != "new" and status not in STATUSES:
        status = None

    sort = _first(params, "sort")
    if sort not in SORTS:
        sort = "found"

    return Filters(
        days=days,
        min_score=min_score,
        status=status,
        q=_first(params, "q").strip(),
        sort=sort,
        desc=_first(params, "desc") != "0",
    )


def _by_id_desc(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    return sorted(rows, key=lambda row: row["id"], reverse=True)


def select_rows(rows: list[sqlite3.Row], filters: Filters) -> list[sqlite3.Row]:
    """Фильтрует и сортирует строки выгрузки; регистронезависимость — в Python,
    потому что lower()/LIKE в SQLite работают так только для латиницы."""
    selected = rows

    if filters.days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=filters.days)
        selected = [
            row for row in selected
            if datetime.fromisoformat(row["first_seen_at"]) >= cutoff
        ]

    if filters.min_score is not None:
        selected = [
            row for row in selected
            if row["score"] is not None and row["score"] >= filters.min_score
        ]

    if filters.status == "new":
        selected = [row for row in selected if row["status"] is None]
    elif filters.status is not None:
        selected = [row for row in selected if row["status"] == filters.status]

    if filters.q:
        needle = filters.q.casefold()
        selected = [
            row for row in selected
            if needle in f"{row['name'] or ''} {row['company'] or ''}".casefold()
        ]

    # Стабильная сортировка в два прохода: второй проход задаёт основной
    # порядок, первый — порядок равных по id (всегда убывающий).
    if filters.sort == "score":
        scored = [row for row in selected if row["score"] is not None]
        unscored = [row for row in selected if row["score"] is None]
        scored = _by_id_desc(scored)
        scored.sort(key=lambda row: row["score"], reverse=filters.desc)
        return scored + _by_id_desc(unscored)

    ordered = _by_id_desc(selected)
    if filters.sort == "name":
        ordered.sort(key=lambda row: (row["name"] or "").casefold(), reverse=filters.desc)
    else:
        ordered.sort(key=lambda row: row["first_seen_at"] or "", reverse=filters.desc)
    return ordered


_STYLE = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; padding: 24px; font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; color: #1f2328; background: #f6f7f9; }
h1 { margin: 0 0 16px; font-size: 20px; }
.filters { display: flex; flex-wrap: wrap; gap: 12px; align-items: end; margin-bottom: 16px; padding: 12px; background: #fff; border: 1px solid #d8dee4; border-radius: 8px; }
.filters label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: #57606a; }
.filters input, .filters select { padding: 5px 8px; font: inherit; color: inherit; background: #fff; border: 1px solid #d0d7de; border-radius: 6px; }
.filters button { padding: 6px 14px; font: inherit; cursor: pointer; background: #1f6feb; color: #fff; border: 1px solid #1f6feb; border-radius: 6px; }
.wrap { overflow: auto; background: #fff; border: 1px solid #d8dee4; border-radius: 8px; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 7px 10px; text-align: left; vertical-align: top; border-bottom: 1px solid #eaeef2; }
th { position: sticky; top: 0; z-index: 1; font-size: 12px; white-space: nowrap; background: #f6f8fa; }
th a { color: #1f2328; text-decoration: none; }
th a:hover { text-decoration: underline; }
td.name { min-width: 240px; max-width: 420px; }
td.name a { color: #0969da; text-decoration: none; }
td.name a:hover { text-decoration: underline; }
td.why, td.flags { max-width: 260px; color: #57606a; }
td.score { font-weight: 600; white-space: nowrap; }
td.score.good { color: #1a7f37; }
td.score.near { color: #9a6700; }
td.score.low, td.score.none { color: #8c959f; font-weight: 400; }
td.stage { min-width: 150px; }
td.stage select { width: 100%; padding: 4px 6px; font: inherit; }
td.note { min-width: 220px; }
td.note input { width: 100%; padding: 4px 6px; font: inherit; border: 1px solid #d0d7de; border-radius: 6px; }
td.note .row-actions { display: flex; gap: 8px; align-items: center; margin-top: 4px; }
td.note button { padding: 3px 10px; font: inherit; cursor: pointer; border: 1px solid #d0d7de; border-radius: 6px; background: #f6f8fa; }
td.note button:disabled { cursor: default; opacity: .6; }
.flash { font-size: 12px; color: #1a7f37; }
tr.saved td { background: #dafbe1; }
.count { margin-top: 12px; color: #57606a; }
"""

_SCRIPT = """
document.querySelectorAll("form.save").forEach(function (form) {
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var row = form.closest("tr");
    var stage = form.querySelector("select[name=status]");
    var back = form.querySelector("input[name=back]");
    var button = row.querySelector("button[type=submit]");
    var flash = row.querySelector(".flash");
    // Этап поменялся — после сохранения перечитываем список с теми же
    // фильтрами: строка может из них выпасть или войти в них.
    var stageChanged = stage.value !== form.dataset.status;
    button.disabled = true;
    flash.textContent = "";
    fetch("/save", {
      method: "POST",
      headers: {"X-Requested-With": "fetch"},
      body: new URLSearchParams(new FormData(form))
    }).then(function (response) {
      if (!response.ok) {
        return response.text().then(function (text) { flash.textContent = text; });
      }
      if (stageChanged) {
        window.location.href = back.value + "#" + row.id;
        return;
      }
      row.classList.add("saved");
      flash.textContent = "сохранено";
      setTimeout(function () {
        row.classList.remove("saved");
        flash.textContent = "";
      }, 2000);
    }).catch(function () {
      flash.textContent = "не удалось сохранить";
    }).finally(function () {
      button.disabled = false;
    });
  });
});
"""

_HEADERS = (
    "Название", "Компания", "Оценка", "Почему", "Флаги", "Роль", "Этап", "Заметка", "Найдена",
)

_SORT_KEYS = {
    "Название": "name",
    "Оценка": "score",
    "Найдена": "found",
}


def _esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _plain(value) -> str:
    return "" if value is None else str(value)


def _short(value, limit: int = 120) -> str:
    text = _plain(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _flags_text(raw) -> str:
    if not raw:
        return ""
    try:
        flags = json.loads(raw)
    except ValueError:
        return _plain(raw)
    if not isinstance(flags, list):
        return _plain(raw)
    return ", ".join(map(str, flags))


def _score_cell(score, threshold: int) -> str:
    if score is None:
        return '<td class="score none">—</td>'
    if score >= threshold:
        kind = "good"
    elif score >= 3:
        kind = "near"
    else:
        kind = "low"
    return f'<td class="score {kind}">{_esc(score)}</td>'


def _sort_header(label: str, filters: Filters) -> str:
    key = _SORT_KEYS[label]
    desc = not filters.desc if filters.sort == key else True
    query = filters.query(sort=key, desc=desc)
    href = "/" + ("?" + query if query else "")
    arrow = ""
    if filters.sort == key:
        arrow = " ▼" if filters.desc else " ▲"
    return f'<th><a href="{_esc(href)}">{label}{arrow}</a></th>'


def _stage_select(row) -> str:
    current = row["status"]
    options = ['<option value="">новая</option>']
    for status in STATUSES:
        selected = " selected" if status == current else ""
        options.append(
            f'<option value="{status}"{selected}>{_esc(STATUS_LABELS[status])}</option>'
        )
    return '<select name="status">' + "".join(options) + "</select>"


def _filter_form(filters: Filters) -> str:
    days = '<option value="">всё</option>'
    for value, label in ((7, "7 дней"), (30, "30 дней")):
        selected = " selected" if filters.days == value else ""
        days += f'<option value="{value}"{selected}>{label}</option>'

    stages = '<option value="">все</option>'
    stages += '<option value="new"' + (" selected" if filters.status == "new" else "") + ">новая</option>"
    for status in STATUSES:
        selected = " selected" if filters.status == status else ""
        stages += f'<option value="{status}"{selected}>{_esc(STATUS_LABELS[status])}</option>'

    score = "" if filters.min_score is None else str(filters.min_score)
    sort = _esc(filters.sort)
    desc = "1" if filters.desc else "0"

    return (
        '<form class="filters" method="get" action="/">'
        f'<input type="hidden" name="sort" value="{sort}">'
        f'<input type="hidden" name="desc" value="{desc}">'
        f'<label>Период<select name="days">{days}</select></label>'
        f'<label>Оценка от<input type="number" name="min_score" min="0" value="{_esc(score)}"></label>'
        f'<label>Этап<select name="status">{stages}</select></label>'
        f'<label>Поиск<input type="search" name="q" value="{_esc(filters.q)}"></label>'
        '<button type="submit">Показать</button>'
        "</form>"
    )


def _row(row, *, filters: Filters, threshold: int) -> str:
    back = "/" + ("?" + filters.query() if filters.query() else "")
    why = _plain(row["why"])
    flags = _flags_text(row["flags"])
    return (
        f'<tr id="v-{_esc(row["id"])}">'
        f'<td class="name"><a href="{_esc(row["url"])}" target="_blank" rel="noopener">'
        f'{_esc(row["name"])}</a></td>'
        f'<td>{_esc(row["company"])}</td>'
        + _score_cell(row["score"], threshold)
        + f'<td class="why" title="{_esc(why)}">{_esc(_short(why))}</td>'
        f'<td class="flags" title="{_esc(flags)}">{_esc(_short(flags))}</td>'
        f'<td>{_esc(row["role"])}</td>'
        f'<td class="stage"><form class="save" id="f-{_esc(row["id"])}" method="post" action="/save"'
        f' data-status="{_esc(_plain(row["status"]))}">'
        f'<input type="hidden" name="id" value="{_esc(row["id"])}">'
        f'<input type="hidden" name="back" value="{_esc(back)}">'
        + _stage_select(row)
        + "</form></td>"
        f'<td class="note"><input type="text" name="note" form="f-{_esc(row["id"])}" '
        f'value="{_esc(row["note"])}">'
        f'<div class="row-actions"><button type="submit" form="f-{_esc(row["id"])}">Сохранить</button>'
        '<span class="flash"></span></div></td>'
        f'<td>{_esc(_plain(row["first_seen_at"])[:10])}</td>'
        "</tr>"
    )


def render_page(
    rows: list[sqlite3.Row], *, filters: Filters, threshold: int, total: int
) -> str:
    """Страница таблицы: фильтры, строки с формами сохранения и счётчик."""
    headers = []
    for label in _HEADERS:
        if label in _SORT_KEYS:
            headers.append(_sort_header(label, filters))
        else:
            headers.append(f"<th>{label}</th>")

    body = "".join(_row(row, filters=filters, threshold=threshold) for row in rows)
    if not rows:
        body = '<tr><td colspan="9" class="count">Ничего не найдено</td></tr>'

    return (
        "<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Вакансии</title><style>" + _STYLE + "</style></head><body>"
        "<h1>Вакансии</h1>"
        + _filter_form(filters)
        + '<div class="wrap"><table><thead><tr>'
        + "".join(headers)
        + "</tr></thead><tbody>"
        + body
        + "</tbody></table></div>"
        + f'<p class="count">Показано {len(rows)} из {total}</p>'
        "<script>" + _SCRIPT + "</script></body></html>"
    )


class _Handler(BaseHTTPRequestHandler):
    db: Path
    threshold: int = 5

    def _respond(self, status: int, body: str = "",
                 content_type: str = "text/plain; charset=utf-8") -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Cache-Control", "no-store")
        if payload:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        if url.path == "/":
            try:
                with Corpus.open_readonly(self.db) as corpus:
                    all_rows = export_rows(corpus, None)
                    filters = parse_filters(parse_qs(url.query))
                    rows = select_rows(all_rows, filters)
                    page = render_page(
                        rows, filters=filters, threshold=self.threshold, total=len(all_rows),
                    )
            except CorpusError as exc:
                self._respond(500, str(exc))
                return
            self._respond(200, page, "text/html; charset=utf-8")
        elif url.path == "/favicon.ico":
            self._respond(204)
        else:
            self._respond(404, "Нет такой страницы")

    def do_POST(self) -> None:
        url = urlsplit(self.path)
        if url.path != "/save":
            self._respond(404, "Нет такой страницы")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > _MAX_BODY:
            self._respond(413, "Слишком большое тело запроса")
            return
        raw = self.rfile.read(length) if length > 0 else b""
        data = parse_qs(raw.decode("utf-8", errors="replace"))

        def field(name: str, default: str = "") -> str:
            values = data.get(name) or []
            return values[0] if values else default

        try:
            vacancy_id = int(field("id"))
        except ValueError:
            self._respond(400, "Неверная вакансия")
            return
        status = field("status")
        if not status:
            self._respond(400, "Сначала выберите этап")
            return
        if status not in STATUSES:
            self._respond(400, "Неверный этап")
            return
        note = field("note")
        back = field("back", "/") or "/"
        if not back.startswith("/") or back.startswith("//") or "\\" in back:
            back = "/"
        back = back.split("#", 1)[0]

        try:
            with Corpus.open(self.db) as corpus:
                corpus.set_status(vacancy_id, status, note)
        except CorpusError as exc:
            self._respond(400, str(exc))
            return

        if self.headers.get("X-Requested-With") == "fetch":
            self._respond(204)
            return
        self.send_response(303)
        self.send_header("Location", f"{back}#v-{vacancy_id}")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args) -> None:
        pass


def make_server(
    db: Path, port: int = 0, *, host: str = "127.0.0.1", threshold: int = 5,
) -> ThreadingHTTPServer:
    """Сервер на localhost; базу не читает, пока не пришёл первый запрос."""
    handler = type("_BoundHandler", (_Handler,), {"db": Path(db), "threshold": threshold})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def serve(
    db: Path, port: int = 8765, *, open_browser: bool = True,
    host: str = "127.0.0.1", threshold: int = 5,
) -> int:
    """Поднимает таблицу и держит её до Ctrl+C. Возвращает код выхода."""
    db = Path(db)
    if not db.exists():
        print(f"База {db} не найдена. Сначала выполните vacancies init.", file=sys.stderr)
        return 2
    try:
        server = make_server(db, port, host=host, threshold=threshold)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            print(f"Порт {port} занят, попробуйте --port", file=sys.stderr)
            return 2
        raise
    url = f"http://{host}:{server.server_address[1]}"
    print(f"Таблица: {url}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0
