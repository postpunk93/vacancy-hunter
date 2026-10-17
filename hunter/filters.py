from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime

from .models import UNKNOWN_COMPANY, UNKNOWN_VACANCY_NAME, Score, Vacancy
from .text import plural

Group = tuple[str, list[Vacancy]]


def drop_ads(vacancies: list[Vacancy]) -> list[Vacancy]:
    """Рекламные врезки помечены свежей датой публикации, но могут быть
    созданы годы назад — в дайджесте им не место."""
    return [v for v in vacancies if not v.is_ad]


def drop_without_salary(vacancies: list[Vacancy]) -> list[Vacancy]:
    """Страховка: основной отсев делает сам hh по only_with_salary."""
    return [v for v in vacancies if v.has_salary]


def drop_published_before(vacancies: list[Vacancy], cutoff: datetime) -> list[Vacancy]:
    """Нужен, когда период запроса не совпадает с периодом выдачи hh:
    просили 2 дня — hh отдал 3, лишний день отсекаем здесь."""
    return [v for v in vacancies if v.published_at >= cutoff]


def sort_by_freshness(vacancies: list[Vacancy]) -> list[Vacancy]:
    return sorted(vacancies, key=lambda v: v.published_at, reverse=True)


def _merge_areas(areas: list[str]) -> str:
    """Список городов вакансии-клона для отображения: без повторов и
    пустых значений; при 4+ городах — первые три и "и ещё N"."""
    unique = list(dict.fromkeys(a for a in areas if a))
    if len(unique) <= 1:
        return unique[0] if unique else ""
    if len(unique) <= 3:
        return ", ".join(unique)
    head = ", ".join(unique[:3])
    return f"{head} и ещё {plural(len(unique) - 3, 'город', 'города', 'городов')}"


def dedupe(groups: list[Group]) -> tuple[list[Group], list[Vacancy]]:
    """Схлопывает дубли в два прохода. Первый — по `vacancy.key` (буквально
    одна и та же карточка, попавшая в выдачу нескольких запросов): остаётся
    в первой группе, где встретилась, считая от начала списка queries
    в конфиге. Второй — по `(company, name)`: одна и та же вакансия,
    размещённая отдельными карточками в разных городах (частый случай для
    удалённой работы) или на разных источниках; представитель — первый
    встреченный, его `area` заменяется на объединённый список городов всех
    дублей. Второй элемент результата — плоский список поглощённых вакансий:
    вызывающий обязан пометить их виденными сам, иначе они всплывут по
    отдельности в следующем проходе."""
    seen_ids: set[str] = set()
    seen_content: set[tuple[str, str]] = set()
    kept: list[list[Vacancy]] = [[] for _ in groups]
    areas: dict[tuple[str, str], list[str]] = {}
    absorbed: list[Vacancy] = []

    for group_index, (_, vacancies) in enumerate(groups):
        for vacancy in vacancies:
            if vacancy.key in seen_ids:
                continue
            seen_ids.add(vacancy.key)

            has_placeholder_identity = (
                vacancy.company == UNKNOWN_COMPANY or vacancy.name == UNKNOWN_VACANCY_NAME
            )
            if has_placeholder_identity:
                kept[group_index].append(vacancy)
                continue

            content_key = (vacancy.company, vacancy.name)
            if content_key in seen_content:
                areas[content_key].append(vacancy.area)
                absorbed.append(vacancy)
                continue

            seen_content.add(content_key)
            areas[content_key] = [vacancy.area]
            kept[group_index].append(vacancy)

    result: list[Group] = []
    for group_index, (name, _) in enumerate(groups):
        merged = []
        for vacancy in kept[group_index]:
            city_areas = areas.get((vacancy.company, vacancy.name))
            if city_areas and len(city_areas) > 1:
                vacancy = replace(vacancy, area=_merge_areas(city_areas))
            merged.append(vacancy)
        result.append((name, merged))

    return result, absorbed


def sort_by_score(vacancies: list[Vacancy], scores: Mapping[str, Score]) -> list[Vacancy]:
    """Лучшие сверху. Без оценки — в конце, в прежнем порядке."""

    def rank(vacancy: Vacancy) -> tuple[bool, int]:
        score = scores.get(vacancy.key)
        if score is None or not score.scored:
            return (True, 0)
        return (False, -score.value)

    return sorted(vacancies, key=rank)
