"""superjob.ru: и выдача, и страница вакансии отдаются состоянием `window.APP_STATE`.

У опубликованных вакансий на странице есть ещё JSON-LD JobPosting — метаданные
берём из него. У архивных страниц JobPosting нет, и деталь собирается из того же
состояния, что и карточки выдачи: разбор страницы не должен зависеть от того,
снята вакансия с сайта или нет.

Выдача — это SSR-страница с нормализованным стором (entities + responses).
Узкие запросы площадка уводит на SEO-лендинг: он тоже содержит сорок карточек,
поэтому «это выдача» проверяется по `pageInfo.pageType`, а не по числу карточек.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlencode

from ..models import UNKNOWN_COMPANY, UNKNOWN_VACANCY_NAME, SearchQuery
from ..corpus.markdown import html_to_markdown
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
_DETAIL_PAGE = "vacancy-page"

# Код опыта в справочнике superjob → код hh: остальной код ждёт hh-словарь.
_EXPERIENCE = {
    0: None,
    1: "noExperience",
    2: "between1And3",
    3: "between3And6",
    4: "moreThan6",
}

_CURRENCY = {"rub": "RUR", "usd": "USD", "eur": "EUR"}

# employmentType по schema.org → код hh, который ждёт остальной код.
_EMPLOYMENT = {
    "FULL_TIME": "FULL",
    "PART_TIME": "PART",
    "CONTRACTOR": "PROJECT",
    "TEMPORARY": "PROJECT",
    "INTERN": "PROBATION",
}

# Занятость в справочнике страницы вакансии — слаг, а JSON-LD отдаёт код hh.
_WORK_TYPE = {"full-time": "FULL", "part-time": "PART"}

_VACANCY_PATH = re.compile(r"/vakansii/(?:[^/]*-)?(\d+)\.html")
_JOB_POSTING = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
    re.DOTALL,
)


def _employment(value: object) -> str | None:
    """employmentType → код hh. Схема schema.org разрешает список, площадка им пользуется."""
    if isinstance(value, list):
        known = [_EMPLOYMENT[code] for code in value if code in _EMPLOYMENT]
        if known:
            return known[0]
        first = next((code for code in value if isinstance(code, str)), None)
        return _EMPLOYMENT.get(first, first) if first else None
    if isinstance(value, str):
        return _EMPLOYMENT.get(value, value)
    return None


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
    if work_type:
        work_type = _WORK_TYPE.get(work_type, work_type)

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


def _find_job_posting(page_html: str) -> dict | None:
    """JSON-LD JobPosting со страницы вакансии. У архивных страниц его нет."""
    for block in _JOB_POSTING.findall(page_html):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            return data
    return None


def _detail_vacancy_id(state: dict) -> str:
    page_info = state.get("pageInfo") or {}
    page_type = page_info.get("pageType")
    if page_type != _DETAIL_PAGE:
        raise ParseError(
            f"страница superjob отдала не вакансию ({page_type}) — сайт сменил формат"
        )
    vacancy_id = (page_info.get("routeParams") or {}).get("vacancyId")
    if not vacancy_id:
        raise ParseError("в APP_STATE нет routeParams.vacancyId — сайт сменил формат")
    return str(vacancy_id)


def _detail_status(state: dict, vacancy_id: str) -> str:
    """Снята с сайта или жива. Нет сведений о статусе — считаем живой."""
    info = ((state.get("entities") or {}).get("vacancyStatusesInfo") or {}).get(vacancy_id) or {}
    attributes = info.get("attributes") or {}
    if attributes.get("isArchive") or attributes.get("isClosed"):
        return "archived"
    return "active"


def _detail_entity(state: dict, vacancy_id: str) -> dict:
    link = _relation(_entity(state, "vacancy", vacancy_id), "detailInfo")
    if link is None:
        return {}
    return _entity(state, "vacancyDetailInfo", link["id"], vacancy_id)


def _detail_text(attributes: dict) -> str:
    for key in ("fullText", "fullTextPlain"):
        text = attributes.get(key)
        if text and text.strip():
            return text
    parts = [attributes.get("duties"), attributes.get("requirements")]
    return "\n\n".join(part for part in parts if part)


def _detail_skills(state: dict, vacancy_id: str) -> list[str]:
    links = (_detail_entity(state, vacancy_id).get("relationships") or {}).get(
        "professionalSkills", {}).get("data") or []
    entities = (state.get("entities") or {}).get("professionalSkill") or {}
    names = []
    for link in links:
        if not isinstance(link, dict):
            continue
        name = ((entities.get(str(link.get("id"))) or {}).get("attributes") or {}).get("name")
        if name:
            names.append(name)
    return names


def _detail_from_state(state: dict, vacancy_id: str, status: str) -> Detail:
    """Архивная страница: JobPosting нет, но состояние страницы полное."""
    card = _card(state, vacancy_id)
    attributes = _detail_entity(state, vacancy_id).get("attributes") or {}
    main = _entity(state, "vacancyMainInfo", vacancy_id, vacancy_id).get("attributes") or {}
    row = dict(card.row)
    # Даты публикации в карточке выдачи нет — берём из состояния.
    row["published_at"] = main.get("publishedAt")
    return Detail(
        source="superjob",
        external_id=str(vacancy_id),
        row=row,
        description=html_to_markdown(_detail_text(attributes)),
        key_skills=_detail_skills(state, vacancy_id),
        status=status,
        raw=attributes,
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
        try:
            return [_card(state, str(vacancy_id)) for vacancy_id in ids]
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            # Одна странная карточка не должна ронять весь проход:
            # run ловит ParseError, а не сырые ошибки разбора.
            raise ParseError(
                f"Карточка вакансии не разобралась "
                f"({exc.__class__.__name__}: {exc}) — сайт сменил формат"
            ) from exc

    def vacancy_url(self, external_id: str) -> str:
        return VACANCY_URL.format(id=external_id)

    def parse_detail(self, page_html: str) -> Detail:
        state = _state(page_html)
        vacancy_id = _detail_vacancy_id(state)
        status = _detail_status(state, vacancy_id)
        posting = _find_job_posting(page_html)
        if posting is None:
            return _detail_from_state(state, vacancy_id, status)
        url = posting.get("url") or ""
        external_id = self.parse_ref(url)
        if external_id is None:
            raise ParseError(
                "в JobPosting нет ссылки на вакансию — сайт сменил формат"
            )
        salary = posting.get("baseSalary") or {}
        value = salary.get("value") or {}
        currency = salary.get("currency")
        employment = posting.get("employmentType")
        address = ((posting.get("jobLocation") or {}).get("address") or {})
        name = posting.get("title") or UNKNOWN_VACANCY_NAME
        company = ((posting.get("hiringOrganization") or {}).get("name")
                   or UNKNOWN_COMPANY)
        row = {
            "source": "superjob",
            "external_id": external_id,
            "name": name,
            "company": company,
            # identifier в JobPosting — id компании, а не вакансии.
            "company_id": None,
            "url": url or VACANCY_URL.format(id=external_id),
            "area": address.get("addressLocality"),
            "published_at": posting.get("datePosted"),
            "salary_from": value.get("minValue"),
            "salary_to": value.get("maxValue"),
            "currency": _CURRENCY.get((currency or "").lower(), currency),
            "gross": None,
            "salary_frequency": value.get("unitText"),
            "experience": None,
            "work_formats": json.dumps([]),
            "employment_form": _employment(employment),
            "employer_trusted": None,
            "employer_category": None,
            "antifraud_power": None,
            "responses_count": None,
            "card_json": None,
        }
        return Detail(
            source="superjob",
            external_id=external_id,
            row=row,
            description=html_to_markdown(posting.get("description") or ""),
            # Ключевых навыков на странице superjob нет.
            key_skills=[],
            status=status,
            raw=posting,
        )

    def unsupported(self, query: SearchQuery) -> tuple[str, ...]:
        """Что из запроса площадка проигнорирует. Молчать об этом нельзя:
        владелец решит, что фильтр применился."""
        ignored = []
        if query.area != 113:
            ignored.append("area")
        if query.salary is not None:
            ignored.append("salary")
        if query.experience:
            ignored.append("experience")
        if query.only_with_salary:
            ignored.append("only_with_salary")
        if query.work_format not in (None, "remote"):
            ignored.append("work_format")
        return tuple(ignored)

    def parse_ref(self, ref: str) -> str | None:
        match = _VACANCY_PATH.search(ref)
        return match.group(1) if match else None
