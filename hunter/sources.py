from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from .models import SearchQuery


@dataclass(frozen=True, slots=True)
class Source:
    name: str
    base_url: str
    search_path: str


# Оба сайта работают на одном движке: имена параметров совпадают,
# различаются только домен. Путь выдачи у обоих /search/vacancy: на
# /vacancies у zarplata лежит не поиск, а страница с одной-двумя карточками.
SOURCES: dict[str, Source] = {
    "hh": Source("hh", "https://hh.ru", "/search/vacancy"),
    "zarplata": Source("zarplata", "https://www.zarplata.ru", "/search/vacancy"),
}


# Периоды, которые понимает выдача. Любое другое значение hh молча
# игнорирует и отдаёт вакансии за всё время.
HH_PERIODS = (1, 3, 7, 30)


class UnknownSource(Exception):
    pass


def hh_period(days: int) -> int:
    """Ближайший период выдачи не короче запрошенного. Лишние дни
    отсекаются после разбора — см. run_pass."""
    return next((p for p in HH_PERIODS if p >= days), HH_PERIODS[-1])


def build_url(source: str, query: SearchQuery, page: int = 0) -> str:
    try:
        src = SOURCES[source]
    except KeyError as exc:
        raise UnknownSource(
            f"Неизвестный источник: {source}. Доступны: {', '.join(SOURCES)}"
        ) from exc

    params: list[tuple[str, object]] = [
        ("text", query.text),
        ("area", query.area),
        ("order_by", "publication_time"),
        ("search_period", hh_period(query.search_period)),
        ("items_on_page", 50),
    ]
    if query.only_with_salary:
        params.append(("only_with_salary", "true"))
    if query.salary is not None:
        params.append(("salary", query.salary))
    if query.experience:
        for value in query.experience:
            params.append(("experience", value))
    if query.work_format:
        params.append(("work_format", query.work_format.upper()))
    if page:
        params.append(("page", page))

    return f"{src.base_url}{src.search_path}?{urlencode(params)}"
