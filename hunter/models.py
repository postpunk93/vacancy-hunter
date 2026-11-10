from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

UNKNOWN_VACANCY_NAME = "(без названия)"
UNKNOWN_COMPANY = "(работодатель не указан)"


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """Один поисковый запрос из config.yaml, уже слитый с defaults."""

    name: str
    text: str
    # Роль для дайджеста и статистики. У роли два запроса — обычный и
    # текстовый про удалёнку, — и оба должны попасть в один блок.
    role: str = ""
    area: int = 1
    salary: int | None = None
    experience: tuple[str, ...] = ()
    work_format: str | None = None
    only_with_salary: bool = True
    search_period: int = 1
    max_pages: int = 1
    # Текст запроса на языке конкретной площадки: у hh свой синтаксис
    # (NAME:, NOT, AND), у остальных — обычные слова. None — на эту
    # площадку запрос не отправлять.
    text_by_source: Mapping[str, str | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Роль всегда заполнена: без неё дайджест и статистика потеряли бы
        # блок, а тесты и CLI создают запрос без неё.
        if not self.role:
            object.__setattr__(self, "role", self.name)

    def text_for(self, source: str) -> str | None:
        """Чем искать на площадке: своя формулировка из карты, иначе общий текст.
        Значение null в карте — «на эту площадку запрос не отправлять»."""
        if source in self.text_by_source:
            return self.text_by_source[source]
        return self.text


@dataclass(frozen=True, slots=True)
class Vacancy:
    """Вакансия, разобранная из выдачи. Поля — только то, что нужно дайджесту."""

    source: str
    vacancy_id: str
    name: str
    company: str
    url: str
    area: str
    # None — даты нет: у superjob её нет в карточке выдачи.
    published_at: datetime | None
    salary_from: int | None
    salary_to: int | None
    currency: str | None
    gross: bool | None
    salary_mode: str | None
    work_formats: tuple[str, ...]
    experience: str | None
    is_ad: bool

    @property
    def key(self) -> str:
        """Ключ для базы. Префикс источника обязателен: id у hh и zarplata
        принадлежат разным пространствам и вполне могут совпасть."""
        return f"{self.source}:{self.vacancy_id}"

    @property
    def has_salary(self) -> bool:
        return self.salary_from is not None or self.salary_to is not None


@dataclass(frozen=True, slots=True)
class FullText:
    """Полный текст со страницы вакансии: описание в markdown и ключевые навыки."""

    description: str
    key_skills: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Score:
    """Оценка вакансии моделью. value=None — оценить не удалось, причина в why.
    from_card — текст не скачался, оценка по одной карточке из выдачи."""

    value: int | None
    why: str
    flags: tuple[str, ...] = ()
    from_card: bool = False

    @property
    def scored(self) -> bool:
        return self.value is not None
