import pytest

from hunter.fetch import Blocked, FetchError, NotFound
from hunter.fulltext import fetch_texts
from hunter.store import Store
from tests.factories import make_detail, make_vacancy, vacancy_page


class Pages:
    """fetch_page для тестов: страница вакансии по id из адреса, или исключение."""

    def __init__(self, errors=None):
        self.urls = []
        self.errors = errors or {}

    def __call__(self, url):
        self.urls.append(url)
        vacancy_id = int(url.rsplit("/", 1)[1])
        error = self.errors.get(vacancy_id)
        if isinstance(error, Exception):
            raise error
        if error == "captcha":
            return "<html>капча</html>"
        return vacancy_page(vacancy_id, description=f"<p>Текст {vacancy_id}</p>")


def vacancies(*ids):
    return [make_vacancy(vacancy_id=str(i), url=f"https://hh.ru/vacancy/{i}") for i in ids]


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "s.db") as s:
        yield s


def test_texts_are_fetched_and_cached(store):
    pages = Pages()
    texts, warnings = fetch_texts(vacancies(1, 2), fetch_page=pages, store=store, pause=lambda s: None)

    assert pages.urls == ["https://hh.ru/vacancy/1", "https://hh.ru/vacancy/2"]
    assert set(texts) == {"hh:1", "hh:2"}
    assert "Текст 1" in texts["hh:1"].description
    assert texts["hh:1"].key_skills == ("Python", "SQL")
    assert store.get_text("hh:2") == texts["hh:2"]
    assert warnings == []


def test_zarplata_vacancy_text_comes_from_hh(store):
    pages = Pages()
    vacancy = make_vacancy(source="zarplata", vacancy_id="7")
    texts, _ = fetch_texts([vacancy], fetch_page=pages, store=store, pause=lambda s: None)

    assert pages.urls == ["https://hh.ru/vacancy/7"]
    assert "zarplata:7" in texts


def test_cached_text_is_not_refetched(store):
    store.corpus.save_detail(make_detail(vacancy_id=1, description="из кеша"))
    pages = Pages()
    texts, _ = fetch_texts(vacancies(1, 2), fetch_page=pages, store=store, pause=lambda s: None)

    assert pages.urls == ["https://hh.ru/vacancy/2"]
    assert texts["hh:1"].description == "из кеша"


def test_pause_only_between_real_requests(store):
    store.corpus.save_detail(make_detail(vacancy_id=2, description="из кеша"))
    pauses = []
    fetch_texts(
        vacancies(1, 2, 3), fetch_page=Pages(), store=store,
        pause=pauses.append, jitter=lambda a, b: 4.2,
    )
    assert pauses == [4.2]


def test_block_stops_and_warns(store):
    pages = Pages(errors={2: Blocked("403")})
    texts, warnings = fetch_texts(vacancies(1, 2, 3), fetch_page=pages, store=store, pause=lambda s: None)

    assert pages.urls == ["https://hh.ru/vacancy/1", "https://hh.ru/vacancy/2"]
    assert set(texts) == {"hh:1"}
    assert len(warnings) == 1
    assert "не скачано 2" in warnings[0]


def test_captcha_page_counts_as_block(store):
    pages = Pages(errors={1: "captcha"})
    texts, warnings = fetch_texts(vacancies(1, 2), fetch_page=pages, store=store, pause=lambda s: None)

    assert texts == {}
    assert len(pages.urls) == 1
    assert "не скачано 2" in warnings[0]


def test_block_warning_names_the_blocked_platform(store):
    vacancy = make_vacancy(source="rabota", vacancy_id="54461414",
                           url="https://www.rabota.ru/vacancy/54461414/")

    def fetch_page(url):
        return "<html>Подтвердите, что вы не робот</html>"

    texts, warnings = fetch_texts([vacancy], fetch_page=fetch_page, store=store,
                                  pause=lambda s: None)

    assert texts == {}
    assert warnings[0].startswith("rabota заблокировал"), "площадка в предупреждении — та, что отказала"


def test_single_failures_are_skipped(store):
    pages = Pages(errors={1: NotFound("404"), 2: FetchError("timeout")})
    texts, warnings = fetch_texts(vacancies(1, 2, 3), fetch_page=pages, store=store, pause=lambda s: None)

    assert set(texts) == {"hh:3"}
    assert warnings == []


def test_cached_texts_after_a_block_are_still_used(store):
    store.corpus.save_detail(make_detail(vacancy_id=3, description="из кеша"))
    pages = Pages(errors={1: Blocked("403")})
    texts, warnings = fetch_texts(vacancies(1, 2, 3), fetch_page=pages, store=store, pause=lambda s: None)

    assert set(texts) == {"hh:3"}
    assert len(pages.urls) == 1
    assert "не скачано 2" in warnings[0]
