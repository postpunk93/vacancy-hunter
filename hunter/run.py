from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from . import paths
from .config import (
    MAX_PERIOD_DAYS,
    Config,
    ConfigError,
    load_config,
    load_secrets,
    with_search_period,
)
from .corpus.extract import vacancy_card_row
from .digest import render, render_console
from .fetch import Blocked, FetchError, get_html, new_client
from .filters import (
    Group,
    dedupe,
    drop_ads,
    drop_published_before,
    drop_without_salary,
    sort_by_freshness,
    sort_by_score,
)
from .models import Score, SearchQuery, Vacancy
from .parse import ParseError, parse_search_page
from .scoring import Criteria, ask_model, load_criteria, score_pass
from .sources import build_url, hh_period
from .store import Store
from .telegram import TelegramError, send_messages

log = logging.getLogger("hunter")

PURGE_DAYS = 90
REQUEST_PAUSE_SECONDS = 1.5
# Столько карточек на странице выдачи (items_on_page в sources.build_url).
# Страница короче — значит, выдача кончилась и листать дальше незачем.
PAGE_SIZE = 50

EXIT_OK = 0
EXIT_TELEGRAM_FAILED = 1
EXIT_BAD_CONFIG = 2
EXIT_BLOCKED = 3
EXIT_UNEXPECTED = 4


def _collect(
    config: Config,
    fetch_page,
    debug_dir: Path | None,
    pause,
) -> tuple[list[Group], list[str], bool]:
    """Обходит все запросы × источники. Возвращает группы, предупреждения
    и признак полной блокировки (заблокированы все настроенные источники)."""
    groups: list[Group] = []
    warnings: list[str] = []
    blocked_sources: set[str] = set()
    first_request = True

    for query in config.queries:
        collected = []
        for source in config.sources:
            if source in blocked_sources:
                continue
            for page in range(query.max_pages):
                if not first_request:
                    pause(REQUEST_PAUSE_SECONDS)
                first_request = False

                url = build_url(source, query, page=page)
                try:
                    page_html = fetch_page(url)
                except Blocked as exc:
                    log.error("Блокировка (%s): %s", source, exc)
                    blocked_sources.add(source)
                    warnings.append(f"{source} заблокировал запросы: {exc}")
                    break
                except FetchError as exc:
                    log.warning("Запрос не выполнен: %s", exc)
                    warnings.append(f"запрос «{query.name}» ({source}) не выполнен")
                    continue

                try:
                    found = parse_search_page(page_html, source)
                except ParseError as exc:
                    log.error("Разбор не удался: %s", exc)
                    if debug_dir is not None:
                        debug_dir.mkdir(parents=True, exist_ok=True)
                        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                        target = debug_dir / f"{source}-{stamp}.html"
                        target.write_text(page_html, encoding="utf-8")
                        log.error("Страница сохранена в %s", target)
                    warnings.append(
                        f"разбор выдачи «{query.name}» ({source}) не удался — "
                        f"похоже, сайт сменил вёрстку"
                    )
                    break
                collected.extend(found)
                if len(found) < PAGE_SIZE:
                    break

        groups.append((query.name, collected))

    all_blocked = bool(config.sources) and blocked_sources >= set(config.sources)
    return groups, warnings, all_blocked


def run_pass(
    *,
    config: Config,
    store: Store,
    fetch_page,
    send,
    now: datetime,
    debug_dir: Path | None = None,
    pause=time.sleep,
    dry_run: bool = False,
    score: Callable[[list[Vacancy]], tuple[dict[str, Score], list[str]]] | None = None,
    threshold: int = 5,
    console: bool = False,
    echo: Callable[[str], None] | None = None,
) -> int:
    """Один проход. Всё внешнее подаётся аргументами, поэтому тестируется
    без сети и без Телеграма. При dry_run=True вакансии не помечаются
    виденными и не получают hits, но тексты и оценки кэшируются
    (fetch_texts кладёт вакансию в корпус). С score каждая новая
    вакансия получает оценку; в Телеграм идут прошедшие порог threshold
    и те, чью оценку нельзя считать надёжной (нет оценки или по карточке).
    С console=True вместо HTML-сообщений Телеграма send получает одну
    текстовую сводку; вакансии после неё помечаются так же, как после отправки.
    echo получает ту же текстовую сводку, когда сообщения идут в Телеграм:
    запустившему проход (и навыку collect) она нужна на экране в любом случае."""

    def mark_seen(vacancies, *, sent):
        if not dry_run:
            store.mark_seen(vacancies, sent=sent)

    if not dry_run:
        # Иначе --dry-run удаляет записи старше 90 дней (и создаёт файл базы,
        # если его не было) — README обещает, что dry-run состояние не трогает.
        removed = store.purge_older_than(PURGE_DAYS)
        if removed:
            log.info("Удалено устаревших записей: %d", removed)

    groups, warnings, all_blocked = _collect(config, fetch_page, debug_dir, pause)

    if all_blocked:
        blocked_console = render_console(
            [], scores=None, below=0, threshold=threshold, warnings=tuple(warnings)
        )
        if console:
            blocked_message = [blocked_console]
        else:
            blocked_message = render([], now, warnings=tuple(warnings))
            if echo is not None:
                echo(blocked_console)
        try:
            send(blocked_message)
        except TelegramError as exc:
            log.error("Предупреждение не отправлено: %s", exc)
            return EXIT_TELEGRAM_FAILED
        return EXIT_BLOCKED

    cleaned: list[Group] = []
    for query, (name, vacancies) in zip(config.queries, groups):
        vacancies = drop_ads(vacancies)
        if hh_period(query.search_period) != query.search_period:
            vacancies = drop_published_before(
                vacancies, now - timedelta(days=query.search_period)
            )
        if query.only_with_salary:
            # Страховка на случай, если hh перестанет уважать параметр —
            # но только когда пользователь сам просил только вакансии
            # с зарплатой; иначе это тихо переопределяет его выбор.
            vacancies = drop_without_salary(vacancies)
        cleaned.append((name, sort_by_freshness(vacancies)))
    cleaned, duplicates = dedupe(cleaned)
    # Вакансии-клоны в разных городах не должны всплывать по отдельности
    # в следующем проходе — представитель уже несёт объединённый список
    # городов, поглощённые в дайджест не попадают вовсе.
    mark_seen(duplicates, sent=False)

    fresh: list[Group] = [(name, store.unseen(vs)) for name, vs in cleaned]
    fresh = [(name, vs) for name, vs in fresh if vs]
    total_new = sum(len(vs) for _, vs in fresh)
    if not dry_run:
        # Корпус пополняется до отправки: сбой Телеграма вакансии не теряет,
        # а повторный проход ничего не задвоит (note_seen и add_hit идемпотентны).
        _note_in_corpus(store, fresh, {q.name: q for q in config.queries})
    fresh = _merge_by_role(fresh, config.queries)

    log.info(
        "Проход завершён: запросов %d, новых вакансий %d, предупреждений %d",
        len(config.queries),
        total_new,
        len(warnings),
    )

    scores: dict[str, Score] | None = None
    digest_groups = fresh
    below: list[Vacancy] = []
    if score is not None and fresh:
        new = [v for _, vs in fresh for v in vs]
        try:
            scores, score_warnings = score(new)
        except Exception as exc:
            # Сбой оценки стоит оценок, а не вакансий: дайджест уходит без них.
            log.exception("Оценка упала")
            reason = f"оценка упала: {exc.__class__.__name__}: {exc}"
            scores = {v.key: Score(None, reason) for v in new}
            score_warnings = [reason]
        warnings.extend(score_warnings)
        fresh = [(name, sort_by_score(vs, scores)) for name, vs in fresh]
        digest_groups = []
        for name, vacancies in fresh:
            passed = [v for v in vacancies if _passes(scores.get(v.key), threshold)]
            below.extend(v for v in vacancies if v not in passed)
            if passed:
                digest_groups.append((name, passed))

    if console or echo is not None:
        # Тишина после прохода непонятна тому, кто его запустил: сводка
        # печатается и когда новых вакансий нет.
        summary = render_console(
            digest_groups, scores=scores, below=len(below),
            threshold=threshold, warnings=tuple(warnings),
        )
    if console:
        messages = [summary]
    else:
        if echo is not None:
            echo(summary)
        messages = render(
            digest_groups, now, warnings=tuple(warnings),
            scores=scores, below=len(below), threshold=threshold,
        )
        if not messages:
            return EXIT_OK

    try:
        send(messages)
    except TelegramError as exc:
        log.error("Отправка не удалась, вакансии остаются новыми: %s", exc)
        return EXIT_TELEGRAM_FAILED

    if dry_run and below:
        print(f"Ниже порога {threshold}:")
        for vacancy in below:
            item = scores[vacancy.key]
            print(f"  {item.value} · {vacancy.name} — {item.why}")

    mark_seen([v for _, vs in digest_groups for v in vs], sent=True)
    mark_seen(below, sent=False)

    return EXIT_OK


def _merge_by_role(groups: list[Group], queries: tuple[SearchQuery, ...]) -> list[Group]:
    """У роли два запроса — обычный и текстовый про удалёнку. В дайджесте
    должен быть один блок, поэтому группы склеиваются по роли."""
    role_by_name = {q.name: q.role for q in queries}
    order = list(dict.fromkeys(q.role for q in queries))
    by_role: dict[str, list[Vacancy]] = {role: [] for role in order}
    for name, vacancies in groups:
        by_role[role_by_name[name]].extend(vacancies)
    return [(role, by_role[role]) for role in order if by_role[role]]


def _note_in_corpus(
    store: Store, fresh: list[Group], queries: dict[str, SearchQuery]
) -> None:
    """Каждая новая вакансия — в vacancies, а запрос, по которому она нашлась, —
    в hits: role — роль для дайджеста и статистики, query — конкретный текст."""
    for name, vacancies in fresh:
        query = queries[name]
        for vacancy in vacancies:
            row = vacancy_card_row(vacancy)
            store.corpus.note_seen(row)
            store.corpus.add_hit(row["id"], query.role, query.text)


def _passes(score: Score | None, threshold: int) -> bool:
    """Оценка по одной карточке ненадёжна: слабое название легко уронит
    хорошую вакансию. Такие и неоценённые идут в Телеграм в любом случае."""
    if score is None or not score.scored or score.from_card:
        return True
    return score.value >= threshold


def _use_utf8_console() -> None:
    """Консоль Windows по умолчанию в cp1251: ₽, тире и эмодзи в неё не влезают,
    и печать дайджеста падает с UnicodeEncodeError."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _setup_logging(log_dir: Path) -> list[logging.Handler]:
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_dir / "hunter.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    added: list[logging.Handler] = [handler]
    root.addHandler(handler)
    # Под pythonw.exe sys.stderr — None: обработчик логов сам не упадёт при
    # создании, но каждый вызов emit() будет бросать исключение изнутри
    # logging. Файловый обработчик работает в любом случае, ничего не теряется.
    if sys.stderr is not None:
        stream_handler = logging.StreamHandler(sys.stderr)
        root.addHandler(stream_handler)
        added.append(stream_handler)
    # httpx логирует каждый запрос на INFO целиком, с URL — а URL отправки
    # в Телеграм содержит токен бота в пути. Корневой логгер стоит на INFO
    # (нужно для наших собственных сообщений), поэтому без этой строчки
    # токен в открытом виде попадает в hunter.log при первой же отправке.
    # httpcore логирует свои запросы на DEBUG, так что его трогать не нужно.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return added


def build_parser() -> argparse.ArgumentParser:
    # Пути по умолчанию считаются здесь, а не при импорте: paths.ME можно подменить.
    parser = argparse.ArgumentParser(
        description="Собрать новые вакансии и показать сводку (в Телеграм — если он включён в конфиге)"
    )
    parser.add_argument("--config", type=Path, default=paths.config())
    parser.add_argument("--env", type=Path, default=paths.env())
    parser.add_argument("--db", type=Path, default=paths.db())
    parser.add_argument("--log-dir", type=Path, default=paths.logs())
    parser.add_argument("--debug-dir", type=Path, default=paths.debug())
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "показать сводку в консоли, ничего не отправлять в Телеграм "
            "и не помечать вакансии виденными"
        ),
    )
    parser.add_argument(
        "--days",
        type=int,
        help=(
            "за сколько дней смотреть выдачу, перебивает search_period "
            f"из конфига (1–{MAX_PERIOD_DAYS})"
        ),
    )
    parser.add_argument(
        "--no-score",
        action="store_true",
        help="не скачивать тексты и не оценивать вакансии в этом запуске",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    _use_utf8_console()
    _setup_logging(args.log_dir)

    try:
        config = load_config(args.config)
        if args.days is not None:
            if not 1 <= args.days <= MAX_PERIOD_DAYS:
                raise ConfigError(f"--days — число дней от 1 до {MAX_PERIOD_DAYS}, получено: {args.days}")
            config = with_search_period(config, args.days)
        criteria: Criteria | None = None
        if config.scoring is not None and not args.no_score:
            criteria = load_criteria(config.scoring)
        token, chat_id = ("", "")
        if config.telegram and not args.dry_run:
            token, chat_id = load_secrets(args.env)
    except ConfigError as exc:
        log.error("%s", exc)
        return EXIT_BAD_CONFIG

    try:
        client = new_client()
        try:
            # Сводка всегда печатается. Без Телеграма (выключен или --dry-run)
            # «отправка» и есть печать; с Телеграмом он получает ещё и HTML.
            to_console = args.dry_run or not config.telegram
            if to_console:
                def send(texts):
                    for text in texts:
                        print(text)
            else:
                def send(texts):
                    send_messages(token, chat_id, texts)

            def fetch_page(url):
                return get_html(url, client=client)

            args.db.parent.mkdir(parents=True, exist_ok=True)
            with Store(args.db) as store:
                score = None
                if criteria is not None:
                    def score(vacancies):
                        return score_pass(
                            vacancies,
                            fetch_page=fetch_page,
                            store=store,
                            pause=time.sleep,
                            ask=lambda system, payload: ask_model(
                                system, payload, criteria=criteria
                            ),
                            criteria=criteria,
                        )

                return run_pass(
                    config=config,
                    store=store,
                    fetch_page=fetch_page,
                    send=send,
                    now=datetime.now(timezone.utc).astimezone(),
                    debug_dir=args.debug_dir,
                    dry_run=args.dry_run,
                    score=score,
                    threshold=config.scoring.threshold if config.scoring else 5,
                    console=to_console,
                    echo=None if to_console else print,
                )
        finally:
            client.close()
    except Exception:
        # pythonw.exe не имеет stderr: без этого перехвата необработанное
        # исключение (битая state.db, запертая другим процессом) уходит
        # в никуда — ни строчки в логе, ни следа для пользователя.
        log.exception("Проход прерван непредвиденной ошибкой")
        return EXIT_UNEXPECTED


if __name__ == "__main__":
    raise SystemExit(main())
