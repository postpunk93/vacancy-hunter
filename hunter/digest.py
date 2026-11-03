from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from .filters import Group
from .models import Score, Vacancy
from .text import plural

MAX_MESSAGE_CHARS = 4096

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
_EXPERIENCE = {
    "noExperience": "без опыта",
    "between1And3": "1–3 года",
    "between3And6": "3–6 лет",
    "moreThan6": "более 6 лет",
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
    return plural(count, "новая вакансия", "новые вакансии", "новых вакансий")


def _score_label(score: Score | None) -> str:
    """«8», «?» — не оценена, «6*» — оценена по одной карточке."""
    if score is None or not score.scored:
        return "?"
    return f"{score.value}*" if score.from_card else str(score.value)


def _format_vacancy(vacancy: Vacancy, scores: Mapping[str, Score] | None) -> str:
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

    prefix = "" if scores is None else f"{_score_label(scores.get(vacancy.key))} · "
    return (
        f'• {prefix}<a href="{escape(vacancy.url)}">{escape(vacancy.name)}</a>\n'
        f'  {escape(" · ".join(details))}'
    )




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


def _format_brief(vacancy: Vacancy, score: Score | None, *, html: bool) -> str:
    """Одна строка для серой зоны: оценка, название, компания, з/п, ссылка, причина."""
    label = _score_label(score)
    why = ""
    if score is not None and score.scored and score.why:
        why = f" — {score.why}"
    if html:
        name = f'<a href="{escape(vacancy.url)}">{escape(vacancy.name)}</a>'
        details = f"{escape(vacancy.company)} · {escape(format_salary(vacancy))}"
        return f"• {label} · {name} — {details}{escape(why)}"
    return (
        f"  {label} · {vacancy.name} — {vacancy.company} · "
        f"{format_salary(vacancy)} · {_console_url(vacancy)}{why}"
    )


def _brief_block(
    title: str,
    vacancies: list[Vacancy],
    scores: Mapping[str, Score] | None,
    *,
    html: bool,
    limit: int | None,
) -> str:
    """Блок серой зоны. В Телеграме он короткий: остальное лежит в базе."""
    shown = vacancies if limit is None else vacancies[:limit]
    body = "\n".join(
        _format_brief(v, None if scores is None else scores.get(v.key), html=html)
        for v in shown
    )
    block = f"{title} ({len(vacancies)})\n{body}"
    if limit is not None and len(vacancies) > limit:
        block += f"\nещё {len(vacancies) - limit} в базе"
    return block


def render(
    groups: list[Group],
    now: datetime,
    warnings: tuple[str, ...] = (),
    *,
    scores: Mapping[str, Score] | None = None,
    below: int = 0,
    threshold: int = 5,
    nearby: list[Vacancy] | None = None,
    unscored: list[Vacancy] | None = None,
    nearby_limit: int = 40,
    unscored_limit: int = 20,
) -> list[str]:
    """Собирает дайджест из всех переданных групп, без потолка. Длинный
    дайджест режется на сообщения по границе группы, а слишком длинная
    группа — по границе строки. С scores у каждой вакансии стоит оценка,
    below — сколько вакансий не прошло порог и осталось только в базе.
    nearby и unscored — серая зона: в Телеграме показываются с потолком."""
    nearby = nearby or []
    unscored = unscored or []
    filled = [(name, vacancies) for name, vacancies in groups if vacancies]
    total = sum(len(v) for _, v in filled)
    if not total and not below and not nearby and not unscored and not warnings:
        return []

    blocks: list[str] = []
    stamp = f"{now.day} {_MONTHS[now.month - 1]}, {now:%H:%M}"
    if total:
        header = f"🔎 {_plural_vacancies(total)} · {stamp}"
        if below:
            header += f" · ещё {below} ниже {threshold} — в базе, выгрузка: vacancies export"
        blocks.append(header)
        for name, vacancies in filled:
            body = "\n".join(_format_vacancy(v, scores) for v in vacancies)
            blocks.append(f"<b>{escape(name)}</b>\n{body}")
    elif below:
        blocks.append(
            f"🔎 {stamp} · все {_plural_vacancies(below)} ниже {threshold} — в базе, выгрузка: vacancies export"
        )
    elif nearby or unscored:
        blocks.append(f"🔎 {stamp}")

    if nearby:
        blocks.append(
            _brief_block("Рядом", nearby, scores, html=True, limit=nearby_limit)
        )
    if unscored:
        blocks.append(
            _brief_block("Без оценки", unscored, scores, html=True, limit=unscored_limit)
        )

    for warning in warnings:
        blocks.append(f"⚠️ {escape(warning)}")

    return _pack(blocks, MAX_MESSAGE_CHARS)


def _console_url(vacancy: Vacancy) -> str:
    """Короткая ссылка без поддомена города: так же её принимает vacancies."""
    if vacancy.source == "hh":
        return f"https://hh.ru/vacancy/{vacancy.vacancy_id}"
    return vacancy.url


def render_console(
    groups: list[Group],
    *,
    scores: Mapping[str, Score] | None,
    below: int,
    threshold: int,
    warnings: tuple[str, ...],
    nearby: list[Vacancy] | None = None,
    unscored: list[Vacancy] | None = None,
) -> str:
    """Сводка прохода для консоли: обычный текст, без HTML и лимита Телеграма.
    Без scores (оценка выключена) вакансии идут без оценки в начале строки.
    nearby и unscored печатаются целиком: консоль не режется на сообщения."""
    nearby = nearby or []
    unscored = unscored or []
    filled = [(name, vacancies) for name, vacancies in groups if vacancies]
    total = sum(len(v) for _, v in filled)

    if total:
        header = _plural_vacancies(total).capitalize()
        if below:
            header += f" · ещё {below} ниже порога {threshold}"
    elif below:
        header = f"Все {_plural_vacancies(below)} ниже порога {threshold}"
    else:
        header = "Новых вакансий нет"

    lines = [header]
    for name, vacancies in filled:
        lines.append("")
        lines.append(name)
        for vacancy in vacancies:
            item = None if scores is None else scores.get(vacancy.key)
            prefix = "" if scores is None else f"{_score_label(item)} · "
            why = f" — {item.why}" if item is not None and item.scored else ""
            lines.append(
                f"  {prefix}{vacancy.name} — {vacancy.company} · "
                f"{format_salary(vacancy)} · {_console_url(vacancy)}{why}"
            )
    if nearby:
        lines.append("")
        lines.append(_brief_block("Рядом", nearby, scores, html=False, limit=None))
    if unscored:
        lines.append("")
        lines.append(_brief_block("Без оценки", unscored, scores, html=False, limit=None))
    if warnings:
        lines.append("")
        lines.extend(f"⚠️ {warning}" for warning in warnings)
    return "\n".join(lines)
