"""rabota.ru: выдача и страница вакансии — состояние Nuxt в `window.__NUXT__`.

Выдача живёт в `state.rabota.search`, вакансия — в `state.rabota.vacancy`.
Формат работы в карточке не отдаётся вовсе: он виден только на странице
вакансии (`operating_schedule.id == 6` — удалённая работа). Зарплата приходит
подписью (`руб./мес.`), а не кодом, поэтому валюта и период разбираются по
частям, а незнакомая часть остаётся пустой.
"""

from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import urlencode, urlparse

from ..corpus.markdown import html_to_markdown
from ..models import UNKNOWN_COMPANY, UNKNOWN_VACANCY_NAME, SearchQuery
from ..parse import ParseError
from .base import Captcha, Card, Detail
from .nuxt import parse_nuxt

BASE_URL = "https://www.rabota.ru"
SEARCH_PATH = "/vacancy/"
VACANCY_URL = BASE_URL + "/vacancy/{id}/"

# Периоды выдачи: дни, которые понимает площадка, и её строка периода.
PERIODS = ((1, "day"), (3, "three-days"), (7, "week"), (30, "month"))

# operating_schedule.id удалённой работы — в карточке его нет, он только на странице.
REMOTE_SCHEDULE_ID = 6

_MARKER = "window.__NUXT__"
# Слова, по которым страница без состояния опознаётся как капча, а не редизайн.
_CAPTCHA_MARKERS = ("captcha", "капча", "робот", "подтвердите")

# Подпись валюты до слеша → код, который ждёт остальной код.
_CURRENCY = {"руб.": "RUR", "руб": "RUR", "rur": "RUR", "rub": "RUR",
             "$": "USD", "usd": "USD", "€": "EUR", "eur": "EUR"}
# Подпись периода после слеша → период оплаты для дайджеста.
_MODE = {"мес.": "MONTH", "мес": "MONTH", "month": "MONTH",
         "час": "HOUR", "час.": "HOUR", "hour": "HOUR",
         "день": "DAY", "дн.": "DAY", "дн": "DAY", "day": "DAY",
         "нед.": "WEEK", "нед": "WEEK", "неделя": "WEEK", "week": "WEEK"}

# payslip-подпись пустая либо из одной части — тогда второй половины нет.
_EMPTY_SALARY = {
    "salary_from": None,
    "salary_to": None,
    "currency": None,
    "gross": None,
    "salary_frequency": None,
    "salary_mode": None,
}


def _state(page_html: str) -> dict:
    """Состояние страницы. Нет состояния — капча или редизайн."""
    if _MARKER not in page_html:
        lowered = page_html.casefold()
        if any(marker in lowered for marker in _CAPTCHA_MARKERS):
            raise Captcha("в ответе rabota нет window.__NUXT__ — похоже на капчу")
        raise ParseError(
            "В странице rabota нет window.__NUXT__ — сайт сменил формат"
        )
    state = parse_nuxt(page_html).get("state")
    if not isinstance(state, dict):
        raise ParseError("window.__NUXT__ разобрался без state — сайт сменил формат")
    return state


def _label(value: object) -> tuple[str | None, str | None]:
    """Подпись вида `руб./мес.` → (валюта, период). Незнакомая часть — None."""
    if not isinstance(value, str):
        return (None, None)
    currency, _, period = value.partition("/")
    currency = currency.strip().casefold()
    period = period.strip().casefold()
    return (_CURRENCY.get(currency), _MODE.get(period))


def _salary(salary: object) -> dict:
    """Вилка из `salary`. Пустая граница — это 0, а не ноль денег."""
    if not isinstance(salary, dict):
        return dict(_EMPTY_SALARY)
    currency, period = _label(salary.get("currency"))
    pay_type = salary.get("pay_type")
    return {
        "salary_from": salary.get("from") or None,
        "salary_to": salary.get("to") or None,
        "currency": currency,
        "gross": None if pay_type not in ("gross", "net") else int(pay_type == "gross"),
        # Частоту выплат rabota не различает: период оплаты и есть подпись.
        "salary_frequency": None,
        "salary_mode": period,
    }


def _area(vacancy: dict) -> str | None:
    places = vacancy.get("places") or []
    if not places or not isinstance(places[0], dict):
        return None
    place = places[0]
    return (place.get("region") or {}).get("name") or place.get("name")


def card_row(vacancy: dict) -> dict:
    """Карточка выдачи → колонки таблицы vacancies."""
    company = vacancy.get("company") or {}
    external_id = str(vacancy["id"])
    return {
        "source": "rabota",
        "external_id": external_id,
        "name": vacancy.get("title") or UNKNOWN_VACANCY_NAME,
        "company": company.get("name") or UNKNOWN_COMPANY,
        "company_id": company.get("id"),
        "url": VACANCY_URL.format(id=external_id),
        "area": _area(vacancy),
        "published_at": vacancy.get("publish_start_at"),
        **_salary(vacancy.get("salary")),
        "experience": None,
        # Формата работы в карточке нет — он приходит со страницы вакансии.
        "work_formats": json.dumps([]),
        "employment_form": None,
        "employer_trusted": None,
        "employer_category": None,
        "antifraud_power": None,
        "responses_count": None,
        "card_json": json.dumps(vacancy, ensure_ascii=False),
    }


def _published_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value)


def _card(vacancy: dict) -> Card:
    row = card_row(vacancy)
    return Card(
        source="rabota",
        external_id=row["external_id"],
        name=row["name"],
        company=row["company"],
        url=row["url"],
        area=row["area"] or "",
        published_at=_published_at(row["published_at"]),
        salary_from=row["salary_from"],
        salary_to=row["salary_to"],
        currency=row["currency"],
        gross=None if row["gross"] is None else bool(row["gross"]),
        salary_mode=row["salary_mode"],
        work_formats=(),
        experience=None,
        # is_promoted — платное продвижение вакансии, а не реклама.
        is_ad=False,
        row=row,
    )


class Rabota:
    """Площадка rabota.ru."""

    page_size = 30
    max_pages = 20

    name = "rabota"

    def search_url(self, query: SearchQuery, page: int = 0) -> str:
        params: list[tuple[str, object]] = [
            ("query", query.text),
            # Регионы у площадки свои; «вся Россия» — единственный общий фильтр.
            ("all_regions", 1),
            ("period", self._period_label(query.search_period)),
        ]
        if query.work_format == "remote":
            params.append(("schedule_ids", REMOTE_SCHEDULE_ID))
        if query.salary is not None:
            params.append(("min_salary", query.salary))
        if page:
            # Пагинация 1-based: page=1 — это первая страница.
            params.append(("page", page + 1))
        return f"{BASE_URL}{SEARCH_PATH}?{urlencode(params)}"

    def search_period(self, days: int) -> int:
        return next((period for period, _ in PERIODS if period >= days), PERIODS[-1][0])

    @staticmethod
    def _period_label(days: int) -> str:
        return next((label for period, label in PERIODS if period >= days),
                    PERIODS[-1][1])

    def parse_cards(self, page_html: str) -> list[Card]:
        state = _state(page_html)
        search = (state.get("rabota") or {}).get("search")
        if not isinstance(search, dict):
            raise ParseError("в состоянии rabota нет state.rabota.search")
        if not search.get("totalVacancies"):
            # Пустая выдача — законный ответ: ld+json у неё тоже нет.
            return []
        vacancies = search.get("vacancies")
        if not isinstance(vacancies, list):
            raise ParseError("в состоянии rabota нет state.rabota.search.vacancies")
        try:
            return [_card(vacancy) for vacancy in vacancies]
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            # Одна странная карточка не должна ронять весь проход:
            # run ловит ParseError, а не сырые ошибки разбора.
            raise ParseError(
                f"Карточка вакансии не разобралась "
                f"({exc.__class__.__name__}: {exc}) — сайт сменил формат"
            ) from exc

    def vacancy_url(self, external_id: str) -> str:
        return VACANCY_URL.format(id=external_id)
