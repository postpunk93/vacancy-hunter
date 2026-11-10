import json
import logging
import sqlite3

import pytest

from hunter import paths
from hunter.corpus.cli import main
from hunter.platforms import parse_ref
from hunter.fetch import Blocked
from tests.factories import vacancy_page


@pytest.mark.parametrize("ref", [
    "123", " 123 ",
    "https://hh.ru/vacancy/123",
    "https://tomsk.hh.ru/vacancy/123?query=python&hhtmFrom=vacancy_search_list",
    "https://m.hh.ru/vacancy/123",
    "http://hh.ru/vacancy/123/",
    "https://www.zarplata.ru/vacancy/123",
    "https://tomsk.zarplata.ru/vacancy/123",
])
def test_vacancy_refs(ref):
    assert parse_ref(ref) == ("hh", "123")


@pytest.mark.parametrize("ref", [
    "abc", "", "https://evil.com/vacancy/1", "https://nothh.ru/vacancy/1",
    "https://hh.ru/employer/1", "https://hh.ru/vacancy/abc", "ftp://hh.ru/vacancy/1",
])
def test_rejected_refs(ref):
    with pytest.raises(ValueError):
        parse_ref(ref)


class Site:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        item = self.pages[url]
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(paths, "ME", tmp_path / "me")
    return tmp_path


def run(*argv, site=None):
    return main(list(argv), fetch_page=site, sleep=lambda _: None)


def test_init_creates_me_and_database_only(workdir, capsys):
    assert run("init") == 0

    assert (workdir / "me" / "job-hunter.db").exists()
    assert not (workdir / "CLAUDE.md").exists()
    assert sorted(p.name for p in workdir.iterdir()) == ["me"]


def test_init_creates_me_even_with_another_db(workdir):
    assert run("--db", str(workdir / "other.db"), "init") == 0

    assert (workdir / "me").is_dir()
    assert (workdir / "other.db").exists()


def test_init_never_overwrites(workdir, capsys):
    run("init")
    db = workdir / "me" / "job-hunter.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE mine (x)")
        conn.execute("INSERT INTO mine VALUES (1)")
    capsys.readouterr()

    assert run("init") == 0

    assert "уже была" in capsys.readouterr().out
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT x FROM mine").fetchall() == [(1,)]


@pytest.mark.parametrize("argv", [
    ("show", "1"), ("mark", "1", "yes"), ("sql", "SELECT 1"), ("fetch", "1"),
    ("collect", "--role", "r", "--text", "t"),
])
def test_commands_outside_job_folder_fail_clearly(workdir, capsys, argv):
    assert run(*argv, site=Site({})) == 2

    assert "vacancies init" in capsys.readouterr().err
    assert not (workdir / "me" / "job-hunter.db").exists()


def test_fetch_show_mark_sql_round_trip(workdir, capsys):
    run("init")
    site = Site({"https://hh.ru/vacancy/42": vacancy_page(42)})

    assert run("fetch", "https://tomsk.hh.ru/vacancy/42?from=x", "--role", "backend", "--delay", "0", site=site) == 0
    assert run("mark", "42", "yes", "нравится стек") == 0
    capsys.readouterr()

    assert run("show", "42") == 0
    shown = capsys.readouterr().out
    assert "Пишем на **Python**" in shown
    assert "Вердикт: yes — нравится стек" in shown
    assert "Роли: backend (manual)" in shown

    assert run("sql", "SELECT vacancy_id, verdict FROM verdicts") == 0
    assert "| 42 | yes |" in capsys.readouterr().out

    assert run("sql", "SELECT id, name FROM vacancies", "--json") == 0
    assert json.loads(capsys.readouterr().out) == [{"id": 42, "name": "Вакансия 42"}]


def test_sql_cannot_write(workdir, capsys):
    run("init")
    run("fetch", "42", "--delay", "0", site=Site({"https://hh.ru/vacancy/42": vacancy_page(42)}))

    assert run("sql", "DELETE FROM vacancies") == 1
    assert "Ошибка SQL" in capsys.readouterr().err
    run("sql", "SELECT count(*) AS n FROM vacancies")
    assert "| 1 |" in capsys.readouterr().out


def test_show_unknown_vacancy(workdir, capsys):
    run("init")

    assert run("show", "7") == 1
    assert "vacancies fetch 7" in capsys.readouterr().err


def test_mark_unknown_vacancy(workdir, capsys):
    run("init")

    assert run("mark", "7", "no") == 2
    assert "vacancies fetch 7" in capsys.readouterr().err


def test_bad_ref_is_rejected_before_any_request(workdir, capsys):
    run("init")
    site = Site({})

    assert run("fetch", "42", "https://example.com/x", site=site) == 2
    assert site.calls == []


def test_collect_page_limit(workdir, capsys):
    run("init")

    assert run("collect", "--role", "r", "--text", "t", "--pages", "0") == 2
    assert run("collect", "--role", "r", "--text", "t", "--pages", "41") == 2


def test_request_log_is_not_echoed_per_request(workdir):
    # Строка лога httpx на каждый запрос — 40 строк шума в выводе fetch,
    # который читает Claude; повторы get_html (WARNING и выше) остаются.
    run("init")

    # Свой уровень, а не унаследованный: под pytest корень и так WARNING.
    assert logging.getLogger("httpx").level == logging.WARNING


def test_collect_block_exits_3(workdir, capsys):
    run("init")

    class Blocking:
        def __call__(self, url):
            raise Blocked("403")

    assert run("collect", "--role", "r", "--text", "t", "--delay", "0", site=Blocking()) == 3
    assert "блокировка" in capsys.readouterr().out


def test_db_defaults_to_project_me(tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(paths, "ME", tmp_path / "me")

    assert run("init") == 0

    assert (tmp_path / "me" / "job-hunter.db").exists()
    assert not (elsewhere / "vacancies.db").exists()


def test_export_with_nothing_to_export_makes_no_file(workdir, capsys):
    run("init")

    assert run("export") == 0

    assert "нечего выгружать" in capsys.readouterr().out.lower()
    assert not (workdir / "me" / "exports").exists()


def test_export_writes_xlsx_into_exports_and_reports_it(workdir, capsys):
    from openpyxl import load_workbook

    run("init")
    run("fetch", "42", "--role", "backend", "--delay", "0",
        site=Site({"https://hh.ru/vacancy/42": vacancy_page(42)}))
    capsys.readouterr()

    assert run("export") == 0

    (path,) = (workdir / "me" / "exports").glob("vacancies-*.xlsx")
    assert str(path) in capsys.readouterr().out
    rows = list(load_workbook(path).active.iter_rows(min_row=2, values_only=True))
    assert [(r[1], r[7]) for r in rows] == [("Вакансия 42", "новая")]


def test_export_to_given_path_prints_row_count(workdir, capsys):
    run("init")
    run("fetch", "42", "--delay", "0", site=Site({"https://hh.ru/vacancy/42": vacancy_page(42)}))
    capsys.readouterr()
    target = workdir / "custom" / "my.xlsx"

    assert run("export", "--days", "7", "--out", str(target)) == 0

    assert target.exists()
    out = capsys.readouterr().out
    assert str(target) in out and "1" in out


def test_export_without_database_fails_clearly(workdir, capsys):
    assert run("export") == 2

    assert "vacancies init" in capsys.readouterr().err


def _add_vacancy(vacancy_id: int) -> None:
    run("fetch", str(vacancy_id), "--delay", "0",
        site=Site({f"https://hh.ru/vacancy/{vacancy_id}": vacancy_page(vacancy_id)}))


def test_status_records_note_and_test(workdir, capsys):
    run("init")
    _add_vacancy(42)
    capsys.readouterr()

    assert run("status", "https://hh.ru/vacancy/42", "test", "срочно",
               "--test", "написать сервис", "--due", "2026-10-20") == 0

    assert "42" in capsys.readouterr().out
    run("sql", "SELECT status, note, test_task, test_due FROM applications", "--json")
    assert json.loads(capsys.readouterr().out) == [
        {"status": "test", "note": "срочно", "test_task": "написать сервис", "test_due": "2026-10-20"}
    ]


def test_status_for_unknown_vacancy(workdir, capsys):
    run("init")

    assert run("status", "7", "applied") == 2
    assert "vacancies fetch 7" in capsys.readouterr().err


def test_bad_due_date_is_a_usage_error_and_db_is_untouched(workdir, capsys):
    run("init")
    _add_vacancy(42)
    capsys.readouterr()

    assert run("status", "42", "test", "--due", "20 октября") == 2
    assert "--due" in capsys.readouterr().err
    run("sql", "SELECT count(*) AS n FROM applications", "--json")
    assert json.loads(capsys.readouterr().out) == [{"n": 0}]


def test_bad_due_date_does_not_create_the_database(workdir):
    assert run("status", "42", "test", "--due", "завтра") == 2
    assert not (workdir / "me" / "job-hunter.db").exists()


def test_funnel_hides_skipped_and_closed(workdir, capsys):
    run("init")
    for vacancy_id, status in ((1, "applied"), (2, "skipped"), (3, "closed"), (4, "selected")):
        _add_vacancy(vacancy_id)
        run("status", str(vacancy_id), status)
    capsys.readouterr()

    assert run("funnel") == 0

    out = capsys.readouterr().out
    assert "откликнулся" in out and "выбрана" in out
    assert "пропущена" not in out and "закрыта" not in out
    assert out.index("выбрана") < out.index("откликнулся")  # по порядку воронки


def test_empty_funnel_says_so(workdir, capsys):
    run("init")

    assert run("funnel") == 0
    assert "пока нет" in capsys.readouterr().out


def test_todo_on_fresh_clone_suggests_setup(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(paths, "ME", tmp_path / "me")

    assert run("todo") == 0

    assert "Первый запуск" in capsys.readouterr().out
    assert not (tmp_path / "me").exists()


def test_todo_without_database_is_silent_and_creates_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(paths, "ME", tmp_path / "me")
    (tmp_path / "me").mkdir()
    (tmp_path / "me" / "profile.md").write_text("я", encoding="utf-8")

    assert run("todo") == 0

    assert capsys.readouterr().out == ""
    assert not (tmp_path / "me" / "job-hunter.db").exists()


def test_todo_with_broken_database_is_silent(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(paths, "ME", tmp_path / "me")
    (tmp_path / "me").mkdir()
    (tmp_path / "me" / "profile.md").write_text("я", encoding="utf-8")
    (tmp_path / "me" / "job-hunter.db").write_text("это не база", encoding="utf-8")

    assert run("todo") == 0

    assert capsys.readouterr().out == ""


def test_todo_prints_nothing_when_nothing_is_due(workdir, capsys):
    (workdir / "me").mkdir()
    (workdir / "me" / "profile.md").write_text("я", encoding="utf-8")
    run("init")
    _add_vacancy(42)
    run("status", "42", "selected")
    capsys.readouterr()

    assert run("todo") == 0

    assert capsys.readouterr().out == ""


def test_todo_lists_what_is_due(workdir, capsys):
    (workdir / "me").mkdir()
    (workdir / "me" / "profile.md").write_text("я", encoding="utf-8")
    run("init")
    _add_vacancy(42)
    run("status", "42", "test", "--test", "сервис", "--due", "2020-01-01")
    capsys.readouterr()

    assert run("todo") == 0

    out = capsys.readouterr().out
    assert "просрочено" in out and "(id 42)" in out
