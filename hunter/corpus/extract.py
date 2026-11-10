from __future__ import annotations

import json
from dataclasses import dataclass

from ..models import UNKNOWN_COMPANY, UNKNOWN_VACANCY_NAME, Vacancy
from ..parse import ParseError, extract_state
from .markdown import html_to_markdown

# Один адрес для вакансий hh и zarplata: у zarplata те же id (кросспостинг hh).
VACANCY_URL = "https://hh.ru/vacancy/{id}"

_STATE_MARKER = 'id="HH-Lux-InitialState"'
_ANTIFRAUD = "HH_ANTIFRAUD_SUSPICIOUS_VACANCY"


class Captcha(Exception):
    """Ответ 200, но без состояния страницы — капча или заглушка.
    По смыслу это блокировка, как 403: продолжать нельзя."""


def _page_state(page_html: str) -> dict:
    # Нет маркера вовсе — нам подсунули другую страницу. Маркер есть, но JSON
    # не разобрался — это уже смена формата, ParseError из extract_state.
    if _STATE_MARKER not in page_html:
        raise Captcha("в ответе нет HH-Lux-InitialState — похоже на капчу")
    return extract_state(page_html)


def search_cards(page_html: str) -> list[dict]:
    result = _page_state(page_html).get("vacancySearchResult")
    if not isinstance(result, dict) or not isinstance(result.get("vacancies"), list):
        raise ParseError("В состоянии страницы нет vacancySearchResult.vacancies")
    return result["vacancies"]


def _flag(value) -> int | None:
    return None if value is None else int(bool(value))


def _identity(raw: dict, company: dict) -> dict:
    vacancy_id = int(raw["vacancyId"])
    return {
        "id": vacancy_id,
        "name": raw.get("name") or UNKNOWN_VACANCY_NAME,
        "company": company.get("visibleName") or company.get("name") or UNKNOWN_COMPANY,
        "company_id": company.get("id"),
        "url": VACANCY_URL.format(id=vacancy_id),
        "area": (raw.get("area") or {}).get("name"),
    }


def _salary(compensation: dict) -> dict:
    return {
        "salary_from": compensation.get("from"),
        "salary_to": compensation.get("to"),
        "currency": compensation.get("currencyCode"),
        "gross": _flag(compensation.get("gross")),
        "salary_frequency": compensation.get("frequency"),
    }


def _antifraud_power(raw: dict) -> float | None:
    for block in (raw.get("vacancyProperties") or {}).get("properties") or []:
        for prop in block.get("property") or []:
            if prop.get("propertyType") != _ANTIFRAUD:
                continue
            for params in prop.get("parameters") or []:
                for param in params.get("parameter") or []:
                    if param.get("key") == "antifraudPessimizePower":
                        return float(param["value"])
            # Свойство есть, силы нет — всё равно помечена антифродом.
            return 0.0
    return None


def card_row(raw: dict) -> dict:
    """Карточка из выдачи → колонки vacancies, кроме отметок времени
    и полей страницы вакансии."""
    company = raw.get("company") or {}
    return {
        **_identity(raw, company),
        "published_at": (raw.get("publicationTime") or {}).get("$"),
        **_salary(raw.get("compensation") or {}),
        "experience": raw.get("workExperience"),
        "work_formats": json.dumps([
            element
            for block in (raw.get("workFormats") or [])
            for element in (block.get("workFormatsElement") or [])
        ]),
        "employment_form": raw.get("employmentForm"),
        "employer_trusted": _flag(company.get("@trusted")),
        "employer_category": company.get("@category"),
        "antifraud_power": _antifraud_power(raw),
        "responses_count": raw.get("responsesCount"),
        "card_json": json.dumps(raw, ensure_ascii=False),
    }


def vacancy_card_row(v: Vacancy) -> dict:
    """Вакансия, разобранная проходом, → колонки vacancies. Проход видит меньше,
    чем полная карточка: чего нет в Vacancy, остаётся NULL и дополнится
    при сборе корпуса или скачивании страницы."""
    vacancy_id = int(v.vacancy_id)
    return {
        "id": vacancy_id,
        "name": v.name,
        "company": v.company,
        "company_id": None,
        "url": VACANCY_URL.format(id=vacancy_id),
        "area": v.area,
        "published_at": v.published_at.isoformat(),
        "salary_from": v.salary_from,
        "salary_to": v.salary_to,
        "currency": v.currency,
        "gross": _flag(v.gross),
        "salary_frequency": None,
        "experience": v.experience,
        "work_formats": json.dumps(list(v.work_formats)),
        "employment_form": None,
        "employer_trusted": None,
        "employer_category": None,
        "antifraud_power": None,
        "responses_count": None,
        "card_json": None,
    }


@dataclass(frozen=True, slots=True)
class Detail:
    """Страница вакансии: полный текст плюс общие поля — на случай,
    если вакансию добавляют по ссылке и в выдаче её не было."""

    vacancy_id: int
    row: dict
    description: str
    key_skills: list[str]
    status: str
    raw: dict

    # Переходное: пара для записи в базу v4. У hh и zarplata id общие,
    # поэтому площадка всегда hh. Уйдёт вместе с этим модулем в Task 3.
    @property
    def source(self) -> str:
        return "hh"

    @property
    def external_id(self) -> str:
        return str(self.vacancy_id)


def parse_detail(page_html: str) -> Detail:
    state = _page_state(page_html)
    try:
        raw = state["vacancyView"]["vacancyFull"]["vacancy"]
        vacancy_id = int(raw["vacancyId"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ParseError(
            f"На странице нет vacancyView.vacancyFull.vacancy ({exc!r}) — сайт сменил формат"
        ) from exc
    # На странице вакансии у company нет префикса @, а workFormats — плоский список.
    company = raw.get("company") or {}
    row = {
        **_identity(raw, company),
        "published_at": raw.get("publicationTimeIso"),
        **_salary(raw.get("compensation") or {}),
        "experience": raw.get("workExperience"),
        "work_formats": json.dumps(list(raw.get("workFormats") or [])),
        "employment_form": raw.get("employmentForm"),
        "employer_trusted": _flag(company.get("trusted")),
        "employer_category": company.get("category"),
        "antifraud_power": None,
        "responses_count": None,
        "card_json": None,
    }
    return Detail(
        vacancy_id=vacancy_id,
        row=row,
        description=html_to_markdown(raw.get("description") or ""),
        key_skills=[str(skill) for skill in raw.get("keySkills") or []],
        status="archived" if (raw.get("status") or {}).get("archived") else "active",
        raw=raw,
    )
