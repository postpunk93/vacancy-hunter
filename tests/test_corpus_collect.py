import pytest

from hunter.corpus.collect import (
    EXIT_BLOCKED, EXIT_OK, EXIT_PARTIAL, collect, fetch_details,
)
from hunter.corpus.store import Corpus
from hunter.fetch import Blocked, FetchError, NotFound
from hunter.models import SearchQuery
from hunter.platforms import get
from tests.factories import search_card, search_page, vacancy_page

QUERY = SearchQuery(name="backend", text="python", area=113, only_with_salary=False,
                    search_period=30, max_pages=3)
CAPTCHA = "<html><body>Вы не робот?</body></html>"


def page_url(n):
    return get("hh").search_url(QUERY, n)


def vacancy_url(vacancy_id):
    return f"https://hh.ru/vacancy/{vacancy_id}"


def refs(ids):
    """Голые id — это вакансии hh; докачка теперь принимает пары."""
    return [("hh", str(i)) for i in ids]


def full_page(start, count=50):
    return search_page([search_card(i) for i in range(start, start + count)])


class Site:
    """Заготовленные ответы по URL; исключение в словаре — бросается."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        item = self.pages[url]
        if isinstance(item, Exception):
            raise item
        return item


class Waits:
    def __init__(self):
        self.count = 0

    def __call__(self):
        self.count += 1


@pytest.fixture
def corpus(tmp_path):
    with Corpus.open(tmp_path / "v.db", create=True) as opened:
        yield opened


def test_collect_stops_on_short_page_and_records_hits(corpus):
    site = Site({page_url(0): full_page(1), page_url(1): full_page(100, 10)})
    waits = Waits()

    report = collect(corpus, site, QUERY, "backend", wait=waits)

    assert site.calls == [page_url(0), page_url(1)]
    assert (report.cards, report.new, report.existing, report.pages) == (60, 60, 0, 2)
    assert report.exit_code == EXIT_OK
    assert waits.count == 1, "пауза между страницами, но не перед первой"
    assert [(h["role"], h["query"]) for h in corpus.hits(100)] == [("backend", "python")]


def test_collect_respects_max_pages(corpus):
    site = Site({page_url(n): full_page(n * 100) for n in range(5)})

    report = collect(corpus, site, QUERY, "backend")

    assert len(site.calls) == 3
    assert report.pages == 3


def test_collect_empty_result(corpus):
    report = collect(corpus, Site({page_url(0): search_page([])}), QUERY, "backend")

    assert (report.cards, report.pages, report.exit_code) == (0, 1, EXIT_OK)


def test_ad_duplicate_on_next_page_is_counted_once(corpus):
    second = search_page([search_card(1, **{"@isAdv": True}), search_card(500), search_card(501)])
    site = Site({page_url(0): full_page(1), page_url(1): second})

    report = collect(corpus, site, QUERY, "backend")

    assert report.cards == 52
    assert report.new == 52


def test_second_collect_counts_existing(corpus):
    pages = {page_url(0): full_page(1, 10)}
    collect(corpus, Site(pages), QUERY, "backend")

    report = collect(corpus, Site(pages), QUERY, "backend")

    assert (report.cards, report.new, report.existing) == (10, 0, 10)


def test_block_stops_collect_and_keeps_what_was_saved(corpus):
    site = Site({page_url(0): full_page(1), page_url(1): Blocked("403")})

    report = collect(corpus, site, QUERY, "backend")

    assert report.exit_code == EXIT_BLOCKED
    assert report.cards == 50
    assert corpus.exists(1)
    assert "блокировка" in report.summary()


def test_captcha_counts_as_block(corpus):
    report = collect(corpus, Site({page_url(0): CAPTCHA}), QUERY, "backend")

    assert report.exit_code == EXIT_BLOCKED


def test_network_failure_stops_with_partial_code(corpus):
    report = collect(corpus, Site({page_url(0): FetchError("timeout")}), QUERY, "backend")

    assert report.exit_code == EXIT_PARTIAL
    assert "timeout" in report.summary()


def test_broken_card_rejects_the_whole_page(corpus):
    broken = search_card(2)
    del broken["vacancyId"]
    site = Site({page_url(0): search_page([search_card(1), broken])})

    report = collect(corpus, site, QUERY, "backend")

    assert report.exit_code == EXIT_PARTIAL
    assert not corpus.exists(1), "страница пишется целиком или никак"


def test_fetch_details_saves_text_and_pauses_between_requests(corpus):
    site = Site({vacancy_url(1): vacancy_page(1), vacancy_url(2): vacancy_page(2)})
    waits = Waits()

    report = fetch_details(corpus, site, refs([1, 2]), wait=waits)

    assert report.fetched == ["1", "2"]
    assert report.exit_code == EXIT_OK
    assert waits.count == 1
    assert corpus.vacancy(2)["description"] == "Пишем на **Python**"


def test_fetch_details_skips_known_text_unless_refresh(corpus):
    fetch_details(corpus, Site({vacancy_url(1): vacancy_page(1)}), refs([1]))

    site = Site({vacancy_url(1): vacancy_page(1, description="<p>Новое</p>")})
    assert fetch_details(corpus, site, refs([1])).skipped == ["1"]
    assert site.calls == []

    assert fetch_details(corpus, site, refs([1]), refresh=True).fetched == ["1"]
    assert corpus.vacancy(1)["description"] == "Новое"


def test_fetch_details_marks_deleted_vacancies(corpus):
    fetch_details(corpus, Site({vacancy_url(1): vacancy_page(1)}), refs([1]))
    site = Site({vacancy_url(1): NotFound("404"), vacancy_url(9): NotFound("404")})

    report = fetch_details(corpus, site, refs([1, 9]), refresh=True)

    assert report.deleted == ["1", "9"]
    assert report.exit_code == EXIT_OK
    assert corpus.vacancy(1)["status"] == "deleted"
    assert not corpus.exists(9), "строку для неизвестной удалённой вакансии не заводим"


def test_one_failure_does_not_stop_the_rest(corpus):
    site = Site({vacancy_url(1): FetchError("timeout"), vacancy_url(2): vacancy_page(2)})

    report = fetch_details(corpus, site, refs([1, 2]))

    assert report.fetched == ["2"]
    assert report.failed == [("1", "timeout")]
    assert report.exit_code == EXIT_PARTIAL


def test_malformed_page_is_a_failure_not_a_crash(corpus):
    site = Site({
        vacancy_url(1): vacancy_page(1, status="archived"),  # строка вместо объекта
        vacancy_url(2): vacancy_page(2),
    })

    report = fetch_details(corpus, site, refs([1, 2]))

    assert report.fetched == ["2"]
    assert [ref for ref, _ in report.failed] == ["1"]
    assert "сменил формат" in report.failed[0][1]
    assert report.exit_code == EXIT_PARTIAL


@pytest.mark.parametrize("answer", [Blocked("403"), CAPTCHA])
def test_block_stops_fetching_and_lists_the_rest(corpus, answer):
    site = Site({vacancy_url(1): vacancy_page(1), vacancy_url(2): answer})

    report = fetch_details(corpus, site, refs([1, 2, 3]))

    assert report.fetched == ["1"]
    assert report.not_reached == ["2", "3"]
    assert report.exit_code == EXIT_BLOCKED
    assert vacancy_url(3) not in site.calls


def test_role_tags_fetched_and_already_known_vacancies(corpus):
    fetch_details(corpus, Site({vacancy_url(1): vacancy_page(1)}), refs([1]))

    fetch_details(corpus, Site({vacancy_url(2): vacancy_page(2)}), refs([1, 2]), role="backend")

    assert [(h["role"], h["query"]) for h in corpus.hits(1)] == [("backend", "manual")]
    assert [(h["role"], h["query"]) for h in corpus.hits(2)] == [("backend", "manual")]


def test_fetch_adds_vacancy_missing_from_base(corpus):
    """Вакансию по ссылке заводят в базе, даже если в выдаче её не было."""
    site = Site({vacancy_url(7): vacancy_page(7)})

    report = fetch_details(corpus, site, refs([7]))

    assert report.fetched == ["7"]
    assert corpus.find("hh", "7") is not None
    assert corpus.vacancy(7)["description"]
