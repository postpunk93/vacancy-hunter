import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from hunter import paths
from hunter.corpus.cli import main
from hunter.corpus.store import _MIGRATIONS
from hunter.evaluate import Report, evaluate, load_verdicts, rescore, vacancy_from_row
from hunter.models import Score
from hunter.scoring import ScoringError, Criteria
from hunter.store import Store

CRITERIA = Criteria(profile="ПРОФИЛЬ")


def old_database(path, rows):
    """База, созданная только миграцией v1: так выглядит vacancies.db владельца.
    rows: (id, название, вердикт, описание | None)."""
    conn = sqlite3.connect(path)
    conn.executescript(_MIGRATIONS[0])
    conn.execute("PRAGMA user_version = 1")
    for vacancy_id, name, verdict, description in rows:
        conn.execute(
            "INSERT INTO vacancies (id, name, company, url, published_at, salary_from, currency, "
            "gross, experience, work_formats, description, key_skills, first_seen_at, last_seen_at) "
            "VALUES (?, ?, 'Компания', ?, '2026-10-01T10:00:00.000+03:00', 300000, 'RUR', 0, "
            "'between1And3', '[\"REMOTE\"]', ?, '[\"Python\"]', 'x', 'x')",
            (vacancy_id, name, f"https://hh.ru/vacancy/{vacancy_id}", description),
        )
        if verdict:
            conn.execute(
                "INSERT INTO verdicts (vacancy_id, verdict, updated_at) VALUES (?, ?, 'x')",
                (vacancy_id, verdict),
            )
    conn.commit()
    conn.close()


class Ask:
    """Фейковый Claude: оценка вакансии по её id; нет в таблице — не отвечает."""

    def __init__(self, scores=None, error=None):
        self.scores = scores or {}
        self.error = error

    def __call__(self, system, payload):
        if self.error:
            raise ScoringError(self.error)
        items = json.loads(payload)
        return {"items": [
            {"id": item["id"], "score": self.scores[item["id"]], "why": "потому", "flags": []}
            for item in items if item["id"] in self.scores
        ]}


def test_agreement_counts_yes_above_and_no_below_threshold(tmp_path):
    db = tmp_path / "old.db"
    old_database(db, [
        (1, "Автор", "yes", "текст"), (2, "Бэкенд", "no", "текст"), (3, "Продажи", "no", "текст"),
    ])
    ask = Ask({"hh:1": 8, "hh:2": 7, "hh:3": 3})
    report = evaluate(load_verdicts(db), ask=ask, criteria=CRITERIA, threshold=5)
    assert report.total == 3
    assert report.agreement == pytest.approx(2 / 3)
    assert report.mean_by_verdict == {"yes": 8, "no": 5}
    assert report.misses == [(2, "Бэкенд", "no", 7, "потому")]
    assert report.unscored == 0


def test_yes_below_threshold_is_a_miss_too(tmp_path):
    db = tmp_path / "old.db"
    old_database(db, [(1, "Автор", "yes", "текст")])
    report = evaluate(load_verdicts(db), ask=Ask({"hh:1": 4}), criteria=CRITERIA, threshold=5)
    assert report.agreement == 0
    assert report.misses == [(1, "Автор", "yes", 4, "потому")]


def test_threshold_itself_counts_as_pass(tmp_path):
    db = tmp_path / "old.db"
    old_database(db, [(1, "Автор", "yes", "текст"), (2, "Бэкенд", "no", "текст")])
    report = evaluate(load_verdicts(db), ask=Ask({"hh:1": 5, "hh:2": 5}), criteria=CRITERIA, threshold=5)
    assert report.agreement == 0.5
    assert [miss[0] for miss in report.misses] == [2]


def test_maybe_is_excluded_from_agreement(tmp_path):
    db = tmp_path / "old.db"
    old_database(db, [(1, "Автор", "yes", "текст"), (2, "Может", "maybe", "текст")])
    report = evaluate(load_verdicts(db), ask=Ask({"hh:1": 9, "hh:2": 1}), criteria=CRITERIA, threshold=5)
    assert report.total == 2
    assert report.agreement == 1
    assert report.misses == []
    assert report.mean_by_verdict == {"yes": 9, "maybe": 1}


def test_unscored_are_counted_apart_from_agreement(tmp_path):
    db = tmp_path / "old.db"
    old_database(db, [(1, "Автор", "yes", "текст"), (2, "Бэкенд", "no", "текст")])
    report = evaluate(load_verdicts(db), ask=Ask({"hh:1": 9}), criteria=CRITERIA, threshold=5)
    assert report.unscored == 1
    assert report.agreement == 1
    assert report.mean_by_verdict == {"yes": 9}
    assert report.misses == []


def test_model_down_leaves_everything_unscored_with_reason(tmp_path):
    db = tmp_path / "old.db"
    old_database(db, [(1, "Автор", "yes", "текст")])
    report = evaluate(load_verdicts(db), ask=Ask(error="лимит"), criteria=CRITERIA, threshold=5)
    assert report.unscored == 1
    assert report.agreement == 0
    assert any("лимит" in warning for warning in report.warnings)


def test_source_database_is_not_modified(tmp_path):
    db = tmp_path / "old.db"
    old_database(db, [(1, "Автор", "yes", "текст"), (2, "Бэкенд", "no", "текст")])
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    evaluate(load_verdicts(db), ask=Ask({"hh:1": 8, "hh:2": 2}), criteria=CRITERIA, threshold=5)
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert not list(tmp_path.glob("old.db-*"))


def test_old_schema_database_is_readable(tmp_path):
    db = tmp_path / "old.db"
    old_database(db, [(1, "Автор", "yes", "текст")])
    assert sqlite3.connect(db).execute("PRAGMA user_version").fetchone()[0] == 1
    [(vacancy, text, verdict)] = load_verdicts(db)
    assert vacancy.key == "hh:1"
    assert (vacancy.name, vacancy.company, vacancy.salary_from, vacancy.gross) == ("Автор", "Компания", 300000, False)
    assert vacancy.work_formats == ("REMOTE",)
    assert (text.description, text.key_skills, verdict) == ("текст", ("Python",), "yes")
    assert sqlite3.connect(db).execute("PRAGMA user_version").fetchone()[0] == 1


def test_vacancies_without_text_or_verdict_are_skipped(tmp_path):
    db = tmp_path / "old.db"
    old_database(db, [
        (1, "Есть всё", "yes", "текст"),
        (2, "Без текста", "no", None),
        (3, "Без вердикта", None, "текст"),
    ])
    assert [v.vacancy_id for v, _, _ in load_verdicts(db)] == ["1"]


def test_missing_or_foreign_file_is_a_clear_error(tmp_path):
    from hunter.corpus.store import CorpusError

    with pytest.raises(CorpusError):
        load_verdicts(tmp_path / "нет.db")
    junk = tmp_path / "junk.db"
    junk.write_text("не база")
    with pytest.raises(CorpusError):
        load_verdicts(junk)


def test_vacancy_from_row_maps_columns():
    vacancy = vacancy_from_row({
        "id": 77, "name": "Автор", "company": "Хабр", "url": "https://hh.ru/vacancy/77",
        "area": "Москва", "published_at": "2026-10-01T10:00:00.000+03:00",
        "salary_from": None, "salary_to": 200000, "currency": "RUR", "gross": 1,
        "experience": "noExperience", "work_formats": '["REMOTE", "HYBRID"]',
    })
    assert (vacancy.source, vacancy.key, vacancy.is_ad, vacancy.salary_mode) == ("hh", "hh:77", False, None)
    assert vacancy.work_formats == ("REMOTE", "HYBRID")
    assert vacancy.gross is True
    assert vacancy.published_at.utcoffset().total_seconds() == 3 * 3600


def test_vacancy_from_row_tolerates_missing_values():
    vacancy = vacancy_from_row({
        "id": 5, "name": "X", "company": "Y", "url": "u", "area": None, "published_at": None,
        "salary_from": None, "salary_to": None, "currency": None, "gross": None,
        "experience": None, "work_formats": None,
    })
    assert vacancy.published_at == datetime(1970, 1, 1, tzinfo=timezone.utc)
    assert vacancy.work_formats == ()
    assert vacancy.gross is None
    assert vacancy.area == ""


# --- vacancies eval ---


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "ME", tmp_path / "me")
    db = tmp_path / "old.db"
    old_database(db, [
        (1, "Автор", "yes", "текст"), (2, "Бэкенд", "no", "текст"), (3, "Продажи", "no", "текст"),
    ])
    (tmp_path / "profile.md").write_text("профиль", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        "queries:\n  - {name: a, text: b}\n"
        f"scoring:\n  profile: {tmp_path / 'profile.md'}\n"
        "  threshold: 5\n",
        encoding="utf-8",
    )
    return db, config


def test_eval_prints_report(setup, monkeypatch, capsys):
    db, config = setup
    ask = Ask({"hh:1": 8, "hh:2": 7, "hh:3": 3})
    seen = {}

    def fake(system, payload, *, criteria):
        seen["backend"] = criteria.backend
        return ask(system, payload)

    monkeypatch.setattr("hunter.corpus.cli.ask_model", fake)
    assert main(["eval", "--from", str(db), "--config", str(config)]) == 0
    out = capsys.readouterr().out
    assert "Всего: 3" in out
    assert "yes: 8.0" in out and "no: 5.0" in out
    assert "Согласие с порогом 5: 2 из 3" in out
    assert "| 2 | Бэкенд | no | 7 | потому |" in out
    assert seen["backend"] == "opencode"


def test_eval_uses_claude_backend_from_config(setup, monkeypatch, capsys):
    db, config = setup
    config.write_text(
        config.read_text(encoding="utf-8") + "  backend: claude\n  model: sonnet\n",
        encoding="utf-8",
    )
    ask = Ask({"hh:1": 8, "hh:2": 7, "hh:3": 3})
    seen = {}

    def fake(system, payload, *, criteria):
        seen["backend"] = criteria.backend
        seen["model"] = criteria.model
        return ask(system, payload)

    monkeypatch.setattr("hunter.corpus.cli.ask_model", fake)
    assert main(["eval", "--from", str(db), "--config", str(config)]) == 0
    assert seen == {"backend": "claude", "model": "sonnet"}


def test_eval_without_scoring_section_is_a_usage_error(setup, capsys):
    db, config = setup
    config.write_text("queries:\n  - {name: a, text: b}\n", encoding="utf-8")
    assert main(["eval", "--from", str(db), "--config", str(config)]) == 2
    assert "scoring" in capsys.readouterr().err


def test_eval_with_missing_config_is_a_usage_error(setup, tmp_path, capsys):
    db, _ = setup
    assert main(["eval", "--from", str(db), "--config", str(tmp_path / "нет.yaml")]) == 2
    assert "не найден" in capsys.readouterr().err


def test_eval_with_model_down_prints_reason_and_exits_1(setup, monkeypatch, capsys):
    db, config = setup

    def down(system, payload, *, criteria):
        raise ScoringError("лимит подписки")

    monkeypatch.setattr("hunter.corpus.cli.ask_model", down)
    assert main(["eval", "--from", str(db), "--config", str(config)]) == 1
    assert "лимит подписки" in capsys.readouterr().err


def test_eval_without_candidates_exits_1(setup, tmp_path, capsys):
    _, config = setup
    empty = tmp_path / "empty.db"
    old_database(empty, [])
    assert main(["eval", "--from", str(empty), "--config", str(config)]) == 1
    assert "нет вакансий" in capsys.readouterr().err


# --- rescore ---


def cached_database(path, rows):
    """База с корпусом и оценками прежних критериев.
    rows: (id, название, дней назад найдена, описание | None, прежняя оценка | None)."""
    with Store(path) as store:
        now = datetime.now(timezone.utc)
        for vacancy_id, name, days_ago, description, old_score in rows:
            seen = (now - timedelta(days=days_ago)).isoformat()
            store.corpus._conn.execute(
                "INSERT INTO vacancies (id, name, company, url, published_at, salary_from, "
                "currency, gross, experience, work_formats, description, key_skills, "
                "first_seen_at, last_seen_at) VALUES (?, ?, 'Компания', ?, "
                "'2026-10-01T10:00:00.000+03:00', 300000, 'RUR', 0, 'between1And3', "
                "'[\"REMOTE\"]', ?, '[\"Python\"]', ?, ?)",
                (vacancy_id, name, f"https://hh.ru/vacancy/{vacancy_id}", description, seen, seen),
            )
            if old_score is not None:
                store.save_score(f"hh:{vacancy_id}", "старый-хеш", Score(old_score, "раньше"))
        store.corpus._conn.commit()


def test_rescore_reports_crossings_of_the_threshold(tmp_path):
    db = tmp_path / "me.db"
    cached_database(db, [
        (1, "Поднялась", 1, "текст", 4), (2, "Упала", 1, "текст", 7),
        (3, "Без оценки", 1, "текст", None),
    ])
    with Store(db) as store:
        result = rescore(store, ask=Ask({"hh:1": 7, "hh:2": 3, "hh:3": 6}), criteria=CRITERIA, days=7)
    by_id = {vacancy.vacancy_id: (old, new.value, new.why) for vacancy, old, new in result}
    assert by_id == {"1": (4, 7, "потому"), "2": (7, 3, "потому"), "3": (None, 6, "потому")}


def test_rescore_never_touches_the_network(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("rescore пошёл в сеть")

    monkeypatch.setattr("hunter.fetch.get_html", boom)
    monkeypatch.setattr("hunter.fetch.new_client", boom)
    monkeypatch.setattr("hunter.scoring.fetch_texts", boom)
    db = tmp_path / "me.db"
    cached_database(db, [(1, "Автор", 1, "текст", 4)])
    with Store(db) as store:
        result = rescore(store, ask=Ask({"hh:1": 7}), criteria=CRITERIA, days=7)
    assert [new.value for _, _, new in result] == [7]


def test_rescore_skips_vacancies_without_text_and_older_than_days(tmp_path):
    db = tmp_path / "me.db"
    cached_database(db, [
        (1, "С текстом", 1, "текст", None), (2, "Без текста", 1, None, None),
        (3, "Старая", 30, "текст", None),
    ])
    with Store(db) as store:
        result = rescore(store, ask=Ask({"hh:1": 5, "hh:2": 5, "hh:3": 5}), criteria=CRITERIA, days=7)
    assert [vacancy.vacancy_id for vacancy, _, _ in result] == ["1"]


def test_rescore_caches_the_new_scores_under_the_new_criteria(tmp_path):
    db = tmp_path / "me.db"
    cached_database(db, [(1, "Автор", 1, "текст", 4)])
    with Store(db) as store:
        rescore(store, ask=Ask({"hh:1": 7}), criteria=CRITERIA, days=7)
        # Второй раз Claude не нужен: оценка по тем же критериям уже в кеше.
        result = rescore(store, ask=Ask(error="не должен вызываться"), criteria=CRITERIA, days=7)
    [(_, old, new)] = result
    assert (old, new.value) == (7, 7)


def test_rescore_with_model_down_leaves_scores_empty_with_reason(tmp_path):
    db = tmp_path / "me.db"
    cached_database(db, [(1, "Автор", 1, "текст", 4)])
    with Store(db) as store:
        [(_, old, new)] = rescore(store, ask=Ask(error="лимит"), criteria=CRITERIA, days=7)
    assert old == 4
    assert new.value is None and "лимит" in new.why


# --- vacancies rescore ---


@pytest.fixture
def rescore_setup(tmp_path, monkeypatch):
    db = tmp_path / "me.db"
    cached_database(db, [
        (1, "Поднялась", 1, "текст", 4), (2, "Упала", 1, "текст", 7),
        (3, "Без оценки", 1, "текст", None), (4, "Осталась", 1, "текст", 8),
        (5, "Сдвинулась", 1, "текст", 6),
    ])
    (tmp_path / "profile.md").write_text("профиль", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        "queries:\n  - {name: a, text: b}\n"
        f"scoring:\n  profile: {tmp_path / 'profile.md'}\n"
        "  threshold: 5\n",
        encoding="utf-8",
    )

    def fake(scores):
        ask = Ask(scores)
        monkeypatch.setattr(
            "hunter.corpus.cli.ask_model", lambda system, payload, *, criteria: ask(system, payload)
        )

    return db, config, fake


def rescore_argv(db, config, *extra):
    return ["--db", str(db), "rescore", "--config", str(config), *extra]


def test_rescore_prints_threshold_summary_and_changes(rescore_setup, capsys):
    db, config, fake = rescore_setup
    fake({"hh:1": 7, "hh:2": 3, "hh:3": 6, "hh:4": 9, "hh:5": 7})
    assert main(rescore_argv(db, config)) == 0
    out = capsys.readouterr().out
    # Было ≥ 5: 2, 4, 5. Стало ≥ 5: 1, 3, 4, 5.
    assert "Порог 5: было 3, стало 4" in out
    assert out.index("Поднялись") < out.index("Упали")
    assert "4 → 7 · Поднялась — потому" in out
    assert "— → 6 · Без оценки — потому" in out
    assert "7 → 3 · Упала — потому" in out
    assert out.index("Поднялась") < out.index("Упала")
    # Прошли порог с обеих сторон: не «поднялась» и не «упала», но изменение видно.
    assert "6 → 7 · Сдвинулась" in out and "8 → 9 · Осталась" in out
    assert out.index("Упала") < out.index("Сдвинулась")


def test_rescore_partial_claude_failure_does_not_look_like_a_drop(rescore_setup, capsys):
    db, config, fake = rescore_setup
    fake({"hh:1": 7, "hh:3": 6, "hh:4": 9, "hh:5": 7})  # «Упала» (была 7) не оценена
    assert main(rescore_argv(db, config)) == 0
    out = capsys.readouterr().out
    # Сравниваем только оценённые: было ≥ 5 — 4 и 5, стало — 1, 3, 4, 5.
    assert "Порог 5: было 2, стало 4 (всего вакансий: 5, не оценено: 1)" in out
    assert "Упали ниже порога" not in out


def test_rescore_never_touches_the_network_from_the_command_line(rescore_setup, monkeypatch):
    db, config, fake = rescore_setup

    def boom(*args, **kwargs):
        raise AssertionError("rescore пошёл в сеть")

    for target in ("hunter.fetch.get_html", "hunter.fetch.new_client",
                   "hunter.corpus.cli.get_html", "hunter.corpus.cli.new_client"):
        monkeypatch.setattr(target, boom)
    fake({"hh:1": 7})
    assert main(rescore_argv(db, config)) == 0


def test_rescore_with_model_down_exits_1_with_reason(rescore_setup, monkeypatch, capsys):
    db, config, _ = rescore_setup

    def down(system, payload, *, criteria):
        raise ScoringError("лимит подписки")

    monkeypatch.setattr("hunter.corpus.cli.ask_model", down)
    assert main(rescore_argv(db, config)) == 1
    assert "лимит подписки" in capsys.readouterr().err


def test_rescore_without_database_fails_and_creates_nothing(rescore_setup, tmp_path, capsys):
    _, config, _ = rescore_setup
    missing = tmp_path / "нет.db"
    assert main(rescore_argv(missing, config)) == 2
    assert "нет базы" in capsys.readouterr().err.lower()
    assert not missing.exists()


def test_rescore_without_scoring_section_is_a_usage_error(rescore_setup, capsys):
    db, config, _ = rescore_setup
    config.write_text("queries:\n  - {name: a, text: b}\n", encoding="utf-8")
    assert main(rescore_argv(db, config)) == 2
    assert "scoring" in capsys.readouterr().err


def test_rescore_with_nothing_to_rescore_says_so(rescore_setup, capsys):
    db, config, _ = rescore_setup
    empty = db.parent / "empty.db"
    cached_database(empty, [(1, "Старая", 30, "текст", None)])
    assert main(rescore_argv(empty, config)) == 0
    assert "Нечего пересчитывать" in capsys.readouterr().out


def test_rescore_rejects_nonpositive_days(rescore_setup, capsys):
    db, config, _ = rescore_setup
    assert main(rescore_argv(db, config, "--days", "0")) == 2
    assert "--days" in capsys.readouterr().err
