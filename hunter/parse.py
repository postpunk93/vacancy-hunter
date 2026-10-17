from __future__ import annotations

import html as html_module
import json
import re
from datetime import datetime, timezone

from .models import UNKNOWN_COMPANY, UNKNOWN_VACANCY_NAME, Vacancy

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
    try:
        return [_to_vacancy(raw, source) for raw in result["vacancies"]]
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ParseError(
            f"Карточка вакансии не разобралась "
            f"({exc.__class__.__name__}: {exc}) — сайт сменил формат"
        ) from exc


def _to_vacancy(raw: dict, source: str) -> Vacancy:
    compensation = raw.get("compensation") or {}
    company = raw.get("company") or {}
    area = raw.get("area") or {}
    links = raw.get("links") or {}
    timestamp = (raw.get("publicationTime") or {}).get("@timestamp")

    return Vacancy(
        source=source,
        vacancy_id=str(raw["vacancyId"]),
        name=raw.get("name") or UNKNOWN_VACANCY_NAME,
        company=(
            company.get("visibleName")
            or company.get("name")
            or UNKNOWN_COMPANY
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
        experience=raw.get("workExperience"),
        is_ad=bool(raw.get("@isAdv")),
    )
