import json
from datetime import datetime, timedelta, timezone

import pytest
from openpyxl import load_workbook

from hunter.corpus.extract import vacancy_card_row
from hunter.corpus.store import STATUS_LABELS, Corpus
from hunter.export import export_rows, write_xlsx
from tests.factories import make_vacancy

HEADER = [
    "Ссылка", "Название", "Компания", "Оценка", "Почему", "Флаги",
    "Роль", "Статус", "Заметка", "Найдена",
]


@pytest.fixture
def corpus(tmp_path):
    with Corpus.open(tmp_path / "v.db", create=True) as opened:
        yield opened


def _add(corpus, vacancy_id, *, role="backend", name=None):
    corpus.note_seen(vacancy_card_row(make_vacancy(
        vacancy_id=str(vacancy_id), name=name or f"Вакансия {vacancy_id}",
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


def _sheet(path):
    return load_workbook(path).active


def test_export_has_score_status_and_role(corpus, tmp_path):
    _add(corpus, 7, role="Python backend", name="Python dev")
    _score(corpus, 7, 8, "стек совпал", ("ГПХ", "часовой пояс"))
    _apply(corpus, 7, "drafted", "письмо в me/applications")
    path = tmp_path / "out.xlsx"

    write_xlsx(export_rows(corpus, days=None), path)

    sheet = _sheet(path)
    assert [c.value for c in sheet[1]] == HEADER
    row = [c.value for c in sheet[2]]
    assert row[:9] == [
        "https://hh.ru/vacancy/7", "Python dev", "Авито", 8, "стек совпал",
        "ГПХ, часовой пояс", "Python backend", "черновик готов",
        "письмо в me/applications",
    ]
    assert row[9]  # дата, когда проход нашёл вакансию


def test_vacancy_without_application_is_new(corpus, tmp_path):
    _add(corpus, 7)
    path = tmp_path / "out.xlsx"

    write_xlsx(export_rows(corpus, days=None), path)

    sheet = _sheet(path)
    assert sheet["H2"].value == "новая"
    assert [sheet[c].value for c in ("D2", "E2", "F2", "I2")] == [None, None, None, None]


def test_days_limits_rows(corpus):
    _add(corpus, 1)
    _add(corpus, 2)
    _add(corpus, 3)
    _age(corpus, 1, days=10)
    _age(corpus, 2, days=2)
    _age(corpus, 3, days=1)

    assert [r["id"] for r in export_rows(corpus, days=3)] == [3, 2]
    assert [r["id"] for r in export_rows(corpus, days=None)] == [3, 2, 1]


def test_first_role_wins_and_rows_are_not_duplicated(corpus):
    _add(corpus, 7, role="backend")
    corpus.add_hit(7, "data", "spark")

    rows = export_rows(corpus, days=None)

    assert [(r["id"], r["role"]) for r in rows] == [(7, "backend")]


def test_empty_corpus_gives_no_rows(corpus):
    assert export_rows(corpus, days=None) == []


def test_link_is_clickable(corpus, tmp_path):
    _add(corpus, 7)
    path = tmp_path / "out.xlsx"

    write_xlsx(export_rows(corpus, days=None), path)

    assert _sheet(path)["A2"].hyperlink.target == "https://hh.ru/vacancy/7"


def test_status_column_offers_a_dropdown_of_funnel_stages(corpus, tmp_path):
    _add(corpus, 7)
    path = tmp_path / "out.xlsx"

    write_xlsx(export_rows(corpus, days=None), path)

    (validation,) = _sheet(path).data_validations.dataValidation
    assert validation.type == "list"
    assert validation.formula1 == '"' + ",".join(("новая", *STATUS_LABELS.values())) + '"'
    assert "H2" in validation.sqref


def test_header_is_frozen_and_filterable(corpus, tmp_path):
    _add(corpus, 1)
    _add(corpus, 2)
    path = tmp_path / "out.xlsx"

    write_xlsx(export_rows(corpus, days=None), path)

    sheet = _sheet(path)
    assert sheet.freeze_panes == "A2"
    assert sheet.auto_filter.ref == "A1:J3"
