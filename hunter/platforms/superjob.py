"""superjob.ru: выдача отдаётся состоянием `window.APP_STATE`, страница вакансии — JSON-LD.

Выдача — это SSR-страница с нормализованным стором (entities + responses).
Узкие запросы площадка уводит на SEO-лендинг: он тоже содержит сорок карточек,
поэтому «это выдача» проверяется по `pageInfo.pageType`, а не по числу карточек.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlencode

from ..models import UNKNOWN_COMPANY, UNKNOWN_VACANCY_NAME, SearchQuery
from ..parse import ParseError
from .base import Captcha, Card, Detail

BASE_URL = "https://russia.superjob.ru"
SEARCH_PATH = "/vacancy/search/"
VACANCY_URL = BASE_URL + "/vakansii/x-{id}.html"

# Периоды, которые площадка умеет отдавать. Больше недели она не смотрит.
PERIODS = (1, 3, 7)

# workFormatTag[0]=81 — «Удалённая работа». Параметр remoteWork=1 выдача
# игнорирует: это фасет, а не фильтр.
REMOTE_TAG = "81"

_STATE_MARKER = "window.APP_STATE="
_CARD_MARKER = "f-test-vacancy-item"
_CARD_PAGE = "search-vacancy"

# Код опыта в справочнике superjob → код hh: остальной код ждёт hh-словарь.
_EXPERIENCE = {
    0: None,
    1: "noExperience",
    2: "between1And3",
    3: "between3And6",
    4: "moreThan6",
}

_CURRENCY = {"rub": "RUR", "usd": "USD", "eur": "EUR"}

_VACANCY_PATH = re.compile(r"/vakansii/(?:[^/]*-)?(\d+)\.html")
_JOB_POSTING = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
    re.DOTALL,
)


def _state(page_html: str) -> dict:
    """Состояние страницы. Нет ни состояния, ни контейнера карточек — заглушка."""
    index = page_html.find(_STATE_MARKER)
    if index < 0:
        if _CARD_MARKER in page_html:
            raise ParseError("в выдаче superjob есть карточки, но нет APP_STATE — сайт сменил формат")
        raise Captcha("в ответе superjob нет APP_STATE — похоже на капчу")
    try:
        state, _ = json.JSONDecoder().raw_decode(page_html[index + len(_STATE_MARKER):])
    except json.JSONDecodeError as exc:
        raise ParseError(f"APP_STATE не разбирается как JSON: {exc}") from exc
    if not isinstance(state, dict):
        raise ParseError("APP_STATE разобрался не в словарь — сайт сменил формат")
    return state


def _entity(state: dict, kind: str, entity_id: str, owner: str = "") -> dict:
    try:
        return state["entities"][kind][str(entity_id)]
    except (KeyError, TypeError) as exc:
        raise ParseError(
            f"в APP_STATE нет сущности {kind}[{entity_id}]"
            + (f" у вакансии {owner}" if owner else "")
        ) from exc


def _relation(entity: dict, name: str) -> dict | None:
    data = (entity.get("relationships") or {}).get(name, {}).get("data")
    return data if isinstance(data, dict) else None


def _dictionary_key(state: dict, kind: str, entity_id: str) -> str | None:
    if entity_id is None:
        return None
    try:
        return state["entities"][kind][str(entity_id)]["attributes"]["key"]
    except (KeyError, TypeError) as exc:
        raise ParseError(f"в APP_STATE нет справочника {kind}[{entity_id}]") from exc


def _salary(state: dict, vacancy: dict) -> dict:
    """Вилка из сущности vacancySalary. Плата по договорённости — пустые границы."""
    main = _entity(state, "vacancyMainInfo", vacancy["id"])
    link = _relation(main, "salary")
    if link is None:
        return {"salary_from": None, "salary_to": None, "currency": None,
                "gross": None, "salary_frequency": None, "salary_mode": None}
    salary = _entity(state, "vacancySalary", link["id"])
    attributes = salary.get("attributes") or {}
    if attributes.get("paymentAgreement"):
        low = high = None
    else:
        # 0 в minSalary/maxSalary значит «границы нет».
        low = attributes.get("minSalary") or None
        high = attributes.get("maxSalary") or None
    currency = _dictionary_key(state, "currencyDictionary", (_relation(salary, "currency") or {}).get("id"))
    period = _dictionary_key(state, "vacancyPaymentPeriodDictionary", (_relation(salary, "salaryPeriod") or {}).get("id"))
    salary_type = _dictionary_key(state, "vacancySalaryTypeDictionary", (_relation(salary, "salaryType") or {}).get("id"))
    return {
        "salary_from": low,
        "salary_to": high,
        "currency": _CURRENCY.get(currency or "", currency.upper() if currency else None),
        "gross": None if salary_type is None else salary_type == "gross",
        "salary_frequency": period.upper() if period else None,
        # Период оплаты для дайджеста: у superjob он же и есть (месяц, час…).
        "salary_mode": period.upper() if period else None,
    }


def _card(state: dict, vacancy_id: str) -> Card:
    vacancy = _entity(state, "vacancy", vacancy_id)
    main = _entity(state, "vacancyMainInfo", vacancy_id, vacancy_id)

    company_link = _relation(vacancy, "companyInfo")
    company = (_entity(state, "vacancyCompanyInfo", company_link["id"], vacancy_id)
               .get("attributes") or {}) if company_link else {}

    town_link = _relation(vacancy, "town")
    town = (_entity(state, "town", town_link["id"], vacancy_id).get("attributes") or {}
            if town_link else {})

    detail_link = _relation(vacancy, "detailInfo")
    detail = (_entity(state, "vacancyDetailInfo", detail_link["id"], vacancy_id)
              .get("attributes") or {}) if detail_link else {}

    experience_link = _relation(vacancy, "requiredExperience")
    experience = None
    if experience_link:
        try:
            code = _entity(state, "vacancyExperienceDictionary", experience_link["id"])["attributes"]["value"]
        except (KeyError, TypeError) as exc:
            raise ParseError(
                f"в APP_STATE нет опыта у вакансии {vacancy_id}"
            ) from exc
        experience = _EXPERIENCE.get(code)

    work_type = None
    if detail_link:
        work_type = _dictionary_key(
            state, "vacancyWorkTypeDictionary",
            (_relation(_entity(state, "vacancyDetailInfo", detail_link["id"]), "workType") or {}).get("id"),
        )

    salary = _salary(state, vacancy)
    name = (main.get("attributes") or {}).get("profession") or UNKNOWN_VACANCY_NAME
    formats = ("REMOTE",) if detail.get("isRemoteWork") else ()
    url = VACANCY_URL.format(id=vacancy_id)

    row = {
        "source": "superjob",
        "external_id": str(vacancy_id),
        "name": name,
        "company": company.get("name") or UNKNOWN_COMPANY,
        "company_id": None,
        "url": url,
        "area": town.get("name"),
        # Даты публикации в выдаче нет: она приходит со страницы вакансии.
        "published_at": None,
        "salary_from": salary["salary_from"],
        "salary_to": salary["salary_to"],
        "currency": salary["currency"],
        "gross": None if salary["gross"] is None else int(salary["gross"]),
        "salary_frequency": salary["salary_frequency"],
        "experience": experience,
        "work_formats": json.dumps(list(formats)),
        "employment_form": work_type,
        "employer_trusted": None,
        "employer_category": None,
        "antifraud_power": None,
        "responses_count": None,
        "card_json": json.dumps(
            {"mainInfo": main, "salary": salary, "company": company,
             "town": town, "detail": detail},
            ensure_ascii=False,
        ),
    }
    return Card(
        source="superjob",
        external_id=str(vacancy_id),
        name=name,
        company=row["company"],
        url=url,
        area=row["area"] or "",
        published_at=None,
        salary_from=row["salary_from"],
        salary_to=row["salary_to"],
        currency=row["currency"],
        gross=salary["gross"],
        salary_mode=salary["salary_mode"],
        work_formats=formats,
        experience=experience,
        is_ad=False,
        row=row,
    )


class Superjob:
    """Площадка superjob.ru."""

    page_size = 40
    max_pages = 14

    name = "superjob"

    def search_url(self, query: SearchQuery, page: int = 0) -> str:
        params: list[tuple[str, object]] = [
            ("keywords", query.text),
            ("period", self.search_period(query.search_period)),
        ]
        if query.work_format == "remote":
            params.append(("workFormatTag[0]", REMOTE_TAG))
        if page:
            # Пагинация 1-based: page=1 — это первая страница.
            params.append(("page", page + 1))
        return f"{BASE_URL}{SEARCH_PATH}?{urlencode(params)}"

    def search_period(self, days: int) -> int:
        return next((period for period in PERIODS if period >= days), PERIODS[-1])

    def parse_cards(self, page_html: str) -> list[Card]:
        state = _state(page_html)
        page_type = (state.get("pageInfo") or {}).get("pageType")
        if page_type != _CARD_PAGE:
            raise ParseError(
                f"выдача superjob ушла на SEO-лендинг ({page_type}) — уточните запрос"
            )
        try:
            lists = state["responses"]["lists"]["vacancy"]
            block = next(iter(lists.values()))
            ids = block["result"]
        except (KeyError, TypeError, StopIteration) as exc:
            raise ParseError("в APP_STATE нет responses.lists.vacancy.result") from exc
        return [_card(state, str(vacancy_id)) for vacancy_id in ids]

    def vacancy_url(self, external_id: str) -> str:
        return VACANCY_URL.format(id=external_id)

    def parse_detail(self, page_html: str) -> Detail:
        raise NotImplementedError("страница вакансии superjob — в Task 6")

    def parse_ref(self, ref: str) -> str | None:
        match = _VACANCY_PATH.search(ref)
        return match.group(1) if match else None
