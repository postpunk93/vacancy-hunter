import json
from pathlib import Path

import pytest

from hunter.parse import ParseError, extract_state
from hunter.platforms import get

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def cards(name, source="hh"):
    return get(source).parse_cards(fixture(name))


def test_parses_every_card_from_the_real_page():
    found = cards("hh_search_python_msk.html")
    vacancies = [card.vacancy for card in found]

    assert len(vacancies) == 51
    assert sum(v.is_ad for v in vacancies) == 1
    assert sum(not v.has_salary for v in vacancies) == 38


def test_advertising_card_is_flagged_not_dropped():
    first = cards("hh_search_python_msk.html")[0].vacancy

    assert first.vacancy_id == "114811554"
    assert first.is_ad is True, "отсевом рекламы занимается filters, не площадка"


def test_fields_are_filled_from_the_page():
    v = cards("hh_search_sorted_with_salary.html")[0].vacancy

    assert v.source == "hh"
    assert v.vacancy_id.isdigit()
    assert v.name
    assert v.company
    assert v.url.startswith("https://")
    assert "/vacancy/" in v.url
    assert v.published_at is not None
    assert v.published_at.tzinfo is not None
    assert v.published_at.year == 2026


def test_salary_bounds_currency_and_mode_are_captured():
    vacancies = [card.vacancy for card in cards("hh_search_sorted_with_salary.html")]

    assert all(v.has_salary for v in vacancies), "страница снята с only_with_salary"
    assert {v.currency for v in vacancies} == {"RUR", "USD"}
    assert {v.salary_mode for v in vacancies} == {"MONTH", "HOUR", "SERVICE"}
    assert any(v.gross is True for v in vacancies)
    assert any(v.gross is False for v in vacancies)


def test_zarplata_search_page_parses_every_card():
    """Страница выдачи zarplata разбирается целиком. Исходный баг был в адресе
    выдачи, его ловит тест URL, а не этот."""
    found = cards("zarplata_search_python.html", "zarplata")
    vacancies = [card.vacancy for card in found]

    assert len(vacancies) == 50
    assert {v.source for v in vacancies} == {"zarplata"}
    assert all(v.vacancy_id.isdigit() for v in vacancies)
    assert sum(not v.has_salary for v in vacancies) == 36


def test_missing_compensation_becomes_empty_bounds():
    vacancies = [card.vacancy for card in cards("zarplata_search_python.html", "zarplata")]

    v = next(v for v in vacancies if v.vacancy_id == "139289794")
    assert v.source == "zarplata"
    assert v.salary_from is None
    assert v.salary_to is None
    assert v.has_salary is False


def test_work_formats_are_flattened_into_a_tuple():
    vacancies = [card.vacancy for card in cards("hh_search_sorted_with_salary.html")]
    formats = {f for v in vacancies for f in v.work_formats}

    assert "REMOTE" in formats
    assert "ON_SITE" in formats
    assert all(isinstance(v.work_formats, tuple) for v in vacancies)


def test_page_without_the_state_template_is_a_parse_error():
    """Пустая страница без признаков капчи — редизайн: громкая ошибка разбора,
    а не «заблокировано», иначе владелец будет ждать часа впустую."""
    with pytest.raises(ParseError, match="HH-Lux-InitialState"):
        get("hh").parse_cards("<html><body>Ничего интересного</body></html>")


def test_captcha_page_without_the_state_template_is_a_captcha():
    from hunter.platforms.base import Captcha

    with pytest.raises(Captcha, match="капчу"):
        get("hh").parse_cards("<html><body>Подтвердите, что вы не робот</body></html>")


def test_broken_json_in_the_state_raises():
    with pytest.raises(ParseError, match="JSON"):
        extract_state('<template style="display:none" id="HH-Lux-InitialState">{oops</template>')


def test_state_without_search_result_raises():
    with pytest.raises(ParseError, match="vacancySearchResult"):
        get("hh").parse_cards(
            '<template style="display:none" id="HH-Lux-InitialState">{"other": 1}</template>'
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
    "publicationTime": {"$": "2026-09-01T00:00:00+03:00"},
    "compensation": {"from": 100000},
}


def test_renamed_vacancy_id_field_raises_parse_error():
    card = dict(_GOOD_CARD)
    del card["vacancyId"]
    card["vacancyIdV2"] = 1

    with pytest.raises(ParseError, match="KeyError"):
        get("hh").parse_cards(_state_with(card))


def test_unreadable_publication_date_raises_parse_error():
    card = dict(_GOOD_CARD)
    card["publicationTime"] = {"$": "1 сентября"}

    with pytest.raises(ParseError, match="ValueError"):
        get("hh").parse_cards(_state_with(card))


def test_company_as_a_plain_string_raises_parse_error():
    card = dict(_GOOD_CARD)
    card["company"] = "Тест"

    with pytest.raises(ParseError, match="AttributeError"):
        get("hh").parse_cards(_state_with(card))


def test_experience_code_is_captured():
    vacancies = [card.vacancy for card in cards("hh_search_sorted_with_salary.html")]

    assert {v.experience for v in vacancies} == {
        "noExperience", "between1And3", "between3And6", "moreThan6",
    }
