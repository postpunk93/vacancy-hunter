"""Проверка оценки на вердиктах владельца: насколько оценки Claude совпадают
с тем, что владелец сам решил про вакансии (yes/no). Базу с вердиктами не
трогаем: читаем её напрямую на чтение, оценки кладём в память."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .corpus.store import CorpusError
from .models import FullText, Score, Vacancy
from .scoring import Ask, Criteria, score_vacancies
from .store import Store

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_VERDICT_ORDER = ("yes", "no", "maybe")


@dataclass(frozen=True, slots=True)
class Report:
    total: int
    mean_by_verdict: dict[str, float]
    # Доля оценённых yes/no, где Claude согласен с владельцем: yes ≥ порога,
    # no < порога. maybe не считается. decided — сколько вакансий в знаменателе.
    agreement: float
    decided: int
    # (id, название, вердикт, оценка, why): yes ниже порога и no не ниже порога.
    misses: list[tuple[int, str, str, int, str]]
    unscored: int
    warnings: list[str] = field(default_factory=list)


def _published(value: object) -> datetime:
    if not isinstance(value, str):
        return _EPOCH
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return _EPOCH
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _json_list(value: object) -> tuple[str, ...]:
    try:
        data = json.loads(value) if isinstance(value, str) else None
    except json.JSONDecodeError:
        return ()
    return tuple(str(item) for item in data) if isinstance(data, list) else ()


def vacancy_from_row(row: Mapping) -> Vacancy:
    """Строка таблицы vacancies → вакансия для оценки. Чего в корпусе нет,
    оценке не нужно: зарплатный период и признак рекламы — пустые."""
    gross = row["gross"]
    return Vacancy(
        source="hh",
        vacancy_id=str(row["id"]),
        name=row["name"],
        company=row["company"],
        url=row["url"],
        area=row["area"] or "",
        published_at=_published(row["published_at"]),
        salary_from=row["salary_from"],
        salary_to=row["salary_to"],
        currency=row["currency"],
        gross=None if gross is None else bool(gross),
        salary_mode=None,
        work_formats=_json_list(row["work_formats"]),
        experience=row["experience"],
        is_ad=False,
    )


def load_verdicts(path: Path) -> list[tuple[Vacancy, FullText, str]]:
    """Вакансии с вердиктом владельца и полным текстом. Старая база (схема v1)
    не проходит через Corpus.open_readonly — он требует текущую схему, —
    поэтому открываем напрямую и только на чтение."""
    path = Path(path)
    if not path.exists():
        raise CorpusError(f"Нет базы {path}")
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT v.*, d.verdict FROM vacancies v JOIN verdicts d ON d.vacancy_id = v.id "
            "WHERE v.description IS NOT NULL ORDER BY v.id"
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        raise CorpusError(f"Файл {path} не похож на базу vacancies ({exc})") from exc
    finally:
        conn.close()
    return [
        (
            vacancy_from_row(row),
            FullText(description=row["description"], key_skills=_json_list(row["key_skills"])),
            row["verdict"],
        )
        for row in rows
    ]


def evaluate(
    items: list[tuple[Vacancy, FullText, str]],
    *,
    ask: Ask,
    criteria: Criteria,
    threshold: int,
) -> Report:
    vacancies = [vacancy for vacancy, _, _ in items]
    texts = {vacancy.key: text for vacancy, text, _ in items}
    # Кеш оценок — в памяти: проверка не должна ничего писать в чужую базу.
    with Store(":memory:") as store:
        scores, warnings = score_vacancies(
            vacancies, texts, ask=ask, criteria=criteria, store=store
        )

    by_verdict: dict[str, list[int]] = {}
    misses: list[tuple[int, str, str, int, str]] = []
    agreed = decided = unscored = 0
    for vacancy, _, verdict in items:
        score = scores[vacancy.key]
        if score.value is None:
            unscored += 1
            continue
        by_verdict.setdefault(verdict, []).append(score.value)
        if verdict not in ("yes", "no"):
            continue
        decided += 1
        if (score.value >= threshold) == (verdict == "yes"):
            agreed += 1
        else:
            misses.append((int(vacancy.vacancy_id), vacancy.name, verdict, score.value, score.why))

    means = {
        verdict: sum(by_verdict[verdict]) / len(by_verdict[verdict])
        for verdict in _VERDICT_ORDER if verdict in by_verdict
    }
    return Report(
        total=len(items),
        mean_by_verdict=means,
        agreement=agreed / decided if decided else 0.0,
        decided=decided,
        misses=misses,
        unscored=unscored,
        warnings=warnings,
    )


def rescore(
    store: Store, *, ask: Ask, criteria: Criteria, days: int
) -> list[tuple[Vacancy, int | None, Score]]:
    """Вакансии корпуса, найденные за days дней и с полным текстом, оценённые
    заново после правки критериев. Прежняя оценка — последняя в базе до
    пересчёта (None — вакансию не оценивали). Сеть не нужна: тексты берутся
    из базы, а новые оценки ложатся в кеш под новым хешем критериев."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = store.corpus._conn.execute(
        "SELECT v.*, s.score AS old_score FROM vacancies v "
        "LEFT JOIN latest_scores s ON s.vacancy_id = v.id "
        "WHERE v.first_seen_at >= ? AND v.description IS NOT NULL "
        "ORDER BY v.first_seen_at, v.id",
        (cutoff,),
    ).fetchall()
    vacancies = [vacancy_from_row(row) for row in rows]
    old = {vacancy.key: row["old_score"] for vacancy, row in zip(vacancies, rows)}
    texts = {}
    for vacancy in vacancies:
        text = store.get_text(vacancy.key)
        if text is not None:
            texts[vacancy.key] = text
    scores, _ = score_vacancies(vacancies, texts, ask=ask, criteria=criteria, store=store)
    return [(vacancy, old[vacancy.key], scores[vacancy.key]) for vacancy in vacancies]
