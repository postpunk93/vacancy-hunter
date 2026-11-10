"""Воронка откликов: что показать в таблице и о чём напомнить."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from ..platforms import format_ref
from ..text import plural
from .store import STATUS_LABELS, STATUSES, Corpus

TEST_SOON_DAYS = 2    # тестовое со сроком не позже сегодня + 2 или уже просроченное
DRAFT_STALE_DAYS = 3  # черновик лежит дольше стольки дней
NO_ANSWER_DAYS = 7    # отклик без ответа дольше стольки дней

# Эти этапы в таблице воронки не показываем: с ними работа закончена.
HIDDEN = ("skipped", "closed")

FUNNEL_COLUMNS = ["id", "название", "компания", "статус", "срок тестового", "заметка"]


def funnel_rows(corpus: Corpus) -> list[tuple]:
    """Вакансии с откликом, кроме закрытых, по порядку этапов воронки."""
    _, rows = corpus.query(
        "SELECT v.id, v.source, v.external_id, v.name, v.company, a.status, a.test_due, a.note "
        "FROM applications a JOIN vacancies v ON v.id = a.vacancy_id "
        "WHERE a.status IS NOT NULL"
    )
    shown = [row for row in rows if row[5] not in HIDDEN]
    shown.sort(key=lambda row: (STATUSES.index(row[5]), row[0]))
    return [
        (format_ref(source, external_id), name, company, STATUS_LABELS[status],
         due or "", note or "")
        for _, source, external_id, name, company, status, due, note in shown
    ]


def _days(count: int) -> str:
    return plural(count, "день", "дня", "дней")


def reminders(corpus: Corpus, today: date) -> list[str]:
    """Что просрочено или горит: тестовые по сроку, затем зависшие
    черновики и отклики без ответа. Пустой список — напоминать не о чем.
    Отсчёт — от последней записи status_history (дата в UTC)."""
    _, rows = corpus.query(
        "SELECT v.id, v.source, v.external_id, v.name, v.company, a.status, a.test_due, "
        "       (SELECT h.at FROM status_history h WHERE h.vacancy_id = a.vacancy_id "
        "        ORDER BY h.id DESC LIMIT 1) "
        "FROM applications a JOIN vacancies v ON v.id = a.vacancy_id "
        "WHERE a.status IN ('test', 'drafted', 'applied')"
    )
    tests, drafts, applied = [], [], []
    for _, source, external_id, name, company, status, due, changed_at in rows:
        tail = f"{name} — {company} (id {format_ref(source, external_id)})"
        if status == "test":
            deadline = _parse_date(due)
            if deadline is None or deadline > today + timedelta(days=TEST_SOON_DAYS):
                continue
            late = (today - deadline).days
            what = (f"Тестовое просрочено на {_days(late)} (срок {deadline})" if late > 0
                    else f"Тестовое: срок {deadline}")
            tests.append((deadline, f"{what} · {tail}"))
            continue
        since = _parse_date(changed_at)
        if since is None:
            continue
        waited = (today - since).days
        if status == "drafted" and waited > DRAFT_STALE_DAYS:
            drafts.append((since, f"Черновик отклика лежит {_days(waited)} · {tail}"))
        elif status == "applied" and waited > NO_ANSWER_DAYS:
            applied.append((since, f"Откликнулся {_days(waited)} назад, ответа нет · {tail}"))
    return [line for group in (tests, drafts, applied) for _, line in sorted(group)]


def _parse_date(value: str | None) -> date | None:
    """Дата из ISO-строки; всё нечитаемое (поле правили руками) — None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None
