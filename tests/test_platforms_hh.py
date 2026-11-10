import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from hunter.models import SearchQuery
from hunter.parse import ParseError, extract_state
from hunter.platforms import PLATFORMS, UnknownPlatform, get
from hunter.platforms.base import Captcha, vacancy_card_row
from hunter.platforms.hh import card_row, parse_detail, search_cards
from tests.factories import make_vacancy, search_card, search_page, state_page, vacancy_page

FIXTURES = Path(__file__).parent / "fixtures"


def params(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def real_cards(name):
    state = extract_state(fixture(name))
    return state["vacancySearchResult"]["vacancies"]


# --- адрес выдачи -------------------------------------------------------


def test_hh_search_url_keeps_hh_parameters():
    query = SearchQuery(name="author", text="редактор", area=113, search_period=3,
                        work_format="remote", only_with_salary=True, max_pages=2)
    url = get("hh").search_url(query, page=0)

    assert url.startswith("https://hh.ru/search/vacancy?")
    assert params(url)["text"] == "редактор"
    assert params(url)["area"] == "113"
    assert params(url)["search_period"] == "3"
    assert params(url)["work_format"] == "REMOTE"
    assert params(url)["only_with_salary"] == "true"
    assert "page" not in params(url), "page=0 — параметра нет вовсе"
    assert params(get("hh").search_url(query, page=2))["page"] == "2"


def test_hh_url_sorts_by_freshness():
    url = get("hh").search_url(SearchQuery(name="q", text="python"))

    parts = urlsplit(url)
    assert parts.netloc == "hh.ru"
    assert parts.path == "/search/vacancy"
    assert params(url)["order_by"] == "publication_time"


def test_zarplata_is_its_own_platform_with_hh_engine():
    assert get("zarplata").name == "zarplata"
    url = get("zarplata").search_url(SearchQuery(name="q", text="python"))

    parts = urlsplit(url)
    assert parts.netloc == "www.zarplata.ru"
    assert parts.path == "/search/vacancy", "на /vacancies у zarplata не поиск, а одна карточка"
    assert params(url)["text"] == "python"


def test_query_text_survives_spaces_and_cyrillic():
    url = get("hh").search_url(SearchQuery(name="q", text="python AND бэкенд"))
    assert params(url)["text"] == "python AND бэкенд"


def test_optional_filters_are_omitted_when_unset():
    url = get("hh").search_url(SearchQuery(name="q", text="python", only_with_salary=False))
    got = params(url)

    assert "salary" not in got
    assert "experience" not in got
    assert "work_format" not in got
    assert "only_with_salary" not in got
    assert "page" not in got


def test_multiple_experience_values_all_appear_in_the_url():
    url = get("hh").search_url(
        SearchQuery(name="q", text="python", experience=("noExperience", "between1And3")),
    )

    assert parse_qs(urlsplit(url).query)["experience"] == ["noExperience", "between1And3"]


@pytest.mark.parametrize(
    ("days", "sent"),
    [(1, 1), (2, 3), (3, 3), (5, 7), (7, 7), (8, 30), (30, 30)],
)
def test_search_period_is_rounded_up_to_a_value_hh_understands(days, sent):
    """hh принимает только 1, 3, 7 и 30 — остальное он молча игнорирует
    и отдаёт выдачу за всё время. Лишние дни отсекаются после разбора."""
    assert get("hh").search_period(days) == sent
    url = get("hh").search_url(SearchQuery(name="q", text="python", search_period=days))
    assert params(url)["search_period"] == str(sent)


def test_unknown_platform_is_rejected():
    with pytest.raises(UnknownPlatform, match="linkedin"):
        get("linkedin")
    assert set(PLATFORMS) >= {"hh", "zarplata"}


# --- карточка выдачи ----------------------------------------------------


def test_card_row_extracts_quality_signals_from_a_real_card():
    raw = next(c for c in real_cards("hh_search_sorted_with_salary.html") if c["vacancyId"] == 136823260)

    row = card_row(raw)

    assert row["external_id"] == "136823260"
    assert row["source"] == "hh"
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

    row = card_row(raw, "zarplata")

    assert row["source"] == "zarplata"
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


def test_hh_parse_cards_matches_fixture():
    cards = get("hh").parse_cards(fixture("hh_search_python_msk.html"))

    first = cards[0]
    assert first.source == "hh" and first.external_id == "114811554"
    assert first.name and first.company
    assert first.row["external_id"] == "114811554"
    assert first.row["published_at"].startswith("2026-08-16")
    assert first.published_at is not None
    assert first.is_ad is True


def test_hh_parse_cards_counts_every_card():
    cards = get("hh").parse_cards(fixture("hh_search_python_msk.html"))

    assert len(cards) == 51
    assert sum(card.vacancy.has_salary for card in cards) == 51 - 38


def test_broken_card_is_a_parse_error_not_a_key_error():
    broken = search_card(1)
    del broken["vacancyId"]

    with pytest.raises(ParseError, match="KeyError"):
        get("hh").parse_cards(search_page([broken]))


# --- страница вакансии --------------------------------------------------


def test_parse_detail_builds_description_and_row():
    detail = parse_detail(vacancy_page(42))

    assert detail.external_id == "42"
    assert detail.source == "hh"
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


def test_hh_parse_detail_returns_full_text():
    detail = get("hh").parse_detail(fixture("hh_vacancy_page.html"))

    assert detail.source == "hh" and detail.external_id.isdigit()
    assert detail.description and detail.row["name"]
    assert re.search(r"<[a-z]", detail.description) is None, "в описании остались теги"


# --- строка для корпуса -------------------------------------------------


def test_vacancy_card_row_has_every_card_column():
    from hunter.corpus.store import _CARD_COLUMNS

    vacancy = make_vacancy(vacancy_id="7", source="zarplata", gross=False)
    row = vacancy_card_row(vacancy)

    assert set(row) == {"source", "external_id", *_CARD_COLUMNS}
    assert (row["source"], row["external_id"]) == ("zarplata", "7")
    assert row["url"] == "https://hh.ru/vacancy/7"
    assert row["published_at"] == "2026-09-01T09:41:00+00:00"
    assert row["gross"] == 0
    assert json.loads(row["work_formats"]) == ["REMOTE"]
    assert row["card_json"] is None and row["responses_count"] is None


def test_vacancy_card_row_keeps_unknown_gross_unknown():
    assert vacancy_card_row(make_vacancy(gross=None))["gross"] is None
    assert vacancy_card_row(make_vacancy(gross=True))["gross"] == 1


def test_vacancy_card_row_allows_unknown_date():
    assert vacancy_card_row(make_vacancy(published_at=None))["published_at"] is None


# --- ссылка на вакансию -------------------------------------------------


def test_parse_ref_forms():
    from hunter.platforms import parse_ref

    assert parse_ref("114811554") == ("hh", "114811554")
    assert parse_ref("zarplata:114811554") == ("zarplata", "114811554")
    assert parse_ref("https://hh.ru/vacancy/114811554?from=serp") == ("hh", "114811554")
    assert parse_ref("https://m.hh.ru/vacancy/114811554") == ("hh", "114811554")
    assert parse_ref("https://www.zarplata.ru/vacancy/114811554") == ("hh", "114811554")


def test_parse_ref_rejects_foreign_links():
    from hunter.platforms import parse_ref

    for value in ("abc", "", "https://evil.com/vacancy/1", "https://hh.ru/employer/1",
                  "https://hh.ru/vacancy/abc", "ftp://hh.ru/vacancy/1", "avito:123"):
        with pytest.raises(ValueError):
            parse_ref(value)


def test_format_ref_is_bare_for_hh():
    from hunter.platforms import format_ref

    assert format_ref("hh", "114811554") == "114811554"
    assert format_ref("zarplata", "114811554") == "zarplata:114811554"


def test_ref_round_trip():
    from hunter.platforms import format_ref, parse_ref

    for source, external_id in (("hh", "1"), ("zarplata", "3")):
        assert parse_ref(format_ref(source, external_id)) == (source, external_id)
