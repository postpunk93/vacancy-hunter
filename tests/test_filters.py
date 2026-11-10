from datetime import datetime, timezone

from hunter.filters import (
    dedupe,
    drop_ads,
    drop_published_before,
    drop_without_salary,
    sort_by_freshness,
)
from hunter.models import UNKNOWN_COMPANY, UNKNOWN_VACANCY_NAME
from tests.factories import make_vacancy


def at(day):
    return datetime(2026, 9, day, 12, 0, tzinfo=timezone.utc)


def test_ads_are_dropped():
    kept = make_vacancy(vacancy_id="1", is_ad=False)
    ad = make_vacancy(vacancy_id="2", is_ad=True)

    assert drop_ads([kept, ad]) == [kept]


def test_vacancies_without_salary_are_dropped():
    with_salary = make_vacancy(vacancy_id="1")
    without = make_vacancy(vacancy_id="2", salary_from=None, salary_to=None)

    assert drop_without_salary([with_salary, without]) == [with_salary]


def test_sorted_from_freshest_to_oldest():
    old = make_vacancy(vacancy_id="1", published_at=at(1))
    new = make_vacancy(vacancy_id="2", published_at=at(3))
    mid = make_vacancy(vacancy_id="3", published_at=at(2))

    assert sort_by_freshness([old, new, mid]) == [new, mid, old]


def test_duplicate_stays_in_the_first_group_it_appeared_in():
    shared = make_vacancy(vacancy_id="100")
    only_second = make_vacancy(
        vacancy_id="200", company="Другая компания", name="Другая вакансия"
    )

    result, absorbed = dedupe([("Первый", [shared]), ("Второй", [shared, only_second])])

    assert result == [("Первый", [shared]), ("Второй", [only_second])]
    assert absorbed == []


def test_same_id_from_different_sources_is_not_a_duplicate():
    hh = make_vacancy(source="hh", vacancy_id="42", company="Авито")
    zp = make_vacancy(source="zarplata", vacancy_id="42", company="Яндекс")

    result, absorbed = dedupe([("Запрос", [hh, zp])])

    assert result == [("Запрос", [hh, zp])]
    assert absorbed == []


def test_dedupe_keeps_empty_groups_so_the_caller_can_decide():
    shared = make_vacancy(vacancy_id="100")

    result, absorbed = dedupe([("Первый", [shared]), ("Второй", [shared])])

    assert result == [("Первый", [shared]), ("Второй", [])]
    assert absorbed == []


def test_same_company_and_name_across_cities_are_merged_into_one():
    moscow = make_vacancy(vacancy_id="1", company="Roobinium", name="SalesMan", area="Москва")
    kazan = make_vacancy(vacancy_id="2", company="Roobinium", name="SalesMan", area="Казань")

    result, absorbed = dedupe([("Запрос", [moscow, kazan])])

    assert result[0][1][0].area == "Москва, Казань"
    assert absorbed == [kazan]


def test_merged_area_lists_up_to_three_cities_in_full():
    a = make_vacancy(vacancy_id="1", company="X", name="Y", area="Москва")
    b = make_vacancy(vacancy_id="2", company="X", name="Y", area="Казань")
    c = make_vacancy(vacancy_id="3", company="X", name="Y", area="Самара")

    result, absorbed = dedupe([("Запрос", [a, b, c])])

    assert result[0][1][0].area == "Москва, Казань, Самара"
    assert len(absorbed) == 2


def test_merged_area_is_capped_at_three_cities_plus_a_counter():
    cities = ["Москва", "Казань", "Самара", "Курск", "Тверь"]
    vacancies = [
        make_vacancy(vacancy_id=str(i), company="X", name="Y", area=city)
        for i, city in enumerate(cities)
    ]

    result, absorbed = dedupe([("Запрос", vacancies)])

    assert result[0][1][0].area == "Москва, Казань, Самара и ещё 2 города"
    assert len(absorbed) == 4


def test_a_single_repeated_city_does_not_get_a_counter():
    a = make_vacancy(vacancy_id="1", company="X", name="Y", area="Москва")
    b = make_vacancy(vacancy_id="2", company="X", name="Y", area="Москва")

    result, absorbed = dedupe([("Запрос", [a, b])])

    assert result[0][1][0].area == "Москва"
    assert absorbed == [b]


def test_vacancies_with_placeholder_identity_are_never_content_merged():
    first = make_vacancy(
        vacancy_id="1", company=UNKNOWN_COMPANY, name="Менеджер по продажам", area="Москва",
    )
    second = make_vacancy(
        vacancy_id="2", company=UNKNOWN_COMPANY, name="Менеджер по продажам", area="Казань",
    )

    result, absorbed = dedupe([("Запрос", [first, second])])

    assert result == [("Запрос", [first, second])]
    assert absorbed == []


def test_vacancies_published_before_the_cutoff_are_dropped():
    old = make_vacancy(vacancy_id="1", published_at=at(1))
    edge = make_vacancy(vacancy_id="2", published_at=at(2))
    new = make_vacancy(vacancy_id="3", published_at=at(3))

    assert drop_published_before([old, edge, new], at(2)) == [edge, new]


def test_drop_published_before_keeps_unknown_date():
    unknown = make_vacancy(vacancy_id="1", published_at=None)

    assert drop_published_before([unknown], at(2)) == [unknown]


def test_sort_by_freshness_puts_unknown_date_last():
    unknown = make_vacancy(vacancy_id="1", published_at=None)
    fresh = make_vacancy(vacancy_id="2", published_at=at(3))

    assert sort_by_freshness([unknown, fresh]) == [fresh, unknown]


def test_sort_by_score_descending_unscored_last():
    from hunter.filters import sort_by_score
    from hunter.models import Score

    vs = [make_vacancy(vacancy_id=str(i)) for i in range(1, 7)]
    scores = {
        "hh:1": Score(5, "w"),
        "hh:2": Score(None, "w"),
        "hh:3": Score(9, "w"),
        # hh:4 нет в оценках
        "hh:5": Score(7, "w"),
        "hh:6": Score(None, "w"),
    }
    ordered = [v.vacancy_id for v in sort_by_score(vs, scores)]
    assert ordered == ["3", "5", "1", "2", "4", "6"]
