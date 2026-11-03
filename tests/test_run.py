import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from hunter import run as run_module
from hunter.config import Config
from hunter.corpus.extract import vacancy_card_row
from hunter.fetch import Blocked, FetchError
from hunter.models import SearchQuery
from hunter.run import EXIT_BAD_CONFIG, EXIT_UNEXPECTED, run_pass
from hunter.store import Store
from hunter.telegram import TelegramError, send_messages

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 1, 14, 30, tzinfo=timezone.utc)

CONFIG = Config(
    sources=("hh",),
    queries=(SearchQuery(name="Python backend", text="python"),),
)


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def _card(vacancy_id, area, timestamp=1700000000):
    return {
        "vacancyId": vacancy_id,
        "name": "SalesMan",
        "company": {"visibleName": "Roobinium"},
        "area": {"name": area},
        "links": {"desktop": f"https://hh.ru/vacancy/{vacancy_id}"},
        "publicationTime": {"@timestamp": timestamp},
        "compensation": {
            "from": 40000, "to": 300000, "currencyCode": "RUR",
            "gross": False, "mode": "MONTH",
        },
    }


def _page_with(cards):
    payload = json.dumps({"vacancySearchResult": {"vacancies": cards}})
    return f'<template style="display:none" id="HH-Lux-InitialState">{payload}</template>'


class Sender:
    """Записывает отправленные сообщения; может изображать отказ Телеграма."""

    def __init__(self, fail=False):
        self.messages = []
        self.fail = fail

    def __call__(self, texts):
        if self.fail:
            raise TelegramError("Телеграм ответил 500")
        self.messages.extend(texts)


def page_returning(name):
    return lambda url: fixture(name)


def no_pause(_seconds):
    """Заглушка вместо time.sleep: тесты не должны ждать по-настоящему."""


@pytest.fixture
def clean_root_logger():
    """_setup_logging добавляет обработчики в корневой логгер и никогда их
    не снимает. Без этой уборки следующие тесты наследуют открытые файловые
    хендлы на уже удалённые временные каталоги предыдущих тестов."""
    root = logging.getLogger()
    before = list(root.handlers)
    yield
    for handler in list(root.handlers):
        if handler not in before:
            root.removeHandler(handler)
            handler.close()


def test_first_run_sends_everything_it_found():
    """Бот запускают вручную: первый сбор за неделю — это то, что хотят
    прочитать, а не молча занести в базу."""
    store = Store(":memory:")
    sender = Sender()

    code = run_pass(
        config=CONFIG,
        store=store,
        fetch_page=page_returning("hh_search_sorted_with_salary.html"),
        send=sender,
        now=NOW,
    )

    text = "\n".join(sender.messages)
    assert code == 0
    assert text.count("<a href=") == 50, "все вакансии, без потолка"
    assert "ещё" not in text
    marked = store._conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0]
    assert marked == 50




def test_second_run_sends_only_what_is_new():
    store = Store(":memory:")
    run_pass(
        config=CONFIG,
        store=store,
        fetch_page=page_returning("hh_search_sorted_with_salary.html"),
        send=Sender(),
        now=NOW,
    )
    sender = Sender()

    code = run_pass(
        config=CONFIG,
        store=store,
        fetch_page=page_returning("hh_search_sorted_with_salary.html"),
        send=sender,
        now=NOW + timedelta(minutes=30),
    )

    assert code == 0
    assert sender.messages == [], "та же выдача — сообщений быть не должно"


def test_advertising_never_reaches_the_digest():
    store = Store(":memory:")
    sender = Sender()

    run_pass(
        config=CONFIG,
        store=store,
        fetch_page=page_returning("hh_search_python_msk.html"),
        send=sender,
        now=NOW,
    )

    text = "\n".join(sender.messages)
    assert "114811554" not in text, "рекламная врезка отсеяна"
    assert "з/п не указана" not in text, "вакансии без зарплаты отсеяны"


def test_vacancies_are_not_marked_seen_when_telegram_fails():
    store = Store(":memory:")
    sender = Sender(fail=True)

    code = run_pass(
        config=CONFIG,
        store=store,
        fetch_page=page_returning("hh_search_sorted_with_salary.html"),
        send=sender,
        now=NOW,
    )

    assert code == 1
    marked = store._conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0]
    assert marked == 0, (
        "главный инвариант: без подтверждённой отправки вакансии не помечаются"
    )


def test_failed_request_becomes_a_warning_and_does_not_stop_the_pass():
    config = Config(
        sources=("hh",),
        queries=(
            SearchQuery(name="Сломанный", text="broken"),
            SearchQuery(name="Рабочий", text="python"),
        ),
    )
    store = Store(":memory:")
    sender = Sender()

    def fetch_page(url):
        if "broken" in url:
            raise FetchError("таймаут")
        return fixture("hh_search_sorted_with_salary.html")

    # pause подменён: этот тест — единственный, который доходит до паузы
    # между запросами, и ждать полторы секунды по-настоящему незачем.
    code = run_pass(
        config=config,
        store=store,
        fetch_page=fetch_page,
        send=sender,
        now=NOW,
        pause=no_pause,
    )

    text = "\n".join(sender.messages)
    assert code == 0
    assert "⚠️" in text
    assert "Сломанный" in text
    assert "Рабочий" in text, "рабочий запрос отработал несмотря на сбой соседнего"


def test_parse_failure_is_reported_loudly():
    store = Store(":memory:")
    sender = Sender()

    def fetch_page(url):
        return "<html>редизайн</html>"

    code = run_pass(config=CONFIG, store=store, fetch_page=fetch_page, send=sender, now=NOW)

    text = "\n".join(sender.messages)
    assert code == 0
    assert "разбор" in text.lower() or "парс" in text.lower()


def test_block_stops_the_pass_with_its_own_exit_code():
    store = Store(":memory:")
    sender = Sender()

    def fetch_page(url):
        raise Blocked("403")

    code = run_pass(config=CONFIG, store=store, fetch_page=fetch_page, send=sender, now=NOW)

    assert code == 3










def test_only_with_salary_false_keeps_vacancies_without_salary():
    """only_with_salary — страховка на случай, если hh перестанет уважать
    параметр, а не молчаливая перезапись выбора пользователя."""
    config = Config(
        sources=("hh",),
        queries=(
            SearchQuery(name="Python backend", text="python", only_with_salary=False),
        ),
    )
    store = Store(":memory:")
    sender = Sender()

    run_pass(
        config=config,
        store=store,
        fetch_page=page_returning("hh_search_python_msk.html"),
        send=sender,
        now=NOW,
        pause=no_pause,
    )

    text = "\n".join(sender.messages)
    assert "з/п не указана" in text








def test_one_blocked_source_does_not_discard_the_other_sources_results():
    config = Config(
        sources=("hh", "zarplata"),
        queries=(SearchQuery(name="Python backend", text="python"),),
    )
    store = Store(":memory:")
    sender = Sender()

    def fetch_page(url):
        if "zarplata" in url:
            raise Blocked("403")
        return fixture("hh_search_sorted_with_salary.html")

    code = run_pass(
        config=config, store=store, fetch_page=fetch_page, send=sender, now=NOW, pause=no_pause
    )

    text = "\n".join(sender.messages)
    assert code == 0
    assert "<a href=" in text, "вакансии с hh должны дойти несмотря на блокировку zarplata"
    assert "zarplata" in text and "заблокировал" in text


def test_dry_run_does_not_touch_the_database(tmp_path, monkeypatch, clean_root_logger):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "queries:\n  - name: Python backend\n    text: python\n", encoding="utf-8"
    )
    db_path = tmp_path / "state.db"

    monkeypatch.setattr(
        run_module,
        "get_html",
        lambda url, **kwargs: fixture("hh_search_sorted_with_salary.html"),
    )
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)

    code = run_module.main(
        [
            "--dry-run",
            "--config", str(config_path),
            "--db", str(db_path),
            "--log-dir", str(tmp_path / "logs"),
            "--debug-dir", str(tmp_path / "debug"),
        ]
    )

    assert code == 0
    store = Store(db_path)
    try:
        assert store.is_empty(), "dry-run не должен писать вакансии в seen"
    finally:
        store.close()


def test_unexpected_error_is_logged_and_returns_its_own_exit_code(tmp_path, clean_root_logger):
    """Битая state.db раньше роняла процесс без единой строчки в логе —
    под pythonw.exe у процесса нет stderr, и такой сбой был полностью немым."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "queries:\n  - name: Python backend\n    text: python\n", encoding="utf-8"
    )
    db_path = tmp_path / "state.db"
    db_path.write_text("это не файл sqlite", encoding="utf-8")
    log_dir = tmp_path / "logs"

    code = run_module.main(
        [
            "--dry-run",
            "--config", str(config_path),
            "--db", str(db_path),
            "--log-dir", str(log_dir),
            "--debug-dir", str(tmp_path / "debug"),
        ]
    )

    assert code == EXIT_UNEXPECTED
    log_text = (log_dir / "hunter.log").read_text(encoding="utf-8")
    assert "непредвиденной" in log_text


def test_bot_token_never_reaches_the_log_file(tmp_path, clean_root_logger):
    """httpx логирует каждый запрос на INFO уровне целиком, с URL — а в URL
    отправки в Телеграм зашит токен бота. Корневой логгер стоит на INFO
    из-за _setup_logging, так что без явного заглушения httpx его записи
    попадают прямиком в hunter.log."""
    token = "123456:SUPERSECRETTOKEN"

    run_module._setup_logging(tmp_path / "logs")

    def handler(request):
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    send_messages(token, "456", ["первое"], client=client)

    for h in logging.getLogger().handlers:
        h.flush()

    log_text = (tmp_path / "logs" / "hunter.log").read_text(encoding="utf-8")
    assert token not in log_text, "токен бота не должен попадать в hunter.log"


def test_vacancy_clones_across_cities_are_merged_and_absorbed_marked_seen():
    store = Store(":memory:")
    sender = Sender()
    cards = [_card(1, "Москва"), _card(2, "Казань"), _card(3, "Самара")]

    code = run_pass(
        config=CONFIG,
        store=store,
        fetch_page=lambda url: _page_with(cards),
        send=sender,
        now=NOW,
    )

    text = "\n".join(sender.messages)
    assert code == 0
    assert text.count("<a href=") == 1, "три карточки одной вакансии — одна строка в дайджесте"
    assert "Москва, Казань, Самара" in text

    marked = store._conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0]
    assert marked == 3, "поглощённые дубли тоже должны быть помечены виденными"


def test_warnings_are_shown_on_every_run():
    """Запуск ручной: человек смотрит на результат прямо сейчас, глушить
    повтор предупреждения незачем."""
    store = Store(":memory:")

    def fetch_page(url):
        raise Blocked("403")

    first = Sender()
    run_pass(config=CONFIG, store=store, fetch_page=fetch_page, send=first, now=NOW)
    second = Sender()
    run_pass(
        config=CONFIG,
        store=store,
        fetch_page=fetch_page,
        send=second,
        now=NOW + timedelta(hours=1),
    )

    assert first.messages
    assert second.messages, "повторный запуск снова сообщает о блокировке"


def test_vacancies_older_than_the_period_are_cut_when_hh_returns_more():
    """search_period 2 — hh отдаёт выдачу за 3 дня, лишний день отсекаем."""
    config = Config(
        sources=("hh",),
        queries=(SearchQuery(name="Python backend", text="python", search_period=2),),
    )
    fresh = int((NOW - timedelta(days=1)).timestamp())
    stale = int((NOW - timedelta(days=2, hours=12)).timestamp())
    cards = [_card(1, "Москва", fresh), {**_card(2, "Казань", stale), "name": "Другая"}]
    sender = Sender()

    run_pass(
        config=config,
        store=Store(":memory:"),
        fetch_page=lambda url: _page_with(cards),
        send=sender,
        now=NOW,
    )

    text = "\n".join(sender.messages)
    assert "vacancy/1" in text
    assert "vacancy/2" not in text


def test_paging_stops_after_a_page_that_is_not_full():
    config = Config(
        sources=("hh",),
        queries=(SearchQuery(name="Python backend", text="python", max_pages=3),),
    )
    urls = []

    def fetch_page(url):
        urls.append(url)
        return _page_with([_card(1, "Москва")])

    run_pass(
        config=config,
        store=Store(":memory:"),
        fetch_page=fetch_page,
        send=Sender(),
        now=NOW,
        pause=no_pause,
    )

    assert len(urls) == 1, "неполная страница — дальше выдача пуста"


def _dry_run_main(tmp_path, monkeypatch, *extra):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "queries:\n  - name: Python backend\n    text: python\n", encoding="utf-8"
    )
    urls = []

    def get_html(url, **kwargs):
        urls.append(url)
        return fixture("hh_search_sorted_with_salary.html")

    monkeypatch.setattr(run_module, "get_html", get_html)
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)
    code = run_module.main(
        [
            "--dry-run",
            "--config", str(config_path),
            "--db", str(tmp_path / "state.db"),
            "--log-dir", str(tmp_path / "logs"),
            "--debug-dir", str(tmp_path / "debug"),
            *extra,
        ]
    )
    return code, urls


def test_days_option_overrides_the_configured_period(tmp_path, monkeypatch, clean_root_logger):
    code, urls = _dry_run_main(tmp_path, monkeypatch, "--days", "7")

    assert code == 0
    assert urls and all("search_period=7" in url for url in urls)


@pytest.mark.parametrize("days", ["0", "31"])
def test_days_option_outside_one_to_thirty_is_a_config_error(
    tmp_path, monkeypatch, clean_root_logger, days
):
    code, urls = _dry_run_main(tmp_path, monkeypatch, "--days", days)

    assert code == EXIT_BAD_CONFIG
    assert urls == []


# --- оценка вакансий ---------------------------------------------------------

from hunter.models import Score  # noqa: E402
from hunter.scoring import Criteria, score_pass  # noqa: E402
from tests.factories import vacancy_page  # noqa: E402


def _scored_page(*ids):
    cards = []
    for vacancy_id in ids:
        card = _card(vacancy_id, "Москва")
        card["name"] = f"Вакансия {vacancy_id}"
        cards.append(card)
    return _page_with(cards)


class FakeScore:
    """Функция оценки для run_pass: заданные оценки по ключу, остальным — 7."""

    def __init__(self, scores=None, warnings=()):
        self.scores = scores or {}
        self.warnings = list(warnings)
        self.calls = []

    def __call__(self, vacancies):
        self.calls.append([v.key for v in vacancies])
        return (
            {v.key: self.scores.get(v.key, Score(7, "подходит")) for v in vacancies},
            self.warnings,
        )


def _scored_pass(store, score, *, ids=(1, 2), sender=None, **kwargs):
    sender = sender if sender is not None else Sender()
    code = run_pass(
        config=CONFIG,
        store=store,
        fetch_page=lambda url: _scored_page(*ids),
        send=sender,
        now=NOW,
        pause=no_pause,
        score=score,
        **kwargs,
    )
    return code, "\n".join(sender.messages)


def _sent_at(store, key):
    return store._conn.execute("SELECT sent_at FROM seen WHERE key = ?", (key,)).fetchone()


def test_below_threshold_stays_in_corpus_not_telegram():
    store = Store(":memory:")
    score = FakeScore({"hh:1": Score(8, "да"), "hh:2": Score(1, "нет")})

    code, text = _scored_pass(store, score)

    assert code == 0
    assert "Вакансия 1" in text and "Вакансия 2" not in text
    assert "ещё 1 ниже 5 — в базе, выгрузка: vacancies export" in text
    assert store.corpus.exists(2), "отсеянная лежит в корпусе — на неё и указывает сводка"
    assert _sent_at(store, "hh:1")[0] is not None
    assert _sent_at(store, "hh:2")[0] is None, "отсеянная помечена виденной, но не отправленной"


def test_card_score_below_threshold_goes_to_nearby_not_the_main_list():
    store = Store(":memory:")
    score = FakeScore({
        "hh:1": Score(7, "да"),
        "hh:2": Score(3, "по карточке", from_card=True),
    })

    _, text = _scored_pass(store, score)

    assert "• 7 · " in text
    assert "Рядом (1)" in text
    assert "3* · " in text and "Вакансия 2" in text


def test_unscored_goes_to_its_own_block():
    store = Store(":memory:")
    score = FakeScore({"hh:1": Score(7, "да"), "hh:2": Score(None, "модель не ответила")})

    _, text = _scored_pass(store, score)

    assert "Без оценки (1)" in text
    assert "• ? · " in text and "Вакансия 2" in text


def test_low_scores_are_counted_only():
    store = Store(":memory:")
    score = FakeScore({"hh:1": Score(7, "да"), "hh:2": Score(1, "нет")})

    _, text = _scored_pass(store, score)

    assert "ещё 1 ниже 5" in text
    assert "1 · Вакансия 2" not in text


def test_nearby_threshold_comes_from_the_argument():
    store = Store(":memory:")
    score = FakeScore({"hh:1": Score(5, "да"), "hh:2": Score(3, "рядом")})

    _, text = _scored_pass(store, score, nearby_threshold=2)

    assert "Рядом (1)" in text


def test_scoring_crash_sends_all_to_the_unscored_block():
    store = Store(":memory:")

    def boom(vacancies):
        raise RuntimeError("quota")

    _, text = _scored_pass(store, boom)

    assert "Без оценки (2)" in text
    assert "<b>" not in text, "в основном списке пусто — всё в серой зоне"


def test_all_below_threshold_sends_header_only():
    store = Store(":memory:")
    score = FakeScore({"hh:1": Score(2, "нет"), "hh:2": Score(2, "нет")})

    code, text = _scored_pass(store, score)

    assert code == 0
    assert "ниже 5 — в базе, выгрузка: vacancies export" in text
    assert "<a href=" not in text
    assert store.unseen([]) == []
    assert store._conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0] == 2


def test_threshold_comes_from_the_argument():
    store = Store(":memory:")
    score = FakeScore({"hh:1": Score(6, "да"), "hh:2": Score(7, "да")})

    _, text = _scored_pass(store, score, threshold=7)

    assert "• 7 · " in text and "1 новая вакансия" in text
    assert "Рядом (1)" in text and "Вакансия 1" in text


def test_scores_are_shown_and_sorted_in_the_digest():
    store = Store(":memory:")
    score = FakeScore({"hh:1": Score(6, "да"), "hh:2": Score(9, "да")})

    _, text = _scored_pass(store, score)

    assert text.index("• 9 · ") < text.index("• 6 · ")


def test_telegram_failure_after_scoring_marks_nothing(tmp_path):
    store = Store(tmp_path / "s.db")
    vacancy_urls = []
    asks = []

    def fetch_page(url):
        if "/vacancy/" in url:
            vacancy_urls.append(url)
            return vacancy_page(int(url.rsplit("/", 1)[1]))
        return _scored_page(1, 2)

    def ask(system, payload):
        items = json.loads(payload)
        asks.append(items)
        return {"items": [{"id": i["id"], "score": 8, "why": "да", "flags": []} for i in items]}

    def score(vacancies):
        return score_pass(
            vacancies, fetch_page=fetch_page, store=store, pause=no_pause,
            ask=ask, criteria=Criteria("профиль"),
        )

    def one_pass(sender):
        return run_pass(config=CONFIG, store=store, fetch_page=fetch_page, send=sender,
                        now=NOW, pause=no_pause, score=score)

    assert one_pass(Sender(fail=True)) == run_module.EXIT_TELEGRAM_FAILED
    assert store._conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0] == 0
    assert len(vacancy_urls) == 2 and len(asks) == 1

    sender = Sender()
    assert one_pass(sender) == 0
    assert len(vacancy_urls) == 2, "тексты взяты из кеша"
    assert len(asks) == 1, "оценки взяты из кеша"
    assert "• 8 · " in "\n".join(sender.messages)


def test_scoring_warnings_reach_the_digest():
    store = Store(":memory:")
    score = FakeScore(warnings=["не оценено вакансий: 2 — boom"])

    code, text = _scored_pass(store, score)

    assert code == 0
    assert "⚠️ не оценено вакансий: 2 — boom" in text


def test_block_during_text_fetch_keeps_exit_ok(tmp_path):
    store = Store(tmp_path / "s.db")

    def fetch_page(url):
        if "/vacancy/" in url:
            raise Blocked(f"{url}: 403")
        return _scored_page(1, 2)

    def ask(system, payload):
        items = json.loads(payload)
        return {"items": [{"id": i["id"], "score": 3, "why": "мало данных", "flags": []} for i in items]}

    def score(vacancies):
        return score_pass(
            vacancies, fetch_page=fetch_page, store=store, pause=no_pause,
            ask=ask, criteria=Criteria("профиль"),
        )

    sender = Sender()
    code = run_pass(config=CONFIG, store=store, fetch_page=fetch_page, send=sender,
                    now=NOW, pause=no_pause, score=score)
    text = "\n".join(sender.messages)

    assert code == 0
    assert "Вакансия 1" in text and "Вакансия 2" in text, "по карточке — мимо порога"
    assert "• 3* · " in text
    assert "⚠️ hh заблокировал скачивание текстов" in text


def test_dry_run_prints_filtered_and_keeps_seen_untouched(capsys):
    store = Store(":memory:")
    score = FakeScore({"hh:1": Score(8, "да"), "hh:2": Score(3, "чужая роль")})

    _scored_pass(store, score, dry_run=True)

    out = capsys.readouterr().out
    assert "Рядом (3–4):" in out
    assert "3 · Вакансия 2 — чужая роль" in out
    assert store._conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0] == 0


def test_no_fresh_vacancies_means_no_scoring_call():
    store = Store(":memory:")
    _scored_pass(store, FakeScore())
    score = FakeScore()

    _scored_pass(store, score)

    assert score.calls == []


def _main_args(tmp_path, *extra):
    return [
        "--dry-run",
        "--config", str(tmp_path / "config.yaml"),
        "--db", str(tmp_path / "state.db"),
        "--log-dir", str(tmp_path / "logs"),
        "--debug-dir", str(tmp_path / "debug"),
        *extra,
    ]


def _scoring_config(tmp_path, profile):
    (tmp_path / "config.yaml").write_text(
        "queries:\n  - name: Python backend\n    text: python\n"
        f"scoring:\n  profile: {profile}\n  threshold: 6\n",
        encoding="utf-8",
    )


def test_missing_criteria_file_is_a_config_error(tmp_path, clean_root_logger):
    _scoring_config(tmp_path, tmp_path / "nope.md")
    assert run_module.main(_main_args(tmp_path)) == EXIT_BAD_CONFIG


def test_unreadable_criteria_file_is_a_config_error(tmp_path, clean_root_logger):
    profile = tmp_path / "profile.md"
    profile.write_bytes(b"\xff\xfe\xfa")  # не utf-8
    _scoring_config(tmp_path, profile)
    assert run_module.main(_main_args(tmp_path)) == EXIT_BAD_CONFIG


def test_no_score_flag_skips_scoring(tmp_path, monkeypatch, clean_root_logger):
    profile = tmp_path / "profile.md"
    profile.write_text("профиль", encoding="utf-8")
    _scoring_config(tmp_path, profile)
    seen = []

    def fake_run_pass(**kwargs):
        seen.append(kwargs)
        return 0

    monkeypatch.setattr(run_module, "run_pass", fake_run_pass)

    assert run_module.main(_main_args(tmp_path)) == 0
    assert callable(seen[-1]["score"])
    assert seen[-1]["threshold"] == 6

    assert run_module.main(_main_args(tmp_path, "--no-score")) == 0
    assert seen[-1]["score"] is None


def test_crash_inside_scoring_still_sends_the_digest():
    store = Store(":memory:")

    def score(vacancies):
        raise AttributeError("'str' object has no attribute 'get'")

    code, text = _scored_pass(store, score)

    assert code == 0
    assert "Вакансия 1" in text and "Вакансия 2" in text
    assert "• ? · " in text
    assert "⚠️ оценка упала" in text
    assert store._conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0] == 2


# --- личная папка me/, Телеграм по желанию -----------------------------------


def _quiet_main_args(tmp_path, config_path, *extra):
    return [
        "--config", str(config_path),
        "--env", str(tmp_path / "нет.env"),
        "--db", str(tmp_path / "state.db"),
        "--log-dir", str(tmp_path / "logs"),
        "--debug-dir", str(tmp_path / "debug"),
        *extra,
    ]


def test_pass_without_telegram_prints_summary_and_marks_seen(
    tmp_path, monkeypatch, capsys, clean_root_logger
):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "queries:\n  - name: Python backend\n    text: python\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        run_module,
        "get_html",
        lambda url, **kwargs: fixture("hh_search_sorted_with_salary.html"),
    )

    def no_telegram(*args, **kwargs):
        raise AssertionError("Телеграм выключен — отправки быть не должно")

    monkeypatch.setattr(run_module, "send_messages", no_telegram)
    for name in ("TG_BOT_TOKEN", "TG_CHAT_ID"):
        monkeypatch.delenv(name, raising=False)
    args = _quiet_main_args(tmp_path, config_path)

    assert run_module.main(args) == 0
    first = capsys.readouterr().out
    assert "https://hh.ru/vacancy/" in first
    assert "<a href" not in first

    assert run_module.main(args) == 0
    assert "https://hh.ru/vacancy/" not in capsys.readouterr().out


def test_dry_run_without_telegram_leaves_vacancies_new(
    tmp_path, monkeypatch, capsys, clean_root_logger
):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "queries:\n  - name: Python backend\n    text: python\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        run_module,
        "get_html",
        lambda url, **kwargs: fixture("hh_search_sorted_with_salary.html"),
    )
    args = _quiet_main_args(tmp_path, config_path, "--dry-run")

    assert run_module.main(args) == 0
    first = capsys.readouterr().out
    assert run_module.main(args) == 0
    second = capsys.readouterr().out

    assert "https://hh.ru/vacancy/" in first
    assert second == first, "dry-run ничего не помечает виденным"


def test_missing_config_points_to_setup(tmp_path, capsys, clean_root_logger):
    code = run_module.main(_quiet_main_args(tmp_path, tmp_path / "me" / "config.yaml"))

    assert code == EXIT_BAD_CONFIG
    assert "пройдите настройку" in capsys.readouterr().err
    assert not (tmp_path / "state.db").exists()


def test_telegram_on_without_env_is_a_config_error(
    tmp_path, monkeypatch, capsys, clean_root_logger
):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "telegram: true\nqueries:\n  - name: Python backend\n    text: python\n",
        encoding="utf-8",
    )
    for name in ("TG_BOT_TOKEN", "TG_CHAT_ID"):
        monkeypatch.delenv(name, raising=False)

    code = run_module.main(_quiet_main_args(tmp_path, config_path))

    assert code == EXIT_BAD_CONFIG
    assert "TG_BOT_TOKEN" in capsys.readouterr().err


def test_default_paths_come_from_me(monkeypatch, tmp_path):
    from hunter import paths

    monkeypatch.setattr(paths, "ME", tmp_path / "me")
    parser = run_module.build_parser()
    args = parser.parse_args([])

    assert args.config == tmp_path / "me" / "config.yaml"
    assert args.env == tmp_path / "me" / ".env"
    assert args.db == tmp_path / "me" / "job-hunter.db"
    assert args.log_dir == tmp_path / "me" / "logs"
    assert args.debug_dir == tmp_path / "me" / "debug"


def test_pass_in_console_mode_sends_one_plain_text_summary():
    store = Store(":memory:")
    sender = Sender()

    code = run_pass(
        config=CONFIG,
        store=store,
        fetch_page=page_returning("hh_search_sorted_with_salary.html"),
        send=sender,
        now=NOW,
        console=True,
    )

    assert code == 0
    (text,) = sender.messages
    assert "https://hh.ru/vacancy/" in text and "<a href" not in text
    assert store._conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0] > 0


def test_echo_prints_console_summary_next_to_telegram_html():
    store = Store(":memory:")
    sender = Sender()
    echoed = []

    code = run_pass(
        config=CONFIG,
        store=store,
        fetch_page=page_returning("hh_search_sorted_with_salary.html"),
        send=sender,
        now=NOW,
        echo=echoed.append,
    )

    assert code == 0
    (summary,) = echoed
    assert "https://hh.ru/vacancy/" in summary and "<a href" not in summary
    assert any("<a href" in text for text in sender.messages), "в Телеграм уходит HTML"
    assert store._conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0] > 0


def test_echo_still_prints_when_telegram_fails_and_marks_nothing():
    store = Store(":memory:")
    echoed = []

    code = run_pass(
        config=CONFIG,
        store=store,
        fetch_page=page_returning("hh_search_sorted_with_salary.html"),
        send=Sender(fail=True),
        now=NOW,
        echo=echoed.append,
    )

    assert code == 1
    assert len(echoed) == 1
    assert store._conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0] == 0


def test_main_with_telegram_prints_summary_and_sends_it(
    tmp_path, monkeypatch, capsys, clean_root_logger
):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "telegram: true\nqueries:\n  - name: Python backend\n    text: python\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        run_module,
        "get_html",
        lambda url, **kwargs: fixture("hh_search_sorted_with_salary.html"),
    )
    sent = []
    monkeypatch.setattr(run_module, "send_messages", lambda token, chat, texts: sent.extend(texts))
    monkeypatch.setenv("TG_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TG_CHAT_ID", "1")

    assert run_module.main(_quiet_main_args(tmp_path, config_path)) == 0

    out = capsys.readouterr().out
    assert "https://hh.ru/vacancy/" in out and "<a href" not in out
    assert any("<a href" in text for text in sent)


# --- проход пишет в корпус ----------------------------------------------------


def _one_vacancy_pass(store, vacancy_id=7, **kwargs):
    return run_pass(
        config=CONFIG,
        store=store,
        fetch_page=lambda url: _page_with([_card(vacancy_id, "Москва")]),
        send=Sender(),
        now=NOW,
        pause=no_pause,
        **kwargs,
    )


def test_new_vacancies_land_in_the_corpus_with_their_query():
    store = Store(":memory:")

    assert _one_vacancy_pass(store) == 0

    assert store.corpus.exists(7)
    (hit,) = store.corpus.hits(7)
    assert hit["role"] == "Python backend"
    assert hit["query"] == "python"


def test_pass_does_not_overwrite_collected_card():
    from tests.factories import make_vacancy

    store = Store(":memory:")
    row = vacancy_card_row(make_vacancy(vacancy_id="7"))
    row["responses_count"] = 36
    row["card_json"] = '{"vacancyId": 7}'
    store.corpus.upsert_card(row)

    _one_vacancy_pass(store)

    after = store.corpus.vacancy(7)
    assert after["responses_count"] == 36
    assert after["card_json"] == '{"vacancyId": 7}'
    assert after["name"] == "Senior Python Developer", "карточка из сбора остаётся нетронутой"


def test_dry_run_writes_nothing_to_the_corpus():
    store = Store(":memory:")

    _one_vacancy_pass(store, dry_run=True)

    assert not store.corpus.exists(7)
    assert store.corpus.query("SELECT count(*) FROM hits")[1] == [(0,)]


def test_vacancy_seen_by_an_earlier_pass_is_not_noted_again():
    store = Store(":memory:")
    _one_vacancy_pass(store)
    first_seen = store.corpus.vacancy(7)["first_seen_at"]

    _one_vacancy_pass(store)

    assert store.corpus.vacancy(7)["first_seen_at"] == first_seen
    assert len(store.corpus.hits(7)) == 1


def test_pass_never_writes_xlsx(tmp_path, monkeypatch, clean_root_logger):
    from hunter import paths

    monkeypatch.setattr(paths, "ME", tmp_path / "me")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "queries:\n  - name: Python backend\n    text: python\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        run_module,
        "get_html",
        lambda url, **kwargs: fixture("hh_search_sorted_with_salary.html"),
    )
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)

    assert run_module.main(_quiet_main_args(tmp_path, config_path)) == 0

    assert list(tmp_path.rglob("*.xlsx")) == []


def test_export_dir_flag_is_gone():
    with pytest.raises(SystemExit):
        run_module.build_parser().parse_args(["--export-dir", "x"])


def test_two_queries_of_one_role_make_one_digest_block():
    store = Store(":memory:")
    config = Config(sources=("hh",), queries=(
        SearchQuery(name="Автор (удалёнка)", role="author", text="python"),
        SearchQuery(name="Автор (текст)", role="author", text="python remote"),
    ))
    sender = Sender()

    code = run_pass(
        config=config,
        store=store,
        fetch_page=lambda url: _scored_page(1, 2),
        send=sender,
        now=NOW,
        pause=no_pause,
    )

    text = "\n".join(sender.messages)
    assert code == 0
    assert text.count("<b>author</b>") == 1
    assert "Автор (удалёнка)" not in text


def test_hits_record_the_role_not_the_query_name():
    store = Store(":memory:")
    config = Config(sources=("hh",), queries=(
        SearchQuery(name="Промо", role="promo", text="smm"),
    ))

    run_pass(
        config=config,
        store=store,
        fetch_page=lambda url: _scored_page(1),
        send=Sender(),
        now=NOW,
        pause=no_pause,
    )

    rows = store.corpus._conn.execute("SELECT role, query FROM hits").fetchall()
    assert [tuple(row) for row in rows] == [("promo", "smm")]
