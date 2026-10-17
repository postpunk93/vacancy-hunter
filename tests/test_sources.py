from urllib.parse import parse_qs, urlsplit

import pytest

from hunter.models import SearchQuery
from hunter.sources import UnknownSource, build_url


def params(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def test_hh_url_uses_the_search_path_and_sorts_by_freshness():
    url = build_url("hh", SearchQuery(name="q", text="python"))

    parts = urlsplit(url)
    assert parts.netloc == "hh.ru"
    assert parts.path == "/search/vacancy"
    assert params(url)["order_by"] == "publication_time"


def test_zarplata_url_uses_its_own_path_but_the_same_parameters():
    url = build_url("zarplata", SearchQuery(name="q", text="python"))

    parts = urlsplit(url)
    assert parts.netloc == "www.zarplata.ru"
    assert parts.path == "/vacancies"
    assert params(url)["text"] == "python"
    assert params(url)["order_by"] == "publication_time"


def test_query_text_survives_spaces_and_cyrillic():
    url = build_url("hh", SearchQuery(name="q", text="python AND бэкенд"))
    assert params(url)["text"] == "python AND бэкенд"


def test_optional_filters_are_omitted_when_unset():
    url = build_url("hh", SearchQuery(name="q", text="python", only_with_salary=False))
    got = params(url)

    assert "salary" not in got
    assert "experience" not in got
    assert "work_format" not in got
    assert "only_with_salary" not in got
    assert "page" not in got


def test_optional_filters_are_included_when_set():
    url = build_url(
        "hh",
        SearchQuery(
            name="q",
            text="python",
            salary=200000,
            experience=("between3And6",),
            work_format="remote",
            search_period=3,
        ),
        page=2,
    )
    got = params(url)

    assert got["salary"] == "200000"
    assert got["experience"] == "between3And6"
    assert got["work_format"] == "REMOTE", "hh ждёт формат в верхнем регистре"
    assert got["only_with_salary"] == "true"
    assert got["search_period"] == "3"
    assert got["page"] == "2"


def test_unknown_source_is_rejected():
    with pytest.raises(UnknownSource, match="linkedin"):
        build_url("linkedin", SearchQuery(name="q", text="python"))


def test_multiple_experience_values_all_appear_in_the_url():
    url = build_url(
        "hh",
        SearchQuery(name="q", text="python", experience=("noExperience", "between1And3")),
    )

    values = parse_qs(urlsplit(url).query)["experience"]
    assert values == ["noExperience", "between1And3"]


@pytest.mark.parametrize(
    ("days", "sent"),
    [(1, "1"), (2, "3"), (3, "3"), (5, "7"), (7, "7"), (8, "30"), (30, "30")],
)
def test_search_period_is_rounded_up_to_a_value_hh_understands(days, sent):
    """hh принимает только 1, 3, 7 и 30 — остальное он молча игнорирует
    и отдаёт выдачу за всё время. Лишние дни отсекаются после разбора."""
    url = build_url("hh", SearchQuery(name="q", text="python", search_period=days))
    assert params(url)["search_period"] == sent
