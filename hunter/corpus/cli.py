from __future__ import annotations

import argparse
import json
import logging
import random
import re
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from .. import paths
from ..config import ConfigError, load_config
from ..evaluate import evaluate, load_verdicts, rescore
from ..export import export_rows, write_xlsx
from ..fetch import get_html, new_client
from ..models import SearchQuery
from ..scoring import ask_claude, load_criteria
from ..store import Store
from .collect import FetchPage, collect, fetch_details
from .funnel import FUNNEL_COLUMNS, funnel_rows, reminders
from .show import render_table, render_vacancy
from .store import STATUS_LABELS, STATUSES, VERDICTS, Corpus, CorpusError

EXIT_USAGE = 2
GUIDE_PATH = Path(__file__).with_name("GUIDE.md")

EXPERIENCE = ("noExperience", "between1And3", "between3And6", "moreThan6")
WORK_FORMATS = ("remote", "hybrid", "on_site", "field_work")
MAX_PAGES = 40  # hh не отдаёт дальше 2000 вакансий: 40 страниц по 50

_SITES = ("hh.ru", "zarplata.ru")
_VACANCY_PATH = re.compile(r"^/vacancy/(\d+)/?$")

FIRST_RUN = "Первый запуск: me/profile.md нет. Начни настройку (skill setup)."

def parse_vacancy_ref(value: str) -> int:
    """id или ссылка на вакансию hh/zarplata любого поддомена."""
    value = value.strip()
    if value.isdigit():
        return int(value)
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    on_site = any(host == site or host.endswith("." + site) for site in _SITES)
    match = _VACANCY_PATH.match(parsed.path)
    if parsed.scheme in ("http", "https") and on_site and match:
        return int(match.group(1))
    raise ValueError(f"Не похоже на вакансию hh или zarplata: {value!r}")


def _parse_refs(refs: list[str]) -> list[int]:
    # Порядок сохраняем, повторы убираем: Claude может передать один id дважды.
    return list(dict.fromkeys(parse_vacancy_ref(ref) for ref in refs))


def _make_wait(delay: float, sleep):
    def wait() -> None:
        if delay > 0:
            sleep(delay * random.uniform(0.5, 1.5))
    return wait


@contextmanager
def _page_fetcher(fetch_page: FetchPage | None):
    if fetch_page is not None:
        yield fetch_page
        return
    client = new_client()
    try:
        yield lambda url: get_html(url, client=client)
    finally:
        client.close()


def _cmd_init(args, fetch_page, sleep) -> int:
    paths.ME.mkdir(parents=True, exist_ok=True)
    existed = args.db.exists()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    with Corpus.open(args.db, create=True):
        pass
    print(f"База {args.db}: {'уже была, схема проверена' if existed else 'создана'}")
    return 0


def _cmd_collect(args, fetch_page, sleep) -> int:
    if not 1 <= args.pages <= MAX_PAGES:
        print(f"--pages: от 1 до {MAX_PAGES} — дальше hh выдачу не отдаёт", file=sys.stderr)
        return EXIT_USAGE
    query = SearchQuery(
        name=args.role,
        text=args.text,
        area=args.area,
        salary=args.salary,
        experience=tuple(args.experience),
        work_format=args.work_format,
        only_with_salary=args.with_salary,
        search_period=args.days,
        max_pages=args.pages,
    )
    with Corpus.open(args.db) as corpus, _page_fetcher(fetch_page) as fetch:
        report = collect(corpus, fetch, query, args.role, wait=_make_wait(args.delay, sleep))
    print(report.summary())
    return report.exit_code


def _cmd_fetch(args, fetch_page, sleep) -> int:
    try:
        ids = _parse_refs(args.refs)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return EXIT_USAGE
    with Corpus.open(args.db) as corpus, _page_fetcher(fetch_page) as fetch:
        report = fetch_details(
            corpus, fetch, ids,
            role=args.role, refresh=args.refresh, wait=_make_wait(args.delay, sleep),
        )
    print(report.summary())
    return report.exit_code


def _cmd_mark(args, fetch_page, sleep) -> int:
    try:
        vacancy_id = parse_vacancy_ref(args.ref)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return EXIT_USAGE
    with Corpus.open(args.db) as corpus:
        corpus.set_verdict(vacancy_id, args.verdict, args.note)
    print(f"{vacancy_id}: {args.verdict}" + (f" — {args.note}" if args.note else ""))
    return 0


def _cmd_status(args, fetch_page, sleep) -> int:
    try:
        vacancy_id = parse_vacancy_ref(args.ref)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return EXIT_USAGE
    due = None
    if args.due is not None:
        try:
            due = datetime.strptime(args.due, "%Y-%m-%d").date()
        except ValueError:
            print("--due: дата в виде ГГГГ-ММ-ДД, например 2026-10-20", file=sys.stderr)
            return EXIT_USAGE
    with Corpus.open(args.db) as corpus:
        corpus.set_status(vacancy_id, args.status, args.note, test_task=args.test, test_due=due)
    print(f"{vacancy_id}: {STATUS_LABELS[args.status]}" + (f" — {args.note}" if args.note else ""))
    return 0


def _cmd_funnel(args, fetch_page, sleep) -> int:
    with Corpus.open_readonly(args.db) as corpus:
        rows = funnel_rows(corpus)
    if rows:
        print(render_table(FUNNEL_COLUMNS, rows))
    else:
        print("В воронке пока нет вакансий: vacancies status ID selected")
    return 0


def _cmd_todo(args, fetch_page, sleep) -> int:
    # Её зовёт хук начала разговора: ни при каких обстоятельствах не падаем
    # и не создаём базу — нечего напоминать, значит молчим.
    if not paths.profile().exists():
        print(FIRST_RUN)
        return 0
    try:
        with Corpus.open_readonly(args.db) as corpus:
            lines = reminders(corpus, date.today())
    except (CorpusError, sqlite3.Error):
        return 0
    for line in lines:
        print(line)
    return 0


def _cmd_show(args, fetch_page, sleep) -> int:
    try:
        ids = _parse_refs(args.refs)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return EXIT_USAGE
    blocks, missing = [], []
    with Corpus.open(args.db) as corpus:
        for vacancy_id in ids:
            vacancy = corpus.vacancy(vacancy_id)
            if vacancy is None:
                missing.append(vacancy_id)
                continue
            blocks.append(render_vacancy(vacancy, corpus.hits(vacancy_id), corpus.verdict(vacancy_id)))
    if blocks:
        print("\n\n---\n\n".join(blocks))
    for vacancy_id in missing:
        print(f"Вакансии {vacancy_id} нет в базе — vacancies fetch {vacancy_id}", file=sys.stderr)
    return 1 if missing else 0


def _cmd_sql(args, fetch_page, sleep) -> int:
    with Corpus.open_readonly(args.db) as corpus:
        try:
            columns, rows = corpus.query(args.query)
        except sqlite3.Error as exc:
            print(f"Ошибка SQL: {exc}", file=sys.stderr)
            return 1
    if args.json:
        print(json.dumps([dict(zip(columns, row)) for row in rows], ensure_ascii=False, indent=1))
    else:
        print(render_table(columns, rows))
    return 0


def _cmd_export(args, fetch_page, sleep) -> int:
    if args.days is not None and args.days < 1:
        print("--days: число дней, от 1", file=sys.stderr)
        return EXIT_USAGE
    with Corpus.open_readonly(args.db) as corpus:
        rows = export_rows(corpus, args.days)
    if not rows:
        print("Нечего выгружать: в базе нет вакансий за выбранный период — файл не создан")
        return 0
    path = args.out or paths.exports() / f"vacancies-{datetime.now():%Y-%m-%d_%H%M%S}.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_xlsx(rows, path)
    print(f"{path}\nСтрок: {len(rows)}")
    return 0


def _cmd_eval(args, fetch_page, sleep) -> int:
    try:
        config = load_config(args.config)
        if config.scoring is None:
            raise ConfigError(f"{args.config}: нет секции scoring — проверять нечем")
        criteria = load_criteria(config.scoring)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return EXIT_USAGE
    threshold = config.scoring.threshold

    items = load_verdicts(args.source)
    if not items:
        print(f"В {args.source} нет вакансий с вердиктом и полным текстом", file=sys.stderr)
        return 1
    report = evaluate(
        items,
        ask=lambda system, payload: ask_claude(system, payload, model=criteria.model),
        criteria=criteria,
        threshold=threshold,
    )
    if report.unscored == report.total:
        print("Claude не оценил ни одной вакансии: " + "; ".join(report.warnings), file=sys.stderr)
        return 1

    print(f"Всего: {report.total}, не оценено: {report.unscored}")
    print("Средняя оценка по вердиктам владельца:")
    for verdict, mean in report.mean_by_verdict.items():
        print(f"  {verdict}: {mean:.1f}")
    print(
        f"Согласие с порогом {threshold}: {round(report.agreement * report.decided)} "
        f"из {report.decided} ({report.agreement:.0%}); yes — не ниже порога, no — ниже"
    )
    if report.misses:
        print("Промахи:")
        print(render_table(
            ["id", "название", "вердикт", "оценка", "почему"],
            [list(miss) for miss in report.misses],
        ))
    for warning in report.warnings:
        print(f"Внимание: {warning}", file=sys.stderr)
    return 0


def _cmd_rescore(args, fetch_page, sleep) -> int:
    if args.days < 1:
        print("--days: число дней, от 1", file=sys.stderr)
        return EXIT_USAGE
    try:
        config = load_config(args.config)
        if config.scoring is None:
            raise ConfigError(f"{args.config}: нет секции scoring — пересчитывать нечем")
        criteria = load_criteria(config.scoring)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return EXIT_USAGE
    threshold = config.scoring.threshold

    # Store сам создаёт базу и мигрирует схему; пересчёт чужой пустой папки
    # не повод заводить там базу.
    if not args.db.exists():
        print(f"Нет базы {args.db}. Запустите команду в папке поиска работы", file=sys.stderr)
        return EXIT_USAGE
    try:
        with Store(args.db) as store:
            result = rescore(
                store,
                ask=lambda system, payload: ask_claude(system, payload, model=criteria.model),
                criteria=criteria,
                days=args.days,
            )
    except sqlite3.DatabaseError as exc:
        print(f"Файл {args.db} не похож на базу vacancies ({exc})", file=sys.stderr)
        return EXIT_USAGE
    if not result:
        print(f"Нечего пересчитывать: нет вакансий с полным текстом за {args.days} дн.")
        return 0
    if all(new.value is None for _, _, new in result):
        print("Claude не оценил ни одной вакансии: " + result[0][2].why, file=sys.stderr)
        return 1

    # Не оценённые сейчас вакансии в сравнение не входят ни с одной стороны:
    # иначе частичный сбой Claude выглядел бы как падение оценок.
    unscored = [new.why for _, _, new in result if new.value is None]
    was = sum(
        old is not None and old >= threshold for _, old, new in result if new.value is not None
    )
    now = sum(new.value is not None and new.value >= threshold for _, _, new in result)
    skipped = f", не оценено: {len(unscored)}" if unscored else ""
    print(f"Порог {threshold}: было {was}, стало {now} (всего вакансий: {len(result)}{skipped})")

    def line(vacancy, old, new) -> str:
        return f"{'—' if old is None else old} → {new.value} · {vacancy.name} — {new.why}"

    rose, fell, other = [], [], []
    for vacancy, old, new in result:
        if new.value is None or old == new.value:
            continue
        was_up = old is not None and old >= threshold
        is_up = new.value >= threshold
        (rose if is_up and not was_up else fell if was_up and not is_up else other).append(
            line(vacancy, old, new)
        )
    for title, lines in (
        ("Поднялись выше порога:", rose),
        ("Упали ниже порога:", fell),
        ("Остальные изменения:", other),
    ):
        if lines:
            print(f"\n{title}")
            for item in lines:
                print(f"  {item}")
    if not (rose or fell or other):
        print("Оценки не изменились")
    if unscored:
        print(f"Внимание: не оценено вакансий: {len(unscored)} — {unscored[0]}", file=sys.stderr)
    return 0


_COMMANDS = {
    "init": _cmd_init,
    "collect": _cmd_collect,
    "fetch": _cmd_fetch,
    "mark": _cmd_mark,
    "status": _cmd_status,
    "funnel": _cmd_funnel,
    "todo": _cmd_todo,
    "show": _cmd_show,
    "sql": _cmd_sql,
    "export": _cmd_export,
    "eval": _cmd_eval,
    "rescore": _cmd_rescore,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vacancies",
        description="Корпус вакансий hh для анализа в сессии Claude Code",
    )
    parser.add_argument("--db", type=Path, default=paths.db(),
                        help="файл базы (по умолчанию me/job-hunter.db в папке проекта)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="создать папку me/ и базу; существующее не трогает")

    p = sub.add_parser("collect", help="собрать карточки из выдачи hh под роль")
    p.add_argument("--role", required=True, help="метка роли, например backend")
    p.add_argument("--text", required=True, help="поисковый запрос hh (AND, OR, NOT, NAME:)")
    p.add_argument("--area", type=int, default=113, help="113 — Россия, 1 — Москва, 2 — СПб")
    p.add_argument("--days", type=int, choices=(1, 3, 7, 30), default=30)
    p.add_argument("--pages", type=int, default=5, help=f"страниц по 50, от 1 до {MAX_PAGES}")
    p.add_argument("--salary", type=int)
    p.add_argument("--experience", action="append", choices=EXPERIENCE, default=[])
    p.add_argument("--work-format", choices=WORK_FORMATS)
    p.add_argument("--with-salary", action="store_true", help="только с указанной зарплатой")
    p.add_argument("--delay", type=float, default=2.0, help="пауза между запросами, с")

    p = sub.add_parser("fetch", help="скачать полный текст вакансий по id или ссылкам")
    p.add_argument("refs", nargs="+", metavar="ID|URL")
    p.add_argument("--role", help="пометить вакансии ролью (query = manual)")
    p.add_argument("--refresh", action="store_true", help="перекачать уже скачанные")
    p.add_argument("--delay", type=float, default=2.0, help="пауза между запросами, с")

    p = sub.add_parser("mark", help="поставить вердикт вакансии")
    p.add_argument("ref", metavar="ID|URL")
    p.add_argument("verdict", choices=VERDICTS)
    p.add_argument("note", nargs="?", help="комментарий")

    p = sub.add_parser("status", help="поставить этап отклика вакансии")
    p.add_argument("ref", metavar="ID|URL")
    p.add_argument("status", choices=STATUSES)
    p.add_argument("note", nargs="?", help="заметка")
    p.add_argument("--test", help="что просят в тестовом")
    p.add_argument("--due", metavar="ГГГГ-ММ-ДД", help="срок сдачи тестового")

    sub.add_parser("funnel", help="таблица откликов по этапам воронки")
    sub.add_parser("todo", help="о чём напомнить сейчас (для хука начала разговора)")

    p = sub.add_parser("show", help="показать вакансии как Markdown")
    p.add_argument("refs", nargs="+", metavar="ID|URL")

    p = sub.add_parser("sql", help="запрос к базе только на чтение")
    p.add_argument("query")
    p.add_argument("--json", action="store_true", help="полный вывод списком объектов")

    p = sub.add_parser("export", help="выгрузить вакансии из базы в xlsx")
    p.add_argument("--days", type=int, help="только найденные за последние N дней (по умолчанию все)")
    p.add_argument("--out", type=Path,
                   help="куда писать (по умолчанию me/exports/vacancies-ДАТА_ВРЕМЯ.xlsx)")

    p = sub.add_parser("eval", help="сверить оценки Claude с вердиктами владельца в старой базе")
    p.add_argument("--from", dest="source", type=Path, required=True, metavar="PATH",
                   help="база с вердиктами (читается только на чтение)")
    p.add_argument("--config", type=Path, default=paths.config(),
                   help="конфиг с секцией scoring (по умолчанию me/config.yaml)")

    p = sub.add_parser("rescore", help="пересчитать оценки вакансий из базы по новым критериям, без сети")
    p.add_argument("--days", type=int, default=7,
                   help="только найденные за последние N дней (по умолчанию 7)")
    p.add_argument("--config", type=Path, default=paths.config(),
                   help="конфиг с секцией scoring (по умолчанию me/config.yaml)")
    return parser


def main(argv: list[str] | None = None, *, fetch_page: FetchPage | None = None,
         sleep=time.sleep) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    # httpx пишет строку на каждый запрос — это шум в выводе, который читает
    # Claude. Повторы и сбои из get_html (hunter.fetch) остаются видны.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        return _COMMANDS[args.command](args, fetch_page, sleep)
    except CorpusError as exc:
        print(exc, file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
