from __future__ import annotations

import html
import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from .store import STATUSES, STATUS_LABELS

SORTS = ("found", "score", "name")


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
    var button = row.querySelector("button[type=submit]");
    var flash = row.querySelector(".flash");
    button.disabled = true;
    flash.textContent = "";
    fetch("/save", {
      method: "POST",
      headers: {"X-Requested-With": "fetch"},
      body: new URLSearchParams(new FormData(form))
    }).then(function (response) {
      if (response.ok) {
        row.classList.add("saved");
        flash.textContent = "сохранено";
        setTimeout(function () {
          row.classList.remove("saved");
          flash.textContent = "";
        }, 2000);
      } else {
        return response.text().then(function (text) { flash.textContent = text; });
      }
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
        f'<td class="stage"><form class="save" id="f-{_esc(row["id"])}" method="post" action="/save">'
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
