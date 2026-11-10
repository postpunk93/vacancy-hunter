"""Общее для площадок: карточка выдачи, страница вакансии и протокол.

Площадка — это сайт со своей выдачей, своим форматом карточки и своей
страницей вакансии. Всё, что знает о конкретном сайте, живёт в его модуле
(`hh.py`, `superjob.py`); остальной код спрашивает площадку.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ..models import SearchQuery, Vacancy


class Captcha(Exception):
    """Ответ 200 без состояния страницы — капча или заглушка."""


class UnknownPlatform(Exception):
    """Площадки нет в PLATFORMS. Сообщение перечисляет доступные."""


@dataclass(frozen=True, slots=True)
class Card:
    """Карточка из выдачи: поля для дайджеста и строка для корпуса.

    Парсер один: `vacancy` собирает то, что нужно дайджесту, `row` — колонки
    таблицы vacancies. published_at бывает None: у superjob даты в карточке нет.
    """

    source: str
    external_id: str
    name: str
    company: str
    url: str
    area: str
    published_at: datetime | None
    salary_from: int | None
    salary_to: int | None
    currency: str | None
    gross: bool | None
    salary_mode: str | None
    work_formats: tuple[str, ...]
    experience: str | None
    is_ad: bool
    row: dict[str, object]

    @property
    def key(self) -> str:
        """Ключ вакансии в базе: у площадок id живут в разных пространствах."""
        return f"{self.source}:{self.external_id}"

    @property
    def vacancy(self) -> Vacancy:
        return Vacancy(
            source=self.source,
            vacancy_id=self.external_id,
            name=self.name,
            company=self.company,
            url=self.url,
            area=self.area,
            published_at=self.published_at,
            salary_from=self.salary_from,
            salary_to=self.salary_to,
            currency=self.currency,
            gross=self.gross,
            salary_mode=self.salary_mode,
            work_formats=self.work_formats,
            experience=self.experience,
            is_ad=self.is_ad,
        )


@dataclass(frozen=True, slots=True)
class Detail:
    """Страница вакансии: полный текст и метаданные, которых не было в карточке."""

    source: str
    external_id: str
    row: dict[str, object]
    description: str
    key_skills: list[str]
    status: str
    raw: dict


class Platform(Protocol):
    """Что умеет площадка. Методы разбора бросают ParseError, если сайт
    сменил формат, и Captcha, если вместо страницы пришла заглушка."""

    name: str
    page_size: int
    max_pages: int

    def search_url(self, query: SearchQuery, page: int = 0) -> str: ...

    def search_period(self, days: int) -> int:
        """Ближайший период, который площадка умеет отдавать, не короче запрошенного."""
        ...

    def parse_cards(self, page_html: str) -> list[Card]: ...

    def vacancy_url(self, external_id: str) -> str: ...

    def parse_detail(self, page_html: str) -> Detail: ...

    def parse_ref(self, ref: str) -> str | None:
        """id вакансии, если ref — ссылка на эту площадку, иначе None."""
        ...

    def unsupported(self, query: SearchQuery) -> tuple[str, ...]:
        """Поля запроса, которые площадка проигнорирует."""
        ...
