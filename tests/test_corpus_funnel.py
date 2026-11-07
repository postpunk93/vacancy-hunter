from datetime import date

import pytest

from hunter.corpus.extract import vacancy_card_row
from hunter.corpus.funnel import funnel_rows, reminders
from hunter.corpus.store import Corpus, CorpusError
from tests.factories import make_vacancy

TODAY = date(2026, 10, 17)


class FakeClock:
    def __init__(self):
        self.now = "2026-10-01T00:00:00+00:00"

    def __call__(self):
        return self.now

    def days_ago(self, days: int) -> None:
        self.now = f"2026-10-{17 - days:02d}T12:00:00+00:00"


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def corpus(tmp_path, clock):
    with Corpus.open(tmp_path / "v.db", create=True, clock=clock) as opened:
        opened.note_seen(vacancy_card_row(make_vacancy(vacancy_id="7", name="Python", company="Авито")))
        yield opened


def test_status_change_is_recorded_in_history(corpus, clock):
    corpus.set_status(7, "selected")
    clock.now = "2026-10-02T00:00:00+00:00"
    corpus.set_status(7, "drafted")

    assert corpus.application(7)["status"] == "drafted"
    assert corpus.application(7)["updated_at"] == "2026-10-02T00:00:00+00:00"
    _, rows = corpus.query("SELECT status, at FROM status_history WHERE vacancy_id = 7 ORDER BY id")
    assert rows == [("selected", "2026-10-01T00:00:00+00:00"), ("drafted", "2026-10-02T00:00:00+00:00")]


def test_note_survives_a_later_status_without_note(corpus):
    corpus.set_status(7, "selected", "позвал знакомый", test_task="написать сервис", test_due=date(2026, 10, 20))
    corpus.set_status(7, "test")

    app = corpus.application(7)
    assert (app["status"], app["note"], app["test_task"], app["test_due"]) == (
        "test", "позвал знакомый", "написать сервис", "2026-10-20",
    )


def test_repeating_the_same_status_does_not_restart_the_clock(corpus, clock):
    corpus.set_status(7, "applied")
    clock.now = "2026-10-12T00:00:00+00:00"
    corpus.set_status(7, "applied", "написал HR повторно")

    assert corpus.application(7)["note"] == "написал HR повторно"
    _, rows = corpus.query("SELECT count(*) FROM status_history")
    assert rows == [(1,)]


def test_status_for_unknown_vacancy_is_an_error(corpus):
    with pytest.raises(CorpusError, match="vacancies fetch 99"):
        corpus.set_status(99, "applied")


def test_unknown_status_is_an_error(corpus):
    with pytest.raises(CorpusError, match="selected.*closed"):
        corpus.set_status(7, "foo")
    assert corpus.application(7) is None


def test_application_of_vacancy_without_one_is_none(corpus):
    assert corpus.application(7) is None


def test_note_without_a_stage_is_not_in_the_funnel_or_reminders(corpus, clock):
    clock.days_ago(14)
    corpus.set_status(7, None, "позвонить в пн")

    assert corpus.application(7)["note"] == "позвонить в пн"
    assert funnel_rows(corpus) == []
    assert reminders(corpus, TODAY) == []


def _in_test(corpus, due: date) -> None:
    corpus.set_status(7, "test", test_task="сервис", test_due=due)


def test_test_due_tomorrow_is_reminded(corpus):
    _in_test(corpus, date(2026, 10, 18))

    (line,) = reminders(corpus, TODAY)
    assert "Тестовое" in line and "2026-10-18" in line
    assert line.endswith("· Python — Авито (id 7)")


def test_test_due_in_five_days_is_not_reminded(corpus):
    _in_test(corpus, date(2026, 10, 22))

    assert reminders(corpus, TODAY) == []


def test_test_due_in_two_days_is_the_last_reminded_day(corpus):
    _in_test(corpus, date(2026, 10, 19))
    assert len(reminders(corpus, TODAY)) == 1

    _in_test(corpus, date(2026, 10, 20))
    assert reminders(corpus, TODAY) == []


def test_overdue_test_is_reminded(corpus):
    _in_test(corpus, date(2026, 10, 14))

    (line,) = reminders(corpus, TODAY)
    assert "просрочено" in line and "3 дня" in line


def test_test_without_due_date_is_not_reminded(corpus):
    corpus.set_status(7, "test")

    assert reminders(corpus, TODAY) == []


def test_due_date_of_a_finished_test_is_not_reminded(corpus, clock):
    _in_test(corpus, date(2026, 10, 14))
    clock.days_ago(1)
    corpus.set_status(7, "applied")

    assert reminders(corpus, TODAY) == []


def test_applied_eight_days_ago_is_reminded(corpus, clock):
    clock.days_ago(8)
    corpus.set_status(7, "applied")

    (line,) = reminders(corpus, TODAY)
    assert "8 дней" in line and line.endswith("(id 7)")


def test_applied_six_days_ago_is_not_reminded(corpus, clock):
    clock.days_ago(6)
    corpus.set_status(7, "applied")

    assert reminders(corpus, TODAY) == []


def test_drafted_four_days_ago_is_reminded(corpus, clock):
    clock.days_ago(4)
    corpus.set_status(7, "drafted")

    (line,) = reminders(corpus, TODAY)
    assert "Черновик" in line and "4 дня" in line


def test_drafted_three_days_ago_is_not_reminded(corpus, clock):
    clock.days_ago(3)
    corpus.set_status(7, "drafted")

    assert reminders(corpus, TODAY) == []


@pytest.mark.parametrize("status", ["selected", "invited", "rejected", "skipped", "closed"])
def test_other_statuses_are_never_reminded(corpus, clock, status):
    clock.days_ago(14)
    corpus.set_status(7, status)

    assert reminders(corpus, TODAY) == []
