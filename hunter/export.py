from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.worksheet.datavalidation import DataValidation

from .corpus.store import STATUS_LABELS, Corpus

# Вакансия без строки в applications. В базе для неё статуса нет —
# это только подпись в таблице.
NEW_LABEL = "новая"

_HEADER = (
    "Ссылка", "Название", "Компания", "Оценка", "Почему", "Флаги",
    "Роль", "Статус", "Заметка", "Найдена",
)
_WIDTHS = (40, 50, 25, 9, 60, 35, 20, 16, 40, 12)
_COLUMNS = "ABCDEFGHIJ"
_STATUS_COLUMN = "H"

_SELECT = """
SELECT v.id, v.url, v.name, v.company, v.first_seen_at,
       s.score, s.why, s.flags,
       a.status, a.note,
       (SELECT role FROM hits WHERE vacancy_id = v.id
         ORDER BY first_found_at, role LIMIT 1) AS role
FROM vacancies v
LEFT JOIN latest_scores s ON s.vacancy_id = v.id
LEFT JOIN applications a ON a.vacancy_id = v.id
"""


def export_rows(corpus: Corpus, days: int | None) -> list[sqlite3.Row]:
    """Вакансии, найденные за последние days дней (None — все), свежие первыми."""
    sql, params = _SELECT, ()
    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        sql += "WHERE v.first_seen_at >= ? "
        params = (cutoff.isoformat(),)
    sql += "ORDER BY v.first_seen_at DESC, v.id DESC"
    return corpus._conn.execute(sql, params).fetchall()


def _flags(raw: str | None) -> str | None:
    # flags в latest_scores — JSON-массив; в таблице он читается как перечисление.
    if not raw:
        return None
    try:
        flags = json.loads(raw)
    except ValueError:
        return raw
    if not isinstance(flags, list):
        return raw
    return ", ".join(map(str, flags)) or None


def write_xlsx(rows: list[sqlite3.Row], path: Path) -> None:
    """Таблица для просмотра воронки: по строке на вакансию, в порядке rows.
    Статус можно менять руками, но источник правды — база (`vacancies status`)."""
    book = Workbook()
    sheet = book.active
    sheet.title = "Вакансии"
    sheet.append(_HEADER)
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    for row in rows:
        sheet.append((
            row["url"], row["name"], row["company"], row["score"], row["why"],
            _flags(row["flags"]), row["role"],
            STATUS_LABELS[row["status"]] if row["status"] else NEW_LABEL,
            row["note"], row["first_seen_at"][:10],
        ))
        link = sheet.cell(row=sheet.max_row, column=1)
        link.hyperlink = row["url"]
        link.style = "Hyperlink"

    for letter, width in zip(_COLUMNS, _WIDTHS):
        sheet.column_dimensions[letter].width = width
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{_COLUMNS[-1]}{sheet.max_row}"

    # Валидация с запасом строк: добавленные руками вакансии тоже получат список.
    labels = (NEW_LABEL, *STATUS_LABELS.values())
    status = DataValidation(type="list", formula1='"' + ",".join(labels) + '"')
    status.add(f"{_STATUS_COLUMN}2:{_STATUS_COLUMN}1000")
    sheet.add_data_validation(status)

    book.save(path)
