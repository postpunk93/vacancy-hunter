from hunter.models import SearchQuery
from tests.factories import make_vacancy as _vacancy


def test_key_includes_source_because_ids_collide_across_sites():
    hh = _vacancy(source="hh", vacancy_id="42")
    zp = _vacancy(source="zarplata", vacancy_id="42")
    assert hh.key == "hh:42"
    assert zp.key == "zarplata:42"
    assert hh.key != zp.key


def test_has_salary_is_true_when_only_one_bound_is_known():
    assert _vacancy(salary_from=100, salary_to=None).has_salary
    assert _vacancy(salary_from=None, salary_to=100).has_salary
    assert not _vacancy(salary_from=None, salary_to=None).has_salary


def test_search_query_defaults_match_the_spec():
    q = SearchQuery(name="Python", text="python")
    assert q.area == 1
    assert q.only_with_salary is True
    assert q.search_period == 1
    assert q.max_pages == 1
    assert q.salary is None
    assert q.experience == ()


def test_vacancy_carries_the_raw_experience_code():
    assert _vacancy(experience="between1And3").experience == "between1And3"


def test_score_without_value_is_not_scored():
    from hunter.models import Score

    assert Score(None, "x").scored is False
    assert Score(7, "x").scored is True


def test_text_falls_back_to_common_text():
    from hunter.models import SearchQuery

    query = SearchQuery(name="a", text="редактор")
    assert query.text_for("superjob") == "редактор"


def test_platform_absent_from_map_falls_back_to_common_text():
    """Карта переопределяет площадки, но не выключает остальные: иначе запрос
    с одной формулировкой под superjob молча перестаёт идти на hh."""
    from hunter.models import SearchQuery

    query = SearchQuery(name="a", text="NAME:(редактор)",
                        text_by_source={"superjob": "редактор"})
    assert query.text_for("hh") == "NAME:(редактор)"
    assert query.text_for("superjob") == "редактор"


def test_null_text_means_do_not_send():
    from hunter.models import SearchQuery

    query = SearchQuery(name="a", text="редактор", text_by_source={"superjob": None})
    assert query.text_for("superjob") is None
