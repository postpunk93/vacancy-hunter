import pytest
from datetime import datetime, timedelta, timezone

from hunter.platforms.base import vacancy_card_row
from hunter.corpus.store import Corpus
from hunter.models import FullText
from hunter.store import Store
from tests.factories import make_detail, make_vacancy


def test_new_database_is_empty():
    with Store(":memory:") as store:
        assert store.is_empty() is True


def test_marked_vacancies_are_no_longer_unseen():
    a = make_vacancy(vacancy_id="1")
    b = make_vacancy(vacancy_id="2")

    with Store(":memory:") as store:
        assert store.unseen([a, b]) == [a, b]
        store.mark_seen([a], sent=True)

        assert store.unseen([a, b]) == [b]
        assert store.is_empty() is False


def test_marking_twice_does_not_fail():
    a = make_vacancy(vacancy_id="1")

    with Store(":memory:") as store:
        store.mark_seen([a], sent=True)
        store.mark_seen([a], sent=True)

        assert store.unseen([a]) == []


def test_state_survives_reopening_the_file(tmp_path):
    path = tmp_path / "state.db"
    a = make_vacancy(vacancy_id="1")

    with Store(path) as store:
        store.mark_seen([a], sent=True)

    with Store(path) as store:
        assert store.unseen([a]) == []


def test_old_records_are_purged_but_recent_ones_stay(tmp_path):
    old = make_vacancy(vacancy_id="old")
    fresh = make_vacancy(vacancy_id="fresh")

    with Store(tmp_path / "state.db") as store:
        store.mark_seen([old, fresh], sent=True)
        ancient = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        store._conn.execute(
            "UPDATE seen SET first_seen_at = ? WHERE key = ?", (ancient, old.key)
        )
        store._conn.commit()

        removed = store.purge_older_than(90)

        assert removed == 1
        assert store.unseen([old]) == [old], "запись удалена, вакансия снова новая"
        assert store.unseen([fresh]) == []


def test_meta_round_trips_and_overwrites():
    with Store(":memory:") as store:
        assert store.get_meta("last_sent_at") is None

        store.set_meta("last_sent_at", "2026-09-01T10:00:00+00:00")
        assert store.get_meta("last_sent_at") == "2026-09-01T10:00:00+00:00"

        store.set_meta("last_sent_at", "2026-09-02T10:00:00+00:00")
        assert store.get_meta("last_sent_at") == "2026-09-02T10:00:00+00:00"


def test_store_and_corpus_share_one_database(tmp_path):
    db = tmp_path / "job-hunter.db"
    with Store(db) as store:
        store.corpus.note_seen(vacancy_card_row(make_vacancy(vacancy_id="7")))
    with Corpus.open(db) as corpus:
        assert corpus.exists(7)


def test_text_is_read_from_the_corpus(tmp_path):
    with Store(tmp_path / "db") as store:
        store.corpus.save_detail(make_detail(vacancy_id=7, description="Пишем статьи", key_skills=["SEO"]))
        assert store.get_text("hh:7") == FullText("Пишем статьи", ("SEO",))
        assert store.get_text("zarplata:7") == FullText("Пишем статьи", ("SEO",))
        assert store.get_text("hh:8") is None


def test_vacancy_without_downloaded_text_has_no_text(tmp_path):
    with Store(tmp_path / "db") as store:
        store.corpus.note_seen(vacancy_card_row(make_vacancy(vacancy_id="7")))
        assert store.get_text("hh:7") is None


def test_score_is_keyed_by_criteria_hash(tmp_path):
    from hunter.models import Score

    with Store(tmp_path / "s.db") as store:
        store.save_score("hh:1", "h1", Score(8, "почему", ("ГПХ",)))
        assert store.get_score("hh:1", "h2") is None
        assert store.get_score("hh:1", "h1") == Score(8, "почему", ("ГПХ",))


def test_save_score_refuses_unscored_and_card_scores(tmp_path):
    from hunter.models import Score

    with Store(tmp_path / "s.db") as store:
        with pytest.raises(ValueError):
            store.save_score("hh:1", "h", Score(None, "Claude не ответил"))
        with pytest.raises(ValueError):
            store.save_score("hh:1", "h", Score(6, "по карточке", from_card=True))


def test_purge_cleans_seen_and_scores_but_keeps_corpus_texts(tmp_path):
    from hunter.models import Score

    path = tmp_path / "s.db"
    old = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    with Store(path) as store:
        store.corpus.save_detail(make_detail(vacancy_id=1, description="Текст"))
        store.mark_seen([make_vacancy(vacancy_id="1"), make_vacancy(vacancy_id="2")], sent=True)
        for key in ("hh:1", "hh:2"):
            store.save_score(key, "h", Score(5, "w"))
        store._conn.execute("UPDATE seen SET first_seen_at = ? WHERE key = 'hh:1'", (old,))
        store._conn.execute("UPDATE scores SET scored_at = ? WHERE key = 'hh:1'", (old,))
        store._conn.execute("UPDATE vacancies SET detail_fetched_at = ? WHERE id = 1", (old,))
        store._conn.commit()

        assert store.purge_older_than(90) == 2
        assert store.get_score("hh:1", "h") is None
        assert store.get_score("hh:2", "h") is not None
        assert store.get_text("hh:1") == FullText("Текст", ())


def test_get_text_uses_source_and_external_id(tmp_path):
    """Ключ — площадка и внешний id. Одинаковое число у двух площадок
    не должно отдавать текст чужой вакансии."""
    db = tmp_path / "job-hunter.db"
    with Store(db) as store:
        hh_row = vacancy_card_row(make_vacancy(vacancy_id="7"))
        hh_row["description"] = "текст hh"
        sj_row = vacancy_card_row(make_vacancy(vacancy_id="7", source="superjob",
                                               url="https://russia.superjob.ru/vakansii/x-7.html"))
        sj_row["description"] = "текст superjob"
        store.corpus.note_seen(hh_row)
        store.corpus.note_seen(sj_row)
        store.corpus._conn.execute("UPDATE vacancies SET description = 'текст hh' WHERE source = 'hh'")
        store.corpus._conn.execute(
            "UPDATE vacancies SET description = 'текст superjob' WHERE source = 'superjob'")
        store.corpus._conn.commit()

        assert store.get_text("hh:7").description == "текст hh"
        assert store.get_text("superjob:7").description == "текст superjob"
