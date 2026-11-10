import sqlite3

import pytest

from hunter.platforms.base import vacancy_card_row
from hunter.platforms.hh import card_row, parse_detail
from hunter.corpus.store import _MIGRATIONS, SCHEMA_VERSION, STATUS_LABELS, STATUSES, Corpus, CorpusError
from tests.factories import make_vacancy, search_card, vacancy_page


class FakeClock:
    def __init__(self):
        self.now = "2026-10-01T00:00:00+00:00"

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def corpus(tmp_path, clock):
    with Corpus.open(tmp_path / "v.db", create=True, clock=clock) as opened:
        yield opened


def test_new_database_gets_current_schema(tmp_path):
    Corpus.open(tmp_path / "v.db", create=True).close()

    conn = sqlite3.connect(tmp_path / "v.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 4
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"vacancies", "hits", "verdicts", "seen", "meta", "scores", "applications", "status_history"} <= tables
    assert "texts" not in tables


def test_v3_base_migrates_without_losing_rows(tmp_path):
    path = tmp_path / "db.sqlite"
    conn = sqlite3.connect(path)
    for sql in _MIGRATIONS[:3]:
        conn.executescript(sql)
    conn.execute("PRAGMA user_version = 3")
    conn.execute(
        "INSERT INTO vacancies (id, name, company, url, first_seen_at, last_seen_at) "
        "VALUES (114811554, 'Редактор', 'АСТ', 'https://hh.ru/vacancy/114811554', "
        "'2026-11-01', '2026-11-01')"
    )
    conn.execute(
        "INSERT INTO scores (key, criteria_hash, score, why, flags, scored_at) "
        "VALUES ('hh:114811554', 'h', 7, 'ok', '[]', '2026-11-02')"
    )
    conn.commit()
    conn.close()

    with Corpus.open(path) as corpus:
        row = corpus.vacancy(114811554)
        assert (row["source"], row["external_id"]) == ("hh", "114811554")
        assert corpus.find("hh", "114811554") == 114811554
        assert corpus.query("SELECT vacancy_id, score FROM latest_scores")[1] == [(114811554, 7)]


def _row(source, external_id):
    row = card_row(search_card(1))
    row["source"] = source
    row["external_id"] = external_id
    return row


def test_same_number_from_two_sources_are_two_rows(tmp_path):
    with Corpus.open(tmp_path / "db.sqlite", create=True) as corpus:
        hh = _row(source="hh", external_id="1")
        sj = _row(source="superjob", external_id="1")
        first = corpus.upsert_card(hh)
        second = corpus.upsert_card(sj)
        assert first[0] is True and second[0] is True
        assert first[1] != second[1]
        assert corpus.find("hh", "1") == first[1]
        assert corpus.find("superjob", "1") == second[1]
        assert corpus.upsert_card(sj) == (False, second[1])


def test_resolve_points_to_fetch_for_unknown_pair(corpus):
    with pytest.raises(CorpusError, match="vacancies fetch"):
        corpus.resolve("superjob", "1")


def test_missing_database_is_not_created_implicitly(tmp_path):
    with pytest.raises(CorpusError, match="vacancies init"):
        Corpus.open(tmp_path / "v.db")
    assert not (tmp_path / "v.db").exists()


def test_database_from_newer_code_is_refused(tmp_path):
    conn = sqlite3.connect(tmp_path / "v.db")
    conn.execute("PRAGMA user_version = 99")
    conn.close()

    with pytest.raises(CorpusError, match="новее|более новой"):
        Corpus.open(tmp_path / "v.db")


def test_upsert_reports_new_then_existing_and_refreshes_card(corpus, clock):
    assert corpus.upsert_card(card_row(search_card(1, responsesCount=5)))[0] is True
    clock.now = "2026-10-05T00:00:00+00:00"
    assert corpus.upsert_card(card_row(search_card(1, responsesCount=40)))[0] is False

    row = corpus.vacancy(1)
    assert row["responses_count"] == 40
    assert row["first_seen_at"] == "2026-10-01T00:00:00+00:00"
    assert row["last_seen_at"] == "2026-10-05T00:00:00+00:00"


def test_recollect_keeps_description_and_verdict(corpus):
    corpus.upsert_card(card_row(search_card(1)))
    corpus.save_detail(parse_detail(vacancy_page(1)))
    corpus.set_verdict(1, "yes", "огонь")

    corpus.upsert_card(card_row(search_card(1)))

    assert corpus.vacancy(1)["description"] == "Пишем на **Python**"
    assert corpus.verdict(1)["verdict"] == "yes"


def test_save_detail_creates_missing_row_from_the_page(corpus):
    corpus.save_detail(parse_detail(vacancy_page(7)))

    row = corpus.vacancy(7)
    assert row["name"] == "Вакансия 7"
    assert row["card_json"] is None
    assert row["status"] == "active"
    assert row["key_skills"] == '["Python", "SQL"]'
    assert row["detail_fetched_at"] is not None


def test_save_detail_does_not_overwrite_card_fields(corpus):
    corpus.upsert_card(card_row(search_card(1, name="Из выдачи")))
    corpus.save_detail(parse_detail(vacancy_page(1, name="Со страницы")))

    assert corpus.vacancy(1)["name"] == "Из выдачи"


def test_has_detail_after_fetch_and_after_deletion(corpus):
    corpus.upsert_card(card_row(search_card(1)))
    corpus.upsert_card(card_row(search_card(2)))
    assert not corpus.has_detail(1)

    corpus.save_detail(parse_detail(vacancy_page(1)))
    assert corpus.mark_deleted(2) is True

    assert corpus.has_detail(1)
    assert corpus.has_detail(2)
    assert corpus.vacancy(2)["status"] == "deleted"


def test_mark_deleted_unknown_vacancy(corpus):
    assert corpus.mark_deleted(999) is False
    assert not corpus.has_detail(999)


def test_add_hit_is_idempotent(corpus):
    corpus.upsert_card(card_row(search_card(1)))
    corpus.add_hit(1, "backend", "python")
    corpus.add_hit(1, "backend", "python")
    corpus.add_hit(1, "data", "dwh")

    assert [(h["role"], h["query"]) for h in corpus.hits(1)] == [("backend", "python"), ("data", "dwh")]


def test_verdict_is_overwritten(corpus):
    corpus.upsert_card(card_row(search_card(1)))
    corpus.set_verdict(1, "maybe", "посмотреть стек")
    corpus.set_verdict(1, "no")

    verdict = corpus.verdict(1)
    assert verdict["verdict"] == "no"
    assert verdict["note"] is None


def test_verdict_for_unknown_vacancy(corpus):
    with pytest.raises(CorpusError, match="vacancies fetch 5"):
        corpus.set_verdict(5, "yes")


def test_readonly_queries_and_refuses_writes(tmp_path):
    with Corpus.open(tmp_path / "v.db", create=True) as corpus:
        corpus.upsert_card(card_row(search_card(1)))

    with Corpus.open_readonly(tmp_path / "v.db") as readonly:
        columns, rows = readonly.query("SELECT id, name FROM vacancies")
        assert columns == ["id", "name"]
        assert rows == [(1, "Вакансия 1")]
        with pytest.raises(sqlite3.DatabaseError):
            readonly.query("DELETE FROM vacancies")

    with Corpus.open(tmp_path / "v.db") as corpus:
        assert corpus.exists(1)


def test_readonly_missing_or_outdated_database(tmp_path):
    with pytest.raises(CorpusError, match="vacancies init"):
        Corpus.open_readonly(tmp_path / "nope.db")

    sqlite3.connect(tmp_path / "old.db").execute("CREATE TABLE x (a)").connection.close()
    with pytest.raises(CorpusError, match="vacancies init"):
        Corpus.open_readonly(tmp_path / "old.db")


@pytest.mark.parametrize("statement", [
    "ATTACH 'side.db' AS side",
    "ATTACH 'file:side.db?mode=rwc' AS side",
    "VACUUM INTO 'side.db'",
])
def test_readonly_cannot_create_files(tmp_path, monkeypatch, statement):
    # mode=ro защищает только саму базу: ATTACH и VACUUM INTO создают
    # файлы где угодно, а гайд обещает Claude, что sql только читает.
    monkeypatch.chdir(tmp_path)
    Corpus.open(tmp_path / "v.db", create=True).close()

    with Corpus.open_readonly(tmp_path / "v.db") as readonly:
        with pytest.raises(sqlite3.DatabaseError):
            readonly.query(statement)

    assert not (tmp_path / "side.db").exists()


def test_readonly_still_allows_analysis_queries(tmp_path):
    with Corpus.open(tmp_path / "v.db", create=True) as corpus:
        corpus.save_detail(parse_detail(vacancy_page(1)))

    with Corpus.open_readonly(tmp_path / "v.db") as readonly:
        _, skills = readonly.query(
            "WITH s AS (SELECT value FROM vacancies, json_each(key_skills)) "
            "SELECT value, count(*) FROM s GROUP BY value ORDER BY value"
        )
        _, columns = readonly.query("PRAGMA table_info(verdicts)")

    assert skills == [("Python", 1), ("SQL", 1)]
    assert [c[1] for c in columns] == ["vacancy_id", "verdict", "note", "updated_at"]


@pytest.mark.parametrize("opener", [
    lambda path: Corpus.open(path),
    lambda path: Corpus.open_readonly(path),
])
def test_file_that_is_not_a_database(tmp_path, opener):
    path = tmp_path / "v.db"
    path.write_bytes(b"definitely not sqlite" * 100)

    with pytest.raises(CorpusError, match="не похож на базу"):
        opener(path)


def test_note_seen_inserts_only_once_and_keeps_existing_columns(corpus, clock):
    corpus.upsert_card(card_row(search_card(7, responsesCount=36)))
    clock.now = "2026-10-05T00:00:00+00:00"

    assert corpus.note_seen(vacancy_card_row(make_vacancy(vacancy_id="7")))[0] is False

    row = corpus.vacancy(7)
    assert row["responses_count"] == 36
    assert row["card_json"] is not None
    assert row["name"] == "Вакансия 7"
    assert row["last_seen_at"] == "2026-10-05T00:00:00+00:00"
    assert row["first_seen_at"] == "2026-10-01T00:00:00+00:00"


def test_note_seen_inserts_a_new_vacancy(corpus):
    assert corpus.note_seen(vacancy_card_row(make_vacancy(vacancy_id="8")))[0] is True
    assert corpus.vacancy(8)["url"] == "https://hh.ru/vacancy/8"


def test_latest_scores_view_takes_newest_score_per_vacancy(corpus):
    corpus.note_seen(card_row(search_card(7)))
    other = card_row(search_card(9))
    other["source"] = "zarplata"
    corpus.note_seen(other)

    insert = "INSERT INTO scores VALUES (?, ?, ?, ?, '[]', ?)"
    corpus._conn.execute(insert, ("hh:7", "old", 4, "раньше", "2026-10-01T00:00:00+00:00"))
    corpus._conn.execute(insert, ("hh:7", "new", 8, "позже", "2026-10-02T00:00:00+00:00"))
    corpus._conn.execute(insert, ("zarplata:9", "old", 6, "другая", "2026-10-01T00:00:00+00:00"))

    rows = corpus._conn.execute(
        "SELECT vacancy_id, score, why FROM latest_scores ORDER BY vacancy_id"
    ).fetchall()

    assert [tuple(r) for r in rows] == [(7, 8, "позже"), (9, 6, "другая")]


def test_unknown_status_is_rejected_by_the_schema(corpus):
    corpus.note_seen(vacancy_card_row(make_vacancy(vacancy_id="7")))
    insert = "INSERT INTO applications (vacancy_id, status, updated_at) VALUES (7, ?, 'now')"

    with pytest.raises(sqlite3.IntegrityError):
        corpus._conn.execute(insert, ("foo",))
    corpus._conn.execute(insert, (STATUSES[0],))


def test_note_can_be_saved_without_a_stage(corpus):
    corpus.note_seen(vacancy_card_row(make_vacancy(vacancy_id="7")))

    corpus.set_status(7, None, "позвонить в пн")

    application = corpus.application(7)
    assert application["status"] is None
    assert application["note"] == "позвонить в пн"
    _, history = corpus.query("SELECT count(*) FROM status_history")
    assert history == [(0,)]


def test_empty_stage_keeps_the_current_one(corpus):
    corpus.note_seen(vacancy_card_row(make_vacancy(vacancy_id="7")))
    corpus.set_status(7, "applied", "отклик")

    corpus.set_status(7, None, "ждём ответа")

    application = corpus.application(7)
    assert application["status"] == "applied"
    assert application["note"] == "ждём ответа"


def test_every_status_has_a_label():
    assert set(STATUS_LABELS) == set(STATUSES)


def test_v1_database_with_data_is_upgraded_to_v2(tmp_path):
    from hunter.store import Store

    path = tmp_path / "v1.db"
    conn = sqlite3.connect(path)
    conn.executescript(_MIGRATIONS[0])
    conn.execute("PRAGMA user_version = 1")
    conn.execute(
        "INSERT INTO vacancies (id, name, company, url, first_seen_at, last_seen_at) "
        "VALUES (7, 'Python', 'Авито', 'https://hh.ru/vacancy/7', 't', 't')"
    )
    conn.execute("INSERT INTO hits VALUES (7, 'backend', 'python', 't')")
    conn.execute("INSERT INTO verdicts VALUES (7, 'yes', 'ок', 't')")
    conn.commit()
    conn.close()

    with Corpus.open(path) as corpus:
        assert corpus.vacancy(7)["name"] == "Python"
        assert [tuple(h) for h in corpus.hits(7)] == [("backend", "python", "t")]
        assert corpus.verdict(7)["verdict"] == "yes"

    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    conn.close()
    assert {"seen", "meta", "scores", "applications", "status_history", "latest_scores"} <= names

    with Store(path) as store:
        assert store.is_empty() is True
        assert store.corpus.exists(7)


def test_v2_applications_survive_the_migration_to_nullable_status(tmp_path):
    path = tmp_path / "v2.db"
    conn = sqlite3.connect(path)
    conn.executescript(_MIGRATIONS[0])
    conn.executescript(_MIGRATIONS[1])
    conn.execute("PRAGMA user_version = 2")
    conn.execute(
        "INSERT INTO vacancies (id, name, company, url, first_seen_at, last_seen_at) "
        "VALUES (7, 'Python', 'Авито', 'https://hh.ru/vacancy/7', 't', 't')"
    )
    conn.execute(
        "INSERT INTO applications (vacancy_id, status, note, test_task, test_due, updated_at) "
        "VALUES (7, 'applied', 'отклик', 'написать сервис', '2026-11-20', 't')"
    )
    conn.commit()
    conn.close()

    with Corpus.open(path) as corpus:
        application = corpus.application(7)
        assert (
            application["status"], application["note"],
            application["test_task"], application["test_due"],
        ) == ("applied", "отклик", "написать сервис", "2026-11-20")

    # Повторное открытие уже мигрированной базы ничего не ломает.
    with Corpus.open(path) as corpus:
        assert corpus.application(7)["status"] == "applied"

    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
    conn.close()
