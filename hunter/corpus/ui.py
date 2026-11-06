from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from .store import STATUSES

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
