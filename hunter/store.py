from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .corpus.store import Corpus, _migrate
from .models import FullText, Score, Vacancy


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Store:
    """Состояние между запусками: что уже отправляли, служебные отметки времени,
    кеш оценок. База та же, что у корпуса, соединение общее (`corpus`);
    полные тексты живут только в vacancies.description."""

    def __init__(self, path: Path | str) -> None:
        # timeout=30: --dry-run может запуститься, пока плановый проход ещё
        # держит базу открытой; без этого sqlite3 роняет OperationalError
        # немедленно вместо того, чтобы подождать освобождения блокировки.
        self._conn = sqlite3.connect(str(path), timeout=30)
        try:
            _migrate(self._conn)
        except BaseException:
            self._conn.close()
            raise
        self.corpus = Corpus(self._conn)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def is_empty(self) -> bool:
        return self._conn.execute("SELECT 1 FROM seen LIMIT 1").fetchone() is None

    def unseen(self, vacancies: list[Vacancy]) -> list[Vacancy]:
        known = {row[0] for row in self._conn.execute("SELECT key FROM seen")}
        return [v for v in vacancies if v.key not in known]

    def mark_seen(self, vacancies: list[Vacancy], *, sent: bool) -> None:
        now = _now().isoformat()
        self._conn.executemany(
            "INSERT INTO seen (key, first_seen_at, sent_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "sent_at = COALESCE(seen.sent_at, excluded.sent_at)",
            [(v.key, now, now if sent else None) for v in vacancies],
        )
        self._conn.commit()

    def purge_older_than(self, days: int) -> int:
        """Вакансия, снятая и переопубликованная спустя квартал, считается новой,
        а база не растёт бесконечно."""
        cutoff = (_now() - timedelta(days=days)).isoformat()
        removed = 0
        for statement in (
            "DELETE FROM seen WHERE first_seen_at < ?",
            "DELETE FROM scores WHERE scored_at < ?",
        ):
            removed += self._conn.execute(statement, (cutoff,)).rowcount
        self._conn.commit()
        return removed

    def get_text(self, key: str) -> FullText | None:
        # id у zarplata те же, что у hh (кросспостинг): префикс источника не нужен.
        try:
            vacancy_id = int(key.split(":", 1)[1])
        except (IndexError, ValueError):
            return None
        row = self._conn.execute(
            "SELECT description, key_skills FROM vacancies "
            "WHERE id = ? AND description IS NOT NULL",
            (vacancy_id,),
        ).fetchone()
        if row is None:
            return None
        return FullText(row[0], tuple(json.loads(row[1] or "[]")))

    def get_score(self, key: str, criteria_hash: str) -> Score | None:
        """Оценка действительна, только пока не менялись критерии: хеш входит в ключ."""
        row = self._conn.execute(
            "SELECT score, why, flags FROM scores WHERE key = ? AND criteria_hash = ?",
            (key, criteria_hash),
        ).fetchone()
        if row is None:
            return None
        return Score(row[0], row[1], tuple(json.loads(row[2])))

    def save_score(self, key: str, criteria_hash: str, score: Score) -> None:
        # Неоценённую и оценённую по карточке нужно пробовать снова, а не помнить.
        if not score.scored or score.from_card:
            raise ValueError("кешируются только оценки по полному тексту")
        self._conn.execute(
            "INSERT INTO scores (key, criteria_hash, score, why, flags, scored_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(key, criteria_hash) DO UPDATE SET "
            "score = excluded.score, why = excluded.why, flags = excluded.flags, "
            "scored_at = excluded.scored_at",
            (key, criteria_hash, score.value, score.why,
             json.dumps(list(score.flags), ensure_ascii=False), _now().isoformat()),
        )
        self._conn.commit()

    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        self._conn.commit()
