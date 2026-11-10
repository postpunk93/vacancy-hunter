from __future__ import annotations

import json
from sqlite3 import Row

from ..platforms import format_ref
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


def _ref(v: Row) -> str:
    """Как владелец зовёт вакансию в командах: у hh — голый id."""
    return format_ref(v["source"], v["external_id"])


def _body(v: Row) -> str:
    if v["description"]:
        return v["description"]
    if v["status"] == "deleted":
        return "_Вакансия удалена с hh, текста нет._"
    return f"_Полного текста нет — `vacancies fetch {_ref(v)}`._"


def render_vacancy(vacancy: Row, hits: list[Row], verdict: Row | None) -> str:
    v = vacancy
    meta = [f"id {_ref(v)}"]
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
