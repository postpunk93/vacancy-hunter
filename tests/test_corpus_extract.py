import json
import re
from pathlib import Path

import pytest

from hunter.corpus.extract import Captcha, card_row, parse_detail, search_cards, vacancy_card_row
from hunter.parse import ParseError, extract_state
from tests.factories import make_vacancy, search_card, search_page, state_page, vacancy_page

FIXTURES = Path(__file__).parent / "fixtures"


def real_cards(name):
    state = extract_state((FIXTURES / name).read_text(encoding="utf-8"))
    return state["vacancySearchResult"]["vacancies"]


def test_card_row_extracts_quality_signals_from_a_real_card():
    raw = next(c for c in real_cards("hh_search_sorted_with_salary.html") if c["vacancyId"] == 136823260)

    row = card_row(raw)

    assert row["id"] == 136823260
    assert row["url"] == "https://hh.ru/vacancy/136823260", "не региональный поддомен"
    assert row["employer_category"] == "PRIVATE_INDIVIDUAL"
    assert row["employer_trusted"] == 0
    assert row["antifraud_power"] == pytest.approx(0.8174265494414773)
    assert row["salary_frequency"] == "PER_PROJECT"
    assert row["responses_count"] == 19
    assert row["published_at"] == "2026-09-01T09:41:55.502+03:00"
    assert json.loads(row["work_formats"]) == ["REMOTE"]
    assert json.loads(row["card_json"])["vacancyId"] == 136823260


def test_card_without_salary_or_antifraud():
    raw = next(c for c in real_cards("zarplata_search_python.html") if c["vacancyId"] == 139289794)

    row = card_row(raw)

    assert row["salary_from"] is None and row["salary_to"] is None
    assert row["currency"] is None
    assert row["antifraud_power"] is None
    assert row["employer_trusted"] == 1


def test_antifraud_without_power_counts_as_flagged():
    raw = search_card(1, vacancyProperties={"properties": [{"property": [
        {"propertyType": "HH_ANTIFRAUD_SUSPICIOUS_VACANCY", "parameters": []},
    ]}]})

    assert card_row(raw)["antifraud_power"] == 0.0


def test_card_without_id_is_an_error():
    raw = search_card(1)
    del raw["vacancyId"]

    with pytest.raises(KeyError):
        card_row(raw)


def test_search_cards_returns_raw_cards():
    assert [c["vacancyId"] for c in search_cards(search_page([search_card(1), search_card(2)]))] == [1, 2]


def test_page_without_state_is_a_captcha():
    with pytest.raises(Captcha):
        search_cards("<html><body>Подтвердите, что вы не робот</body></html>")


def test_state_without_search_result_is_a_parse_error():
    with pytest.raises(ParseError):
        search_cards(state_page({"somethingElse": {}}))


def test_parse_detail_builds_description_and_row():
    detail = parse_detail(vacancy_page(42))

    assert detail.vacancy_id == 42
    assert detail.description == "Пишем на **Python**"
    assert detail.key_skills == ["Python", "SQL"]
    assert detail.status == "active"
    assert detail.row["url"] == "https://hh.ru/vacancy/42"
    assert detail.row["employer_trusted"] == 1
    assert detail.row["salary_to"] == 280000
    assert json.loads(detail.row["work_formats"]) == ["ON_SITE"]
    assert detail.row["card_json"] is None
    assert detail.row["antifraud_power"] is None


def test_archived_vacancy_status():
    assert parse_detail(vacancy_page(42, status={"archived": True})).status == "archived"


def test_detail_page_without_vacancy_is_a_parse_error():
    with pytest.raises(ParseError):
        parse_detail(state_page({"vacancyView": {}}))


def test_detail_page_without_state_is_a_captcha():
    with pytest.raises(Captcha):
        parse_detail("<html>captcha</html>")


def test_real_vacancy_page():
    detail = parse_detail((FIXTURES / "hh_vacancy_page.html").read_text(encoding="utf-8"))

    assert detail.vacancy_id > 0
    assert detail.description
    assert re.search(r"<[a-z]", detail.description) is None, "в описании остались теги"
    assert detail.key_skills and all(isinstance(s, str) for s in detail.key_skills)
    assert detail.status in ("active", "archived")
    assert detail.row["name"]
    assert isinstance(json.loads(detail.row["work_formats"]), list)


def test_vacancy_card_row_has_every_card_column():
    from hunter.corpus.store import _CARD_COLUMNS

    row = vacancy_card_row(make_vacancy(vacancy_id="7", source="zarplata", gross=False))

    assert set(row) == {"id", *_CARD_COLUMNS}
    assert row["id"] == 7
    assert row["url"] == "https://hh.ru/vacancy/7"
    assert row["published_at"] == "2026-09-01T09:41:00+00:00"
    assert row["gross"] == 0
    assert json.loads(row["work_formats"]) == ["REMOTE"]
    assert row["card_json"] is None and row["responses_count"] is None


def test_vacancy_card_row_keeps_unknown_gross_unknown():
    assert vacancy_card_row(make_vacancy(gross=None))["gross"] is None
    assert vacancy_card_row(make_vacancy(gross=True))["gross"] == 1
