import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import pytest

from hunter.corpus.extract import vacancy_card_row
from hunter.corpus.store import Corpus
from hunter.corpus.ui import Filters, parse_filters, select_rows
from hunter.export import export_rows
from tests.factories import make_vacancy


@pytest.fixture
def db(tmp_path):
    return tmp_path / "v.db"


@pytest.fixture
def corpus(db):
    with Corpus.open(db, create=True) as opened:
        yield opened


def _add(corpus, vacancy_id, *, name=None, company="Компания", role="backend"):
    corpus.note_seen(vacancy_card_row(make_vacancy(
        vacancy_id=str(vacancy_id), name=name or f"Вакансия {vacancy_id}", company=company,
    )))
    corpus.add_hit(vacancy_id, role, "python")


def _score(corpus, vacancy_id, score, why="подходит", flags=()):
    corpus._conn.execute(
        "INSERT INTO scores (key, criteria_hash, score, why, flags, scored_at) "
        "VALUES (?, 'h', ?, ?, ?, '2026-10-01T00:00:00+00:00')",
        (f"hh:{vacancy_id}", score, why, json.dumps(flags, ensure_ascii=False)),
    )
    corpus._conn.commit()


def _apply(corpus, vacancy_id, status, note=None):
    corpus._conn.execute(
        "INSERT INTO applications (vacancy_id, status, note, updated_at) "
        "VALUES (?, ?, ?, '2026-10-02T00:00:00+00:00')",
        (vacancy_id, status, note),
    )
    corpus._conn.commit()


def _age(corpus, vacancy_id, days):
    first_seen = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    corpus._conn.execute(
        "UPDATE vacancies SET first_seen_at = ? WHERE id = ?", (first_seen, vacancy_id)
    )
    corpus._conn.commit()


def test_parse_defaults_and_values():
    assert parse_filters({}) == Filters()
    assert parse_filters({
        "days": ["7"], "min_score": ["4"], "status": ["test"],
        "q": [" python "], "sort": ["score"], "desc": ["0"],
    }) == Filters(days=7, min_score=4, status="test", q="python", sort="score", desc=False)


def test_parse_ignores_unknown_values():
    assert parse_filters({"days": ["5"], "status": ["nope"], "sort": ["x"]}) == Filters()


def test_query_round_trips():
    filters = Filters(days=7, min_score=4, status="test", q="питон", sort="score", desc=False)
    assert parse_filters(parse_qs(filters.query())) == filters


def test_search_ignores_case_for_cyrillic(corpus):
    _add(corpus, 1, name="Яндекс Практикум")
    _add(corpus, 2, name="VK")
    rows = export_rows(corpus, None)
    assert [r["id"] for r in select_rows(rows, Filters(q="яндекс"))] == [1]


def test_status_new_and_exact(corpus):
    _add(corpus, 1)
    _add(corpus, 2)
    _apply(corpus, 2, "applied", "ждём")
    rows = export_rows(corpus, None)
    assert [r["id"] for r in select_rows(rows, Filters(status="new"))] == [1]
    assert [r["id"] for r in select_rows(rows, Filters(status="applied"))] == [2]


def test_min_score_excludes_unscored(corpus):
    _add(corpus, 1)
    _add(corpus, 2)
    _score(corpus, 1, 8)
    rows = export_rows(corpus, None)
    assert [r["id"] for r in select_rows(rows, Filters(min_score=5))] == [1]


def test_days_filter_uses_first_seen(corpus):
    _add(corpus, 1)
    _add(corpus, 2)
    _age(corpus, 2, 10)
    rows = export_rows(corpus, None)
    assert [r["id"] for r in select_rows(rows, Filters(days=7))] == [1]


def test_score_sort_puts_unscored_last(corpus):
    _add(corpus, 1)
    _add(corpus, 2)
    _add(corpus, 3)
    _score(corpus, 1, 2)
    _score(corpus, 2, 8)
    rows = export_rows(corpus, None)
    assert [r["score"] for r in select_rows(rows, Filters(sort="score"))] == [8, 2, None]


def test_found_sort_newest_first(corpus):
    _add(corpus, 1)
    _add(corpus, 2)
    _age(corpus, 1, 3)
    rows = export_rows(corpus, None)
    assert [r["id"] for r in select_rows(rows, Filters())] == [2, 1]
