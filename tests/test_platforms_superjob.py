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


def test_detail_comes_from_jsonld():
    html = (FIXTURES / "superjob_vacancy.html").read_text(encoding="utf-8")

    detail = get("superjob").parse_detail(html)

    assert detail.source == "superjob" and detail.external_id == "52295791"
    assert detail.row["name"].startswith("Ведущий менеджер")
    assert detail.row["company"] == "Уральский банк реконструкции и развития"
    assert detail.row["published_at"].startswith("2026-10-30")
    assert detail.row["salary_from"] == 75000
    assert detail.row["salary_to"] is None
    assert detail.row["currency"] == "RUR"
    assert detail.row["area"] == "Улан-Удэ"
    assert detail.row["employment_form"] == "FULL"
    assert detail.description and "<p>" not in detail.description
    assert detail.key_skills == []
    assert detail.status == "active"


def test_detail_page_without_jobposting_is_a_parse_error():
    with pytest.raises(ParseError, match="JobPosting"):
        get("superjob").parse_detail('<html><script>window.APP_STATE={}</script></html>')


def test_detail_page_without_anything_is_a_captcha():
    with pytest.raises(Captcha):
        get("superjob").parse_detail("<html><body>captcha</body></html>")


def test_superjob_link_adds_vacancy_end_to_end(tmp_path):
    from hunter.corpus.collect import fetch_details
    from hunter.corpus.store import Corpus

    html = (FIXTURES / "superjob_vacancy.html").read_text(encoding="utf-8")

    def fetch_page(url):
        assert "52295791" in url, url
        return html

    with Corpus.open(tmp_path / "db.sqlite", create=True) as corpus:
        report = fetch_details(corpus, fetch_page, [("superjob", "52295791")])
        assert report.fetched == ["superjob:52295791"]
        vacancy_id = corpus.find("superjob", "52295791")
        assert corpus.vacancy(vacancy_id)["description"]
        assert corpus.vacancy(vacancy_id)["published_at"].startswith("2026-10-30")


def test_superjob_reports_unsupported_filters():
    query = SearchQuery(name="a", text="x", area=1, salary=100000,
                        experience=("between1And3",), only_with_salary=True)
    assert set(get("superjob").unsupported(query)) == {
        "area", "salary", "experience", "only_with_salary"}


def test_superjob_supports_remote_and_does_not_complain_about_it():
    query = _query(area=113, only_with_salary=False)
    assert get("superjob").unsupported(query) == ()


def test_hh_does_not_report_unsupported_filters():
    assert get("hh").unsupported(SearchQuery(name="a", text="x", salary=1)) == ()


def _state_page(state):
    import json

    return f"<html><script>window.APP_STATE={json.dumps(state)}</script></html>"


def test_card_without_entities_is_a_parse_error():
    state = {
        "pageInfo": {"pageType": "search-vacancy"},
        "responses": {"lists": {"vacancy": {"u": {"result": ["1"]}}}},
        "entities": {"vacancy": {"1": {"id": "1", "relationships": {}}}},
    }

    with pytest.raises(ParseError, match="сущности"):
        get("superjob").parse_cards(_state_page(state))


def test_broken_card_is_a_parse_error_not_a_key_error():
    """Сырой KeyError из карточки прошёл бы мимо run (он ловит ParseError)
    и уронил бы весь проход, включая дайджест."""
    state = {
        "pageInfo": {"pageType": "search-vacancy"},
        "responses": {"lists": {"vacancy": {"u": {"result": ["1"]}}}},
        "entities": {
            # у вакансии нет своего id — его ждёт разбор вилки
            "vacancy": {"1": {"relationships": {}}},
            "vacancyMainInfo": {"1": {"id": "1", "attributes": {"profession": "Редактор"},
                                      "relationships": {}}},
        },
    }

    with pytest.raises(ParseError, match="сменил формат"):
        get("superjob").parse_cards(_state_page(state))
