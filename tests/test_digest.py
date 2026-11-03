from datetime import datetime, timezone

from hunter.digest import (
    MAX_MESSAGE_CHARS,
    escape,
    format_salary,
    render,
)
from tests.factories import make_vacancy

NOW = datetime(2026, 9, 1, 14, 30, tzinfo=timezone.utc)


def test_salary_range_is_formatted_with_spaces_and_currency():
    v = make_vacancy(salary_from=350000, salary_to=450000, currency="RUR", gross=False)
    assert format_salary(v) == "350 000–450 000 ₽ на руки"


def test_open_ended_ranges_get_a_preposition():
    lower = make_vacancy(salary_from=250000, salary_to=None, gross=True)
    upper = make_vacancy(salary_from=None, salary_to=400000, gross=None)

    assert format_salary(lower) == "от 250 000 ₽ до вычета"
    assert format_salary(upper) == "до 400 000 ₽"


def test_hourly_rate_is_not_shown_as_a_monthly_salary():
    v = make_vacancy(salary_from=1500, salary_to=None, salary_mode="HOUR", gross=False)
    assert "в час" in format_salary(v)


def test_foreign_currency_keeps_its_symbol():
    v = make_vacancy(salary_from=5000, salary_to=None, currency="USD", gross=None)
    assert format_salary(v) == "от 5 000 $"


def test_missing_salary_says_so():
    assert format_salary(make_vacancy(salary_from=None, salary_to=None)) == "з/п не указана"


def test_html_special_characters_are_escaped():
    assert escape("R&D <script>") == "R&amp;D &lt;script&gt;"


def test_digest_has_a_header_groups_and_links():
    v = make_vacancy(name="Senior Python Developer", company="Авито", area="Москва")

    messages = render([("Python backend", [v])], NOW)

    assert len(messages) == 1
    text = messages[0]
    assert "1 новая вакансия" in text
    assert "1 сен, 14:30" in text
    assert "<b>Python backend</b>" in text
    assert f'<a href="{v.url}">Senior Python Developer</a>' in text
    assert "Авито" in text
    assert "350 000–450 000 ₽ на руки" in text
    assert "Москва, удалённо" in text


def test_plural_forms_follow_russian_grammar():
    def header(count):
        vs = [make_vacancy(vacancy_id=str(i)) for i in range(count)]
        return render([("q", vs)], NOW)[0].splitlines()[0]

    assert "1 новая вакансия" in header(1)
    assert "3 новые вакансии" in header(3)
    assert "12 новых вакансий" in header(12)


def test_empty_input_produces_no_messages():
    assert render([], NOW) == []
    assert render([("q", [])], NOW) == []


def test_warnings_are_sent_even_without_vacancies():
    messages = render([], NOW, warnings=("запрос «Python» не выполнен",))

    assert len(messages) == 1
    assert "⚠️" in messages[0]
    assert "не выполнен" in messages[0]






def test_long_digest_is_split_into_messages_within_the_limit():
    vs = [
        make_vacancy(vacancy_id=str(i), name="Очень длинное название вакансии " * 6)
        for i in range(30)
    ]

    messages = render([("q", vs)], NOW)

    assert len(messages) > 1
    assert all(len(m) <= MAX_MESSAGE_CHARS for m in messages)


def test_groups_keep_the_order_they_were_given_in():
    a = make_vacancy(vacancy_id="1", name="Первая")
    b = make_vacancy(vacancy_id="2", name="Вторая")

    text = render([("Группа Б", [b]), ("Группа А", [a])], NOW)[0]

    assert text.index("Группа Б") < text.index("Группа А")


def test_experience_label_is_shown_for_known_codes():
    v = make_vacancy(experience="between1And3")
    text = render([("q", [v])], NOW)[0]
    assert "1–3 года" in text


def test_unknown_or_missing_experience_is_omitted_silently():
    known = make_vacancy(vacancy_id="1", experience="moreThan6")
    unknown = make_vacancy(vacancy_id="2", experience="weirdCode")
    missing = make_vacancy(vacancy_id="3", experience=None)

    text = render([("q", [known, unknown, missing])], NOW)[0]

    assert "более 6 лет" in text
    assert "weirdCode" not in text


def test_without_scores_lines_have_no_prefix():
    v = make_vacancy()
    (text,) = render([("q", [v])], NOW)
    assert f'• <a href="{v.url}">' in text


def test_score_prefixes_each_line():
    from hunter.models import Score

    vs = [make_vacancy(vacancy_id=str(i)) for i in (1, 2, 3)]
    scores = {
        "hh:1": Score(8, "w"),
        "hh:2": Score(None, "w"),
        "hh:3": Score(6, "w", from_card=True),
    }
    (text,) = render([("q", vs)], NOW, scores=scores)

    assert "• 8 · <a " in text
    assert "• ? · <a " in text
    assert "• 6* · <a " in text


def test_header_counts_vacancies_below_threshold():
    (text,) = render([("q", [make_vacancy()])], NOW, scores={}, below=4)
    assert "ещё 4 ниже 5 — в базе, выгрузка: vacancies export" in text


def test_only_header_when_everything_is_below():
    messages = render([], NOW, scores={}, below=3)
    assert len(messages) == 1
    assert "3" in messages[0]
    assert "ниже 5 — в базе, выгрузка: vacancies export" in messages[0]


def test_console_summary_has_scores_links_and_no_html():
    from hunter.digest import render_console
    from hunter.models import Score

    vs = [
        make_vacancy(vacancy_id="1", name="Python <dev>", company="A & B"),
        make_vacancy(vacancy_id="2"),
        make_vacancy(vacancy_id="3", salary_from=None, salary_to=None),
    ]
    scores = {
        "hh:1": Score(8, "w"),
        "hh:2": Score(None, "w"),
        "hh:3": Score(6, "w", from_card=True),
    }
    text = render_console(
        [("q", vs)], scores=scores, below=2, threshold=5, warnings=("hh капризничает",)
    )

    assert "3 новые вакансии" in text and "ещё 2 ниже порога 5" in text
    assert "8 · Python <dev> — A & B · 350 000–450 000 ₽ на руки · https://hh.ru/vacancy/1 — w" in text
    assert "? · " in text and "6* · " in text and "з/п не указана" in text
    assert "<a " not in text and "&amp;" not in text and "<b>" not in text
    assert "⚠️ hh капризничает" in text


def test_console_summary_adds_why_only_for_scored_vacancies():
    from hunter.digest import render_console
    from hunter.models import Score

    vs = [make_vacancy(vacancy_id="1"), make_vacancy(vacancy_id="2")]
    scores = {"hh:1": Score(8, "стек совпадает, удалёнка"), "hh:2": Score(None, "оценка упала")}
    lines = render_console(
        [("q", vs)], scores=scores, below=0, threshold=5, warnings=()
    ).splitlines()

    assert lines[-2].endswith("https://hh.ru/vacancy/1 — стек совпадает, удалёнка")
    assert lines[-1].endswith("https://hh.ru/vacancy/2"), "у неоценённой «почему» нечего показывать"


def test_console_summary_without_scores_has_no_prefix():
    from hunter.digest import render_console

    v = make_vacancy()
    text = render_console([("q", [v])], scores=None, below=0, threshold=5, warnings=())

    assert f"Senior Python Developer — Авито · 350 000–450 000 ₽ на руки · {v.url}" in text
    assert "ниже" not in text


def test_console_summary_says_when_nothing_is_new():
    from hunter.digest import render_console

    text = render_console([], scores=None, below=0, threshold=5, warnings=())
    assert "нет" in text

    text = render_console([], scores={}, below=3, threshold=5, warnings=())
    assert "3" in text and "ниже порога 5" in text


def test_console_summary_drops_city_subdomain_from_hh_links():
    from hunter.digest import render_console

    v = make_vacancy(vacancy_id="7", url="https://tomsk.hh.ru/vacancy/7?query=x")
    text = render_console([("q", [v])], scores=None, below=0, threshold=5, warnings=())

    assert text.endswith("https://hh.ru/vacancy/7")


def test_nearby_block_shows_score_why_and_company():
    from hunter.digest import render_console
    from hunter.models import Score

    v = make_vacancy(name="Редактор", company="Рунити")
    text = render_console(
        [],
        scores={v.key: Score(3, "роль та, нет кейсов B2B")},
        below=2,
        threshold=5,
        warnings=(),
        nearby=[v],
        unscored=[],
    )

    assert "Рядом (1)" in text
    assert "3 · Редактор — Рунити" in text
    assert "роль та, нет кейсов B2B" in text


def test_unscored_block_is_separate_from_passed():
    from hunter.digest import render_console
    from hunter.models import Score

    v = make_vacancy()
    text = render_console(
        [],
        scores={v.key: Score(None, "модель не ответила")},
        below=0,
        threshold=5,
        warnings=(),
        nearby=[],
        unscored=[v],
    )

    assert "Без оценки (1)" in text
    assert "? · Senior Python Developer" in text


def test_nearby_is_capped_at_forty_in_telegram():
    from hunter.models import Score

    vacancies = [make_vacancy(vacancy_id=str(i)) for i in range(41)]
    scores = {v.key: Score(4, "рядом") for v in vacancies}

    text = "\n".join(render([], NOW, scores=scores, nearby=vacancies))

    assert "ещё 1 в базе" in text
    assert text.count('<a href=') == 40


def test_exactly_forty_nearby_has_no_remainder_line():
    from hunter.models import Score

    vacancies = [make_vacancy(vacancy_id=str(i)) for i in range(40)]
    scores = {v.key: Score(4, "рядом") for v in vacancies}

    text = "\n".join(render([], NOW, scores=scores, nearby=vacancies))

    assert "ещё" not in text
    assert text.count('<a href=') == 40


def test_unscored_is_capped_at_twenty_in_telegram():
    from hunter.models import Score

    vacancies = [make_vacancy(vacancy_id=str(i)) for i in range(21)]
    scores = {v.key: Score(None, "оценка упала") for v in vacancies}

    text = "\n".join(render([], NOW, scores=scores, unscored=vacancies))

    assert "Без оценки (21)" in text
    assert "ещё 1 в базе" in text
    assert text.count('<a href=') == 20


def test_console_prints_nearby_and_unscored_without_a_cap():
    from hunter.digest import render_console
    from hunter.models import Score

    nearby = [make_vacancy(vacancy_id=str(i)) for i in range(45)]
    scores = {v.key: Score(3, "рядом") for v in nearby}
    text = render_console(
        [], scores=scores, below=0, threshold=5, warnings=(),
        nearby=nearby, unscored=[],
    )

    assert "ещё" not in text
    assert text.count("Рядом (45)") == 1

def test_console_header_mentions_nearby_instead_of_no_vacancies():
    from hunter.digest import render_console
    from hunter.models import Score

    nearby = [make_vacancy(vacancy_id="1")]
    text = render_console(
        [], scores={nearby[0].key: Score(3, "рядом")}, below=0, threshold=5,
        warnings=(), nearby=nearby, unscored=[],
    )

    assert "Новых вакансий нет" not in text
    assert "Новых вакансий выше порога нет" in text
    assert "Рядом (1)" in text
