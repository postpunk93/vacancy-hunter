from datetime import datetime, timezone

from hunter.platforms.base import Card


def _card(**over):
    base = dict(
        source="hh", external_id="42", name="Редактор", company="АСТ",
        url="https://hh.ru/vacancy/42", area="Москва",
        published_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        salary_from=100000, salary_to=None, currency="RUR", gross=False,
        salary_mode="MONTH", work_formats=("REMOTE",), experience="between1And3",
        is_ad=False, row={"name": "Редактор"},
    )
    base.update(over)
    return Card(**base)


def test_card_key_has_source_prefix():
    assert _card().key == "hh:42"


def test_card_builds_digest_vacancy():
    vacancy = _card().vacancy
    assert (vacancy.source, vacancy.vacancy_id, vacancy.name) == ("hh", "42", "Редактор")
    assert vacancy.work_formats == ("REMOTE",)


def test_card_vacancy_allows_unknown_date():
    assert _card(published_at=None).vacancy.published_at is None
