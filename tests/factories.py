from __future__ import annotations

import html as html_module
import json
from datetime import datetime, timezone

from hunter.corpus.extract import Detail, vacancy_card_row
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
    experience="between1And3",
    is_ad=False,
)


def make_vacancy(**overrides) -> Vacancy:
    """Конструктор вакансии для тестов: задавайте только то, что проверяете."""
    return Vacancy(**{**_DEFAULTS, **overrides})


def state_page(state: dict) -> str:
    """Страница с HH-Lux-InitialState — как её отдаёт hh, только без вёрстки."""
    payload = html_module.escape(json.dumps(state, ensure_ascii=False))
    return f'<html><body><template id="HH-Lux-InitialState">{payload}</template></body></html>'


def search_card(vacancy_id: int, **overrides) -> dict:
    card = {
        "vacancyId": vacancy_id,
        "name": f"Вакансия {vacancy_id}",
        "company": {"id": 1, "visibleName": "Компания", "@trusted": True, "@category": "COMPANY"},
        "compensation": {"from": 300000, "currencyCode": "RUR", "gross": False, "frequency": "MONTHLY"},
        "area": {"name": "Москва"},
        "publicationTime": {"$": "2026-10-01T10:00:00.000+03:00"},
        "workExperience": "between3And6",
        "workFormats": [{"workFormatsElement": ["REMOTE"]}],
        "employmentForm": "FULL",
        "responsesCount": 5,
    }
    card.update(overrides)
    return card


def search_page(cards: list[dict]) -> str:
    return state_page({"vacancySearchResult": {"vacancies": cards}})


def detail_vacancy(vacancy_id: int, **overrides) -> dict:
    vacancy = {
        "vacancyId": vacancy_id,
        "name": f"Вакансия {vacancy_id}",
        "description": "<p>Пишем на <strong>Python</strong></p>",
        "keySkills": ["Python", "SQL"],
        "status": {"archived": False},
        "company": {"id": 1, "visibleName": "Компания", "trusted": True, "category": "COMPANY"},
        "area": {"name": "Москва"},
        "compensation": {"to": 280000, "currencyCode": "RUR", "gross": False, "frequency": "TWICE_PER_MONTH"},
        "publicationTimeIso": "2026-10-02T14:49:07.426+03:00",
        "workExperience": "between3And6",
        "employmentForm": "FULL",
        "workFormats": ["ON_SITE"],
    }
    vacancy.update(overrides)
    return vacancy


def vacancy_page(vacancy_id: int, **overrides) -> str:
    return state_page({"vacancyView": {"vacancyFull": {"vacancy": detail_vacancy(vacancy_id, **overrides)}}})


def make_detail(vacancy_id: int = 134991226, **overrides) -> Detail:
    """Страница вакансии для тестов: строка — как у вакансии из выдачи, поля меняются точечно."""
    fields = dict(
        vacancy_id=vacancy_id,
        row=vacancy_card_row(make_vacancy(vacancy_id=str(vacancy_id))),
        description="Описание",
        key_skills=[],
        status="active",
        raw={"vacancyId": vacancy_id},
    )
    return Detail(**{**fields, **overrides})
