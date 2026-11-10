"""hh.ru и zarplata.ru: один движок, разные домены.

zarplata — отдельная площадка, но разбор у неё тот же: имена параметров выдачи
совпадают, страница вакансии живёт на hh, id вакансий общие (кросспостинг).
Поэтому класс параметризован сайтом, а не скопирован дважды.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from urllib.parse import urlencode, urlparse

from ..models import UNKNOWN_COMPANY, UNKNOWN_VACANCY_NAME, SearchQuery
from ..parse import ParseError, extract_state
from ..corpus.markdown import html_to_markdown
from .base import Captcha, Card, Detail, UnknownPlatform

# Оба сайта работают на одном движке: имена параметров совпадают,
# различаются только домен. Путь выдачи у обоих /search/vacancy: на
# /vacancies у zarplata лежит не поиск, а страница с одной-двумя карточками.
SITES: dict[str, tuple[str, str]] = {
    "hh": ("https://hh.ru", "/search/vacancy"),
    "zarplata": ("https://www.zarplata.ru", "/search/vacancy"),
}

# Периоды, которые понимает выдача. Любое другое значение hh молча
# игнорирует и отдаёт вакансии за всё время.
HH_PERIODS = (1, 3, 7, 30)

# Один адрес для вакансий hh и zarplata: у zarplata те же id (кросспостинг hh).
VACANCY_URL = "https://hh.ru/vacancy/{id}"

_STATE_MARKER = 'id="HH-Lux-InitialState"'
_ANTIFRAUD = "HH_ANTIFRAUD_SUSPICIOUS_VACANCY"
_VACANCY_PATH = re.compile(r"^/vacancy/(\d+)/?$")
# Домены движка hh: ссылка с любого из них ведёт на ту же вакансию.
_HOSTS = ("hh.ru", "zarplata.ru")

# Слова, по которым страница без состояния опознаётся как капча, а не редизайн.
_CAPTCHA_MARKERS = ("captcha", "капча", "робот", "подтвердите", "access denied", "blocked")


def hh_period(days: int) -> int:
    """Ближайший период выдачи не короче запрошенного. Лишние дни
    отсекаются после разбора — см. run_pass."""
    return next((p for p in HH_PERIODS if p >= days), HH_PERIODS[-1])


def build_url(site: str, query: SearchQuery, page: int = 0) -> str:
    try:
        base_url, search_path = SITES[site]
    except KeyError as exc:
        raise UnknownPlatform(
            f"Неизвестный источник: {site}. Доступны: {', '.join(SITES)}"
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

    return f"{base_url}{search_path}?{urlencode(params)}"


def _page_state(page_html: str) -> dict:
    # Нет маркера вовсе — нам подсунули другую страницу. Маркер есть, но JSON
    # не разобрался — это уже смена формата, ParseError из extract_state.
    # Без маркера различаем блокировку и редизайн: от этого зависит, повторять
    # запрос (капча — нет) или чинить разбор (редизайн).
    if _STATE_MARKER not in page_html:
        lowered = page_html.casefold()
        if any(marker in lowered for marker in _CAPTCHA_MARKERS):
            raise Captcha("в ответе нет HH-Lux-InitialState — похоже на капчу")
        raise ParseError(
            "В странице нет HH-Lux-InitialState — вёрстка изменилась "
            "или пришла заглушка вместо выдачи"
        )
    return extract_state(page_html)


def search_cards(page_html: str) -> list[dict]:
    result = _page_state(page_html).get("vacancySearchResult")
    if not isinstance(result, dict) or not isinstance(result.get("vacancies"), list):
        raise ParseError("В состоянии страницы нет vacancySearchResult.vacancies")
    return result["vacancies"]


def _flag(value) -> int | None:
    return None if value is None else int(bool(value))


def _identity(raw: dict, company: dict, site: str) -> dict:
    vacancy_id = int(raw["vacancyId"])
    return {
        "source": site,
        "external_id": str(vacancy_id),
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


def card_row(raw: dict, site: str = "hh") -> dict:
    """Карточка из выдачи → колонки vacancies, кроме отметок времени
    и полей страницы вакансии."""
    company = raw.get("company") or {}
    return {
        **_identity(raw, company, site),
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


def _published_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value)


def _card(raw: dict, site: str) -> Card:
    row = card_row(raw, site)
    work_formats = tuple(
        element
        for block in (raw.get("workFormats") or [])
        for element in (block.get("workFormatsElement") or [])
    )
    return Card(
        source=site,
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
        # mode — период оплаты для дайджеста (MONTH/HOUR/SERVICE),
        # frequency — как часто платят (MONTHLY, PER_PROJECT).
        salary_mode=(raw.get("compensation") or {}).get("mode"),
        work_formats=work_formats,
        experience=row["experience"],
        is_ad=bool(raw.get("@isAdv")),
        row=row,
    )


def parse_detail(page_html: str, site: str = "hh") -> Detail:
    try:
        raw = (_page_state(page_html).get("vacancyView") or {}).get("vacancyFull", {})["vacancy"]
    except (KeyError, TypeError) as exc:
        raise ParseError(
            "В состоянии страницы нет vacancyView.vacancyFull.vacancy — "
            "сайт сменил формат"
        ) from exc
    vacancy_id = int(raw["vacancyId"])
    # На странице вакансии у company нет префикса @, а workFormats — плоский список.
    company = raw.get("company") or {}
    row = {
        **_identity(raw, company, site),
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
        source=site,
        external_id=str(vacancy_id),
        row=row,
        description=html_to_markdown(raw.get("description") or ""),
        key_skills=[str(skill) for skill in raw.get("keySkills") or []],
        status="archived" if (raw.get("status") or {}).get("archived") else "active",
        raw=raw,
    )


class Hh:
    """Площадка на движке hh: `Hh("hh")` и `Hh("zarplata")`."""

    page_size = 50
    max_pages = 40

    def __init__(self, site: str) -> None:
        if site not in SITES:
            raise UnknownPlatform(
                f"Неизвестный источник: {site}. Доступны: {', '.join(SITES)}"
            )
        self._site = site

    @property
    def name(self) -> str:
        return self._site

    def search_url(self, query: SearchQuery, page: int = 0) -> str:
        return build_url(self._site, query, page)

    def search_period(self, days: int) -> int:
        return hh_period(days)

    def parse_cards(self, page_html: str) -> list[Card]:
        raw_cards = search_cards(page_html)
        try:
            return [_card(raw, self._site) for raw in raw_cards]
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ParseError(
                f"Карточка вакансии не разобралась "
                f"({exc.__class__.__name__}: {exc}) — сайт сменил формат"
            ) from exc

    def vacancy_url(self, external_id: str) -> str:
        # id у hh и zarplata общие, страница вакансии всегда на hh.
        return VACANCY_URL.format(id=external_id)

    def parse_detail(self, page_html: str) -> Detail:
        return parse_detail(page_html, self._site)

    def parse_ref(self, ref: str) -> str | None:
        ref = ref.strip()
        if ref.isdigit():
            return ref
        parsed = urlparse(ref)
        host = (parsed.hostname or "").lower()
        # zarplata — тот же пул id, ссылка на неё ведёт на ту же вакансию hh.
        if parsed.scheme not in ("http", "https"):
            return None
        if not any(host == site or host.endswith("." + site) for site in _HOSTS):
            return None
        match = _VACANCY_PATH.match(parsed.path)
        return match.group(1) if match else None
