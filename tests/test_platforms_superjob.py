from pathlib import Path

import pytest

from hunter.models import SearchQuery
from hunter.parse import ParseError
from hunter.platforms import get
from hunter.platforms.base import Captcha

FIXTURES = Path(__file__).parent / "fixtures"


def _query(**over):
    base = dict(name="author", text="редактор удаленно", search_period=7,
                work_format="remote", max_pages=2)
    base.update(over)
    return SearchQuery(**base)


def _cards(name):
    return get("superjob").parse_cards((FIXTURES / name).read_text(encoding="utf-8"))


def test_search_url_uses_keywords_period_and_remote_tag():
    url = get("superjob").search_url(_query(), page=0)

    assert url.startswith("https://russia.superjob.ru/vacancy/search/?")
    assert "period=7" in url
    assert "workFormatTag%5B0%5D=81" in url
    assert "page=" not in url, "page=0 — параметра нет вовсе"
    assert "page=2" in get("superjob").search_url(_query(), page=1)


def test_remote_tag_is_absent_without_remote():
    url = get("superjob").search_url(_query(work_format=None), page=0)
    assert "workFormatTag" not in url


def test_period_is_capped_to_week():
    assert get("superjob").search_period(30) == 7
    assert get("superjob").search_period(3) == 3
    assert get("superjob").search_period(1) == 1


def test_vacancy_url_is_short_form_that_redirects_to_canonical():
    assert get("superjob").vacancy_url("52295791") == (
        "https://russia.superjob.ru/vakansii/x-52295791.html"
    )


def test_cards_carry_salary_and_company():
    cards = _cards("superjob_search_sales.html")

    assert len(cards) >= 40
    paid = next(card for card in cards if card.salary_from)
    assert paid.source == "superjob" and paid.external_id.isdigit()
    assert paid.company and paid.url.endswith(f"x-{paid.external_id}.html")
    assert paid.salary_mode == "MONTH"
    assert paid.row["external_id"] == paid.external_id
    assert paid.published_at is None, "даты публикации в карточке superjob нет"


def test_cards_without_salary_are_payment_agreement():
    cards = _cards("superjob_search_editor.html")

    assert all(card.salary_from is None and card.salary_to is None for card in cards[:5])


def test_card_fields_come_from_entities():
    card = _cards("superjob_search_sales.html")[0]

    assert card.name
    assert card.company
    assert card.experience in (None, "noExperience", "between1And3", "between3And6", "moreThan6")
    assert card.row["url"] == card.url
    assert isinstance(card.row["work_formats"], str)


def test_landing_page_is_a_loud_parse_error():
    with pytest.raises(ParseError, match="лендинг"):
        _cards("superjob_landing.html")


def test_page_without_state_is_captcha():
    with pytest.raises(Captcha):
        get("superjob").parse_cards("<html><body>captcha</body></html>")


def test_parse_ref_understands_superjob_links():
    platform = get("superjob")

    assert platform.parse_ref("https://russia.superjob.ru/vakansii/redaktor-52295791.html") == "52295791"
    assert platform.parse_ref("https://russia.superjob.ru/vakansii/x-52295791.html") == "52295791"
    assert platform.parse_ref("https://hh.ru/vacancy/1") is None


def test_platform_is_registered_and_ref_forms_work():
    from hunter.platforms import format_ref, parse_ref

    assert get("superjob").name == "superjob"
    assert parse_ref("superjob:52295791") == ("superjob", "52295791")
    assert parse_ref("https://russia.superjob.ru/vakansii/x-52295791.html") == (
        "superjob", "52295791")
    assert format_ref("superjob", "52295791") == "superjob:52295791"
