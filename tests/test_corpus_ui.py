import json
import threading
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

import pytest

from hunter import paths
from hunter.corpus import ui
from hunter.corpus.cli import main
from hunter.platforms.base import vacancy_card_row
from hunter.corpus.store import Corpus
from hunter.corpus.ui import Filters, make_server, parse_filters, render_page, select_rows
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


def _page(corpus, *, filters=None, threshold=5):
    rows = export_rows(corpus, None)
    return render_page(rows, filters=filters or Filters(), threshold=threshold, total=len(rows))


def test_page_escapes_data(corpus):
    _add(corpus, 1, name='<script>alert("x")</script>')
    _apply(corpus, 1, "selected", '<b>заметка</b>')
    page = _page(corpus)
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page
    assert "&lt;b&gt;заметка" in page


def test_page_has_columns_link_and_stages(corpus):
    _add(corpus, 7, name="Python dev")
    page = _page(corpus)
    for header in ("Название", "Компания", "Оценка", "Почему", "Флаги", "Роль", "Этап", "Заметка", "Найдена"):
        assert header in page
    assert 'href="https://hh.ru/vacancy/7"' in page
    assert 'target="_blank"' in page
    assert '<option value="" selected>не задан</option>' in page
    assert 'action="/save"' in page
    assert 'id="v-7"' in page


def test_score_class_follows_threshold(corpus):
    _add(corpus, 1)
    _add(corpus, 2)
    _add(corpus, 3)
    _add(corpus, 4)
    _score(corpus, 1, 8)
    _score(corpus, 2, 4)
    _score(corpus, 3, 2)
    page = _page(corpus)
    assert 'class="score good">8' in page
    assert 'class="score near">4' in page
    assert 'class="score low">2' in page
    assert 'class="score none" aria-label="без оценки"><span aria-hidden="true">—</span>' in page


def test_unscored_cell_is_labelled(corpus):
    _add(corpus, 1)
    page = _page(corpus)
    assert 'aria-label="без оценки"' in page
    assert '<span class="sr-only">без оценки</span>' not in page


def test_empty_page_keeps_layout_contract(corpus):
    _add(corpus, 1, name="Python dev")
    filters = Filters(q="нет такой вакансии")
    rows = select_rows(export_rows(corpus, None), filters)
    assert rows == []
    page = render_page(rows, filters=filters, threshold=5, total=1)
    assert "Ничего не найдено" in page
    assert "Показано 0 из 1" in page
    assert 'class="wrap"' in page
    assert 'class="count"' in page


def test_page_keeps_filters_in_form_and_row_back(corpus):
    _add(corpus, 7)
    page = _page(corpus, filters=Filters(days=7, status="new", q="python"))
    assert 'name="q" value="python"' in page
    assert 'name="back" value="/?days=7&amp;status=new&amp;q=python"' in page


def test_save_form_remembers_initial_status(corpus):
    _add(corpus, 1)
    _apply(corpus, 1, "applied", "отклик")
    _add(corpus, 2)
    page = _page(corpus)
    assert 'data-status="applied"' in page
    assert 'data-status=""' in page


def test_staged_row_has_no_blank_stage_option(corpus):
    _add(corpus, 1)
    _apply(corpus, 1, "applied", "отклик")

    page = _page(corpus)

    assert 'не задан' not in page


def test_page_exposes_names_sort_state_and_human_role(corpus):
    _add(corpus, 7, name="Python dev", role="promo")
    page = _page(corpus, filters=Filters(sort="score"))
    assert 'scope="col"' in page
    assert 'aria-sort="descending"' in page
    assert 'aria-label="Этап отклика для вакансии «Python dev»"' in page
    assert 'aria-label="Заметка к вакансии «Python dev»"' in page
    assert 'role="status"' in page
    assert "<caption" in page
    assert "продвижение" in page
    assert '<td class="found" title="' in page


@pytest.fixture
def server(db):
    started = make_server(db, 0)
    thread = threading.Thread(target=started.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{started.server_address[1]}"
    started.shutdown()
    started.server_close()


def test_get_renders_and_filters(corpus, server):
    _add(corpus, 1, name="Яндекс Практикум")
    _add(corpus, 2, name="VK")
    assert "Яндекс Практикум" in urlopen(server + "/").read().decode()
    body = urlopen(server + "/?q=" + quote("яндекс")).read().decode()
    assert "Яндекс Практикум" in body and "VK" not in body


def test_unknown_path_is_404(server):
    with pytest.raises(HTTPError) as caught:
        urlopen(server + "/nope")
    assert caught.value.code == 404


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _post(server, data, *, follow=False, fetch=False):
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if fetch:
        headers["X-Requested-With"] = "fetch"
    request = Request(server + "/save", data=urlencode(data).encode(), headers=headers)
    opener = build_opener() if follow else build_opener(NoRedirect)
    return opener.open(request)


def test_save_writes_status_and_note(db, corpus, server):
    _add(corpus, 7)
    response = _post(server, {"id": 7, "status": "applied", "note": "отклик", "back": "/"}, fetch=True)
    assert response.status == 204
    with Corpus.open(db) as opened:
        application = opened.application(7)
    assert (application["status"], application["note"]) == ("applied", "отклик")


def test_empty_note_clears_it(db, corpus, server):
    _add(corpus, 7)
    corpus.set_status(7, "applied", "старая")
    _post(server, {"id": 7, "status": "applied", "note": "", "back": "/"}, fetch=True)
    with Corpus.open(db) as opened:
        assert opened.application(7)["note"] == ""


def test_note_saves_without_a_stage(db, corpus, server):
    _add(corpus, 7)
    response = _post(server, {"id": 7, "status": "", "note": "позвонить", "back": "/"}, fetch=True)
    assert response.status == 204
    with Corpus.open(db) as opened:
        application = opened.application(7)
    assert application["status"] is None
    assert application["note"] == "позвонить"


def test_empty_status_keeps_the_current_stage(db, corpus, server):
    _add(corpus, 7)
    corpus.set_status(7, "applied", "отклик")
    _post(server, {"id": 7, "status": "", "note": "ждём ответа", "back": "/"}, fetch=True)
    with Corpus.open(db) as opened:
        application = opened.application(7)
    assert (application["status"], application["note"]) == ("applied", "ждём ответа")


def test_no_js_redirects_to_same_view(corpus, server):
    _add(corpus, 7)
    with pytest.raises(HTTPError) as caught:
        _post(server, {"id": 7, "status": "test", "note": "", "back": "/?days=7&status=new"})
    assert caught.value.code == 303
    assert caught.value.headers["Location"] == "/?days=7&status=new#v-7"


def test_bad_status_and_unknown_id_are_400(corpus, server):
    _add(corpus, 7)
    for data in ({"id": 7, "status": "nope", "note": ""}, {"id": 999, "status": "applied", "note": ""}):
        with pytest.raises(HTTPError) as caught:
            _post(server, data, fetch=True)
        assert caught.value.code == 400


def test_back_cannot_leave_the_site(corpus, server):
    _add(corpus, 7)
    for back in ("//evil.com", "/\\evil.com"):
        with pytest.raises(HTTPError) as caught:
            _post(server, {"id": 7, "status": "applied", "note": "", "back": back})
        assert caught.value.headers["Location"] == "/#v-7"


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(paths, "ME", tmp_path / "me")
    return tmp_path


def run(*argv):
    return main(list(argv), fetch_page=lambda url: "", sleep=lambda _: None)


def test_ui_command_passes_db_port_and_threshold(workdir, monkeypatch):
    assert run("init") == 0
    calls = {}
    monkeypatch.setattr(ui, "serve", lambda db, port, **kw: calls.update(db=db, port=port, kw=kw) or 0)
    assert run("ui", "--port", "9999", "--no-open") == 0
    assert calls["db"] == workdir / "me" / "job-hunter.db"
    assert calls["port"] == 9999
    assert calls["kw"] == {"open_browser": False, "threshold": 5}
