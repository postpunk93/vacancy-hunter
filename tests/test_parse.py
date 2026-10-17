import json
from datetime import timezone
from pathlib import Path

import pytest

from hunter.parse import ParseError, extract_state, parse_search_page

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parses_every_card_from_the_real_page():
    vacancies = parse_search_page(fixture("hh_search_python_msk.html"), "hh")

    assert len(vacancies) == 51
    assert sum(v.is_ad for v in vacancies) == 1
    assert sum(not v.has_salary for v in vacancies) == 38


def test_advertising_card_is_flagged_not_dropped():
    vacancies = parse_search_page(fixture("hh_search_python_msk.html"), "hh")

    first = vacancies[0]
    assert first.vacancy_id == "114811554"
    assert first.is_ad is True, "отсевом рекламы занимается filters, не parse"


def test_fields_are_filled_from_the_page():
    vacancies = parse_search_page(fixture("hh_search_sorted_with_salary.html"), "hh")
    v = vacancies[0]

    assert v.source == "hh"
    assert v.vacancy_id.isdigit()
    assert v.name
    assert v.company
    assert v.url.startswith("https://")
    assert "/vacancy/" in v.url
    assert v.published_at.tzinfo is not None
    assert v.published_at.year == 2026


def test_salary_bounds_currency_and_mode_are_captured():
    vacancies = parse_search_page(fixture("hh_search_sorted_with_salary.html"), "hh")

    assert all(v.has_salary for v in vacancies), "страница снята с only_with_salary"
    assert {v.currency for v in vacancies} == {"RUR", "USD"}
    assert {v.salary_mode for v in vacancies} == {"MONTH", "HOUR", "SERVICE"}
    assert any(v.gross is True for v in vacancies)
    assert any(v.gross is False for v in vacancies)


def test_missing_compensation_becomes_empty_bounds():
    vacancies = parse_search_page(fixture("zarplata_search_python.html"), "zarplata")

    assert len(vacancies) == 1
    v = vacancies[0]
    assert v.source == "zarplata"
    assert v.salary_from is None
    assert v.salary_to is None
    assert v.has_salary is False


def test_work_formats_are_flattened_into_a_tuple():
    vacancies = parse_search_page(fixture("hh_search_sorted_with_salary.html"), "hh")
    formats = {f for v in vacancies for f in v.work_formats}

    assert "REMOTE" in formats
    assert "ON_SITE" in formats
    assert all(isinstance(v.work_formats, tuple) for v in vacancies)


def test_page_without_the_state_template_raises_a_loud_error():
    with pytest.raises(ParseError, match="HH-Lux-InitialState"):
        parse_search_page("<html><body>Ничего интересного</body></html>", "hh")


def test_broken_json_in_the_state_raises():
    with pytest.raises(ParseError, match="JSON"):
        extract_state('<template style="display:none" id="HH-Lux-InitialState">{oops</template>')


def test_state_without_search_result_raises():
    with pytest.raises(ParseError, match="vacancySearchResult"):
        parse_search_page(
            '<template style="display:none" id="HH-Lux-InitialState">{"other": 1}</template>',
            "hh",
        )


def _state_with(vacancy: dict) -> str:
    payload = json.dumps({"vacancySearchResult": {"vacancies": [vacancy]}})
    return f'<template style="display:none" id="HH-Lux-InitialState">{payload}</template>'


_GOOD_CARD = {
    "vacancyId": 1,
    "name": "Python Developer",
    "company": {"visibleName": "Тест"},
    "area": {"name": "Москва"},
    "links": {"desktop": "https://hh.ru/vacancy/1"},
    "publicationTime": {"@timestamp": 1700000000},
    "compensation": {"from": 100000},
}


def test_renamed_vacancy_id_field_raises_parse_error():
    card = dict(_GOOD_CARD)
    del card["vacancyId"]
    card["vacancyIdV2"] = 1

    with pytest.raises(ParseError, match="KeyError"):
        parse_search_page(_state_with(card), "hh")


def test_timestamp_as_a_string_raises_parse_error():
    card = dict(_GOOD_CARD)
    card["publicationTime"] = {"@timestamp": "2026-09-01T00:00:00"}

    with pytest.raises(ParseError):
        parse_search_page(_state_with(card), "hh")


def test_company_as_a_plain_string_raises_parse_error():
    card = dict(_GOOD_CARD)
    card["company"] = "Тест"

    with pytest.raises(ParseError, match="AttributeError"):
        parse_search_page(_state_with(card), "hh")


def test_experience_code_is_captured():
    vacancies = parse_search_page(fixture("hh_search_sorted_with_salary.html"), "hh")

    assert {v.experience for v in vacancies} == {
        "noExperience", "between1And3", "between3And6", "moreThan6",
    }
