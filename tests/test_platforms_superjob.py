import json
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


def test_detail_empty_state_is_a_parse_error():
    with pytest.raises(ParseError, match="не вакансия"):
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


def _vacancy_state(*, statuses=None, detail=None, route=True):
    """Синтетическое состояние страницы вакансии: живая архивная страница
    в репозиторий не годится — в ней имя частного работодателя."""
    vid = "900001"
    state = {
        "pageInfo": {"pageType": "vacancy-page"},
        "entities": {
            "vacancy": {vid: {"id": vid, "relationships": {
                "companyInfo": {"data": {"id": "c1"}},
                "town": {"data": {"id": "t1"}},
                "detailInfo": {"data": {"id": vid}},
                "requiredExperience": {"data": {"id": "e1"}},
            }}},
            "vacancyMainInfo": {vid: {"id": vid, "relationships": {
                "salary": {"data": {"id": "s1"}},
            }, "attributes": {
                "profession": "Редактор",
                "publishedAt": "2026-11-01T10:00:00+03:00",
            }}},
            "vacancyCompanyInfo": {"c1": {"id": "c1", "attributes": {"name": "ООО Ромашка"}}},
            "town": {"t1": {"id": "t1", "attributes": {"name": "Москва"}}},
            "vacancyDetailInfo": {vid: {"id": vid, "relationships": {
                "workType": {"data": {"id": "w1"}},
                "professionalSkills": {"data": [{"id": "p1"}, {"id": "p2"}]},
            }, "attributes": {
                "fullText": "<p>Полный текст</p>",
                "fullTextPlain": "Краткий текст",
                "isRemoteWork": True,
                **(detail or {}),
            }}},
            "professionalSkill": {
                "p1": {"id": "p1", "attributes": {"name": "Редактура"}},
                "p2": {"id": "p2", "attributes": {"name": "Python"}},
            },
            "vacancyStatusesInfo": {vid: {"id": vid, "attributes": statuses or {
                "isArchive": False, "isClosed": False,
            }}},
            "vacancyWorkTypeDictionary": {"w1": {"id": "w1", "attributes": {"key": "part-time"}}},
            "vacancyExperienceDictionary": {"e1": {"id": "e1", "attributes": {"value": 2}}},
            "vacancySalary": {"s1": {"id": "s1", "relationships": {
                "currency": {"data": {"id": "cur1"}},
                "salaryPeriod": {"data": {"id": "per1"}},
                "salaryType": {"data": {"id": "st1"}},
            }, "attributes": {"minSalary": 50000, "maxSalary": 70000}}},
            "currencyDictionary": {"cur1": {"id": "cur1", "attributes": {"key": "rub"}}},
            "vacancyPaymentPeriodDictionary": {"per1": {"id": "per1", "attributes": {"key": "month"}}},
            "vacancySalaryTypeDictionary": {"st1": {"id": "st1", "attributes": {"key": "gross"}}},
        },
    }
    if route:
        state["pageInfo"]["routeParams"] = {"vacancyId": vid}
    return state


def _posting(**over):
    posting = {
        "@type": "JobPosting",
        "url": "https://russia.superjob.ru/vakansii/x-900001.html",
        "title": "Редактор",
        "description": "<p>Текст вакансии</p>",
        "datePosted": "2026-11-01",
        "employmentType": "FULL_TIME",
        "baseSalary": {"currency": "RUB",
                       "value": {"minValue": 50000, "maxValue": 0, "unitText": "MONTH"}},
        "jobLocation": {"address": {"addressLocality": "Москва"}},
        "hiringOrganization": {"name": "ООО Ромашка"},
    }
    posting.update(over)
    return posting


def _detail_html(state=None, posting=None):
    state_script = (f"<script>window.APP_STATE={json.dumps(state)}</script>"
                    if state is not None else "")
    posting_script = (f'<script type="application/ld+json">{json.dumps(posting)}</script>'
                      if posting is not None else "")
    return f"<html>{state_script}{posting_script}</html>"


@pytest.mark.parametrize("employment, expected", [
    ("FULL_TIME", "FULL"),
    (["FULL_TIME", "PART_TIME", "TEMPORARY"], "FULL"),
    (["INTERN", "FULL_TIME"], "PROBATION"),
    (["VOLUNTEER"], "VOLUNTEER"),
    ("VOLUNTEER", "VOLUNTEER"),
    (None, None),
])
def test_detail_employment_type_may_be_a_list(employment, expected):
    html = _detail_html(_vacancy_state(), _posting(employmentType=employment))

    assert get("superjob").parse_detail(html).row["employment_form"] == expected


def test_archived_detail_without_jobposting_comes_from_state():
    state = _vacancy_state(statuses={"isArchive": True, "isClosed": False})

    detail = get("superjob").parse_detail(_detail_html(state))

    assert detail.source == "superjob" and detail.external_id == "900001"
    assert detail.status == "archived"
    assert detail.row["name"] == "Редактор"
    assert detail.row["company"] == "ООО Ромашка"
    assert detail.row["published_at"] == "2026-11-01T10:00:00+03:00"
    assert detail.row["area"] == "Москва"
    assert detail.row["salary_from"] == 50000
    assert detail.row["salary_to"] == 70000
    assert detail.row["currency"] == "RUR"
    assert detail.row["employment_form"] == "PART"
    assert json.loads(detail.row["work_formats"]) == ["REMOTE"]
    assert detail.key_skills == ["Редактура", "Python"]
    assert detail.description.strip() == "Полный текст"


def test_closed_detail_is_archived_too():
    state = _vacancy_state(statuses={"isArchive": False, "isClosed": True})

    assert get("superjob").parse_detail(_detail_html(state)).status == "archived"


def test_detail_falls_back_to_full_text_plain():
    detail = get("superjob").parse_detail(
        _detail_html(_vacancy_state(detail={"fullText": ""}))
    )

    assert detail.description.strip() == "Краткий текст"


def test_detail_falls_back_to_duties_and_requirements():
    state = _vacancy_state(detail={
        "fullText": "", "fullTextPlain": "",
        "duties": "Писать тексты\n• Следить за стилем",
        "requirements": "Знать язык",
    })

    detail = get("superjob").parse_detail(_detail_html(state))

    assert detail.description == "Писать тексты\n• Следить за стилем\n\nЗнать язык"


def test_detail_without_any_text_is_a_parse_error():
    """Пустой текст записался бы как скачанная деталь и больше не обновился."""
    state = _vacancy_state(detail={
        "fullText": "", "fullTextPlain": "", "duties": "", "requirements": "",
    })

    with pytest.raises(ParseError, match="текста"):
        get("superjob").parse_detail(_detail_html(state))


def test_detail_state_without_status_is_a_parse_error():
    """Молчаливое «жива» перезаписало бы archived на active у всех строк."""
    state = _vacancy_state()
    del state["entities"]["vacancyStatusesInfo"]

    with pytest.raises(ParseError, match="статус"):
        get("superjob").parse_detail(_detail_html(state))


def test_detail_state_without_route_vacancy_id_is_a_parse_error():
    state = _vacancy_state(route=False)

    with pytest.raises(ParseError, match="vacancyId"):
        get("superjob").parse_detail(_detail_html(state))


def test_detail_state_of_another_page_type_is_a_parse_error():
    state = _vacancy_state()
    state["pageInfo"]["pageType"] = "search-vacancy"

    with pytest.raises(ParseError, match="не вакансия"):
        get("superjob").parse_detail(_detail_html(state))


def test_detail_state_without_vacancy_entities_is_a_parse_error():
    state = _vacancy_state()
    del state["entities"]["vacancyMainInfo"]

    with pytest.raises(ParseError, match="сущности"):
        get("superjob").parse_detail(_detail_html(state))


def test_detail_with_jobposting_takes_status_from_state():
    state = _vacancy_state(statuses={"isArchive": True, "isClosed": False})

    assert get("superjob").parse_detail(_detail_html(state, _posting())).status == "archived"


def test_detail_with_jobposting_survives_without_state():
    """Страница без APP_STATE не должна выглядеть капчей: это оборвало бы докачку."""
    detail = get("superjob").parse_detail(_detail_html(posting=_posting()))

    assert detail.status == "active"
    assert detail.row["name"] == "Редактор"


def test_card_employment_form_is_hh_code():
    cards = {card.external_id: card.row["employment_form"]
             for card in _cards("superjob_search_sales.html")}

    assert cards["52295791"] == "FULL"
    assert cards["52303218"] == "PART", "half-day — тоже неполная занятость"


def test_detail_rows_have_the_same_keys_on_both_paths():
    platform = get("superjob")
    from_posting = platform.parse_detail(_detail_html(_vacancy_state(), _posting()))
    from_state = platform.parse_detail(_detail_html(_vacancy_state()))

    assert set(from_posting.row) == set(from_state.row)


def test_archived_detail_is_written_to_the_base(tmp_path):
    from hunter.corpus.collect import fetch_details
    from hunter.corpus.store import Corpus

    html = _detail_html(_vacancy_state(statuses={"isArchive": True}))

    with Corpus.open(tmp_path / "db.sqlite", create=True) as corpus:
        report = fetch_details(corpus, lambda url: html, [("superjob", "900001")])
        assert report.fetched == ["superjob:900001"]

        row = corpus.vacancy(corpus.find("superjob", "900001"))
        assert row["status"] == "archived"
        assert row["description"].strip() == "Полный текст"
        assert json.loads(row["key_skills"]) == ["Редактура", "Python"]

        again = fetch_details(corpus, lambda url: html, [("superjob", "900001")])
        assert again.skipped == ["superjob:900001"], "скачанный текст не перекачиваем"
