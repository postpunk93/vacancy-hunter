import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hunter.models import SearchQuery
from hunter.parse import ParseError
from hunter.platforms import format_ref, get, parse_ref
from hunter.platforms.base import Captcha
from hunter.platforms.rabota import card_row

FIXTURES = Path(__file__).parent / "fixtures"


def _query(**over):
    base = dict(name="author", text="редактор копирайтер", search_period=7,
                work_format="remote", area=113, max_pages=2)
    base.update(over)
    return SearchQuery(**base)


def _cards(name):
    return get("rabota").parse_cards((FIXTURES / name).read_text(encoding="utf-8"))


def test_search_url_asks_for_all_russia_remote_and_period():
    url = get("rabota").search_url(_query())

    assert url.startswith("https://www.rabota.ru/vacancy/?")
    assert "all_regions=1" in url
    assert "period=week" in url
    assert "schedule_ids=6" in url
    assert "page=" not in url, "page=0 — параметра нет вовсе"


def test_search_url_encodes_cyrillic_and_page_is_one_based():
    url = get("rabota").search_url(_query(), page=1)

    assert "query=%D1%80%D0%B5%D0%B4%D0%B0%D0%BA%D1%82%D0%BE%D1%80" in url
    assert "page=2" in url


def test_remote_schedule_and_salary_are_only_sent_when_asked():
    without = get("rabota").search_url(_query(work_format=None))

    assert "schedule_ids" not in without
    assert "min_salary" not in without
    assert "min_salary=120000" in get("rabota").search_url(_query(salary=120000))


def test_period_rounds_up_to_platform_ladder():
    platform = get("rabota")

    assert [platform.search_period(d) for d in (1, 2, 5, 7, 14, 60)] == [1, 3, 7, 7, 30, 30]
    assert "period=month" in platform.search_url(_query(search_period=30))


def test_cards_come_from_page_state():
    cards = _cards("rabota_search_editor.html")

    assert len(cards) == 2
    card = cards[0]
    assert (card.source, card.external_id) == ("rabota", "50107553")
    assert card.name == "Редактор китайского языка/Менеджер по работе с переводчиками"
    assert card.company.startswith("Индивидуальный предприниматель")
    assert card.url == "https://www.rabota.ru/vacancy/50107553/"
    assert card.area == "Москва"
    assert card.published_at == datetime(2026, 11, 8, 15, 43, 19,
                                        tzinfo=timezone(timedelta(hours=3)))
    assert (card.salary_from, card.salary_to) == (90000, None), "to=0 значит «границы нет»"
    assert (card.currency, card.salary_mode) == ("RUR", "MONTH")
    assert card.gross is None, "pay_type пустой — налоги неизвестны"
    assert card.work_formats == () and card.is_ad is False


def test_card_row_has_every_column_of_the_table():
    row = card_row({"id": 1, "title": "Т", "company": {"name": "К"}, "places": []})

    assert row["area"] is None
    assert row["salary_from"] is None and row["currency"] is None
    assert row["work_formats"] == "[]"
    assert {"source", "external_id", "name", "company", "company_id", "url",
            "area", "published_at", "salary_from", "salary_to", "currency",
            "gross", "salary_frequency", "experience", "work_formats",
            "employment_form", "employer_trusted", "employer_category",
            "antifraud_power", "responses_count", "card_json"} <= set(row)


def test_salary_period_comes_from_the_label_after_the_slash():
    hourly = card_row({"id": 2, "title": "Т", "salary": {
        "from": 0, "to": 900, "currency": "руб./час", "pay_type": "gross"}})

    assert (hourly["salary_from"], hourly["salary_to"]) == (None, 900)
    assert (hourly["currency"], hourly["salary_mode"]) == ("RUR", "HOUR")
    assert hourly["gross"] == 1

    unknown = card_row({"id": 3, "title": "Т", "salary": {
        "from": 1000, "to": 0, "currency": "руб./смена", "pay_type": "net"}})

    assert unknown["salary_mode"] is None, "незнакомый период не показываем месяцем"
    assert (unknown["currency"], unknown["gross"]) == ("RUR", 0)


def test_empty_search_is_not_an_error():
    assert _cards("rabota_search_empty.html") == []


def test_captcha_page_is_recognised():
    with pytest.raises(Captcha):
        get("rabota").parse_cards("<html>Подтвердите, что вы не робот</html>")


def test_page_without_state_and_captcha_words_is_a_parse_error():
    with pytest.raises(ParseError):
        get("rabota").parse_cards("<html>редизайн</html>")


def test_registry_knows_rabota():
    assert get("rabota").name == "rabota"


def _nuxt_page(payload: dict) -> str:
    """Минимальная страница с состоянием: JSON — законный JS-литерал."""
    state = json.dumps(payload, ensure_ascii=False)
    return f"<script>window.__NUXT__=(function(a){{return {state};}}({{}}));</script>"


def test_vacancy_url_is_canonical():
    assert get("rabota").vacancy_url("54461414") == (
        "https://www.rabota.ru/vacancy/54461414/"
    )


def test_detail_carries_full_text_skills_and_remote_format():
    detail = get("rabota").parse_detail(
        (FIXTURES / "rabota_vacancy.html").read_text(encoding="utf-8"))

    assert detail.external_id == "54461414"
    assert detail.description.startswith("Ищем контент-менеджера")
    assert "<p>" not in detail.description
    assert detail.key_skills == ["пунктуальность", "коммуникабельность", "грамотность",
                                 "обучаемость", "корректура текста"]
    assert detail.status == "active"
    assert detail.row["work_formats"] == '["REMOTE"]'


def test_unpublished_vacancy_is_archived():
    page = _nuxt_page({"state": {"rabota": {"vacancy": {"vacancy": {
        "id": 1, "title": "Т", "status": "closed", "description": "<p>текст</p>",
        "company": {"name": "К"}, "places": [], "skills": []}}}}})

    assert get("rabota").parse_detail(page).status == "archived"


def test_captcha_page_on_vacancy_is_recognised():
    with pytest.raises(Captcha):
        get("rabota").parse_detail("<html>Подтвердите, что вы не робот</html>")


def test_vacancy_page_without_vacancy_node_is_a_parse_error():
    with pytest.raises(ParseError):
        get("rabota").parse_detail(_nuxt_page({"state": {"rabota": {"vacancy": {}}}}))


def test_ref_forms_are_understood():
    platform = get("rabota")

    assert platform.parse_ref("https://www.rabota.ru/vacancy/54461414/") == "54461414"
    assert platform.parse_ref("https://rabota.ru/vacancy/54461414") == "54461414"
    assert platform.parse_ref("https://hh.ru/vacancy/54461414") is None
    assert parse_ref("rabota:54461414") == ("rabota", "54461414")
    assert format_ref("rabota", "54461414") == "rabota:54461414"


def test_unsupported_lists_what_the_site_ignores():
    platform = get("rabota")

    assert platform.unsupported(_query(only_with_salary=False)) == ()
    ignored = platform.unsupported(_query(
        area=1, only_with_salary=True,
        experience=("between1And3",), work_format="hybrid",
    ))
    assert set(ignored) == {"area", "only_with_salary", "experience", "work_format"}


def test_search_without_total_vacancies_is_a_parse_error():
    """Пустая выдача — это явный ноль, а не пропавшее поле: иначе смена
    формата выглядела бы как «вакансий нет»."""
    page = _nuxt_page({"state": {"rabota": {"search": {}}}})

    with pytest.raises(ParseError):
        get("rabota").parse_cards(page)
