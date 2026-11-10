from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from ..platforms.base import Detail

VERDICTS = ("yes", "no", "maybe")

# Воронка откликов, по порядку прохождения.
STATUSES = (
    "selected", "drafted", "test", "applied", "invited", "rejected", "skipped", "closed",
)
STATUS_LABELS = {
    "selected": "выбрана",
    "drafted": "черновик готов",
    "test": "тестовое",
    "applied": "откликнулся",
    "invited": "приглашение",
    "rejected": "отказ",
    "skipped": "пропущена",
    "closed": "закрыта",
}

_STATUS_LIST = ", ".join(f"'{status}'" for status in STATUSES)

# Индекс — номер версии, с которой шаг переводит базу на следующую.
# Менять старые шаги нельзя: у владельца уже есть базы, созданные ими.
_MIGRATIONS = (
    """
    CREATE TABLE vacancies (
      id                INTEGER PRIMARY KEY,
      name              TEXT NOT NULL,
      company           TEXT NOT NULL,
      company_id        INTEGER,
      url               TEXT NOT NULL,
      area              TEXT,
      published_at      TEXT,
      salary_from       INTEGER,
      salary_to         INTEGER,
      currency          TEXT,
      gross             INTEGER,
      salary_frequency  TEXT,
      experience        TEXT,
      work_formats      TEXT,
      employment_form   TEXT,
      employer_trusted  INTEGER,
      employer_category TEXT,
      antifraud_power   REAL,
      responses_count   INTEGER,
      description       TEXT,
      key_skills        TEXT,
      status            TEXT,
      detail_fetched_at TEXT,
      card_json         TEXT,
      detail_json       TEXT,
      first_seen_at     TEXT NOT NULL,
      last_seen_at      TEXT NOT NULL
    );
    CREATE TABLE hits (
      vacancy_id     INTEGER NOT NULL REFERENCES vacancies(id),
      role           TEXT NOT NULL,
      query          TEXT NOT NULL,
      first_found_at TEXT NOT NULL,
      PRIMARY KEY (vacancy_id, role, query)
    );
    CREATE TABLE verdicts (
      vacancy_id INTEGER PRIMARY KEY REFERENCES vacancies(id),
      verdict    TEXT NOT NULL CHECK (verdict IN ('yes','no','maybe')),
      note       TEXT,
      updated_at TEXT NOT NULL
    );
    """,
    # v2: состояние прохода, оценки и воронка откликов — в той же базе.
    f"""
    CREATE TABLE seen (
      key           TEXT PRIMARY KEY,
      first_seen_at TEXT NOT NULL,
      sent_at       TEXT
    );
    CREATE TABLE meta (
      key   TEXT PRIMARY KEY,
      value TEXT NOT NULL
    );
    CREATE TABLE scores (
      key           TEXT NOT NULL,
      criteria_hash TEXT NOT NULL,
      score         INTEGER NOT NULL,
      why           TEXT NOT NULL,
      flags         TEXT NOT NULL,
      scored_at     TEXT NOT NULL,
      PRIMARY KEY (key, criteria_hash)
    );
    CREATE TABLE applications (
      vacancy_id INTEGER PRIMARY KEY REFERENCES vacancies(id),
      status     TEXT NOT NULL CHECK (status IN ({_STATUS_LIST})),
      note       TEXT,
      test_task  TEXT,
      test_due   TEXT,
      updated_at TEXT NOT NULL
    );
    CREATE TABLE status_history (
      id         INTEGER PRIMARY KEY AUTOINCREMENT,
      vacancy_id INTEGER NOT NULL,
      status     TEXT NOT NULL,
      at         TEXT NOT NULL
    );
    CREATE VIEW latest_scores AS
    SELECT vacancy_id, score, why, flags, scored_at FROM (
      SELECT CAST(substr(key, instr(key, ':') + 1) AS INTEGER) AS vacancy_id,
             score, why, flags, scored_at,
             ROW_NUMBER() OVER (
               PARTITION BY CAST(substr(key, instr(key, ':') + 1) AS INTEGER)
               ORDER BY scored_at DESC
             ) AS n
      FROM scores
    ) WHERE n = 1;
    """,
    # v3: заметка без этапа. У вакансии может быть заметка («позвонить в пн»),
    # пока решение по ней ещё не принято, — status становится необязательным.
    f"""
    CREATE TABLE applications_new (
      vacancy_id INTEGER PRIMARY KEY REFERENCES vacancies(id),
      status     TEXT CHECK (status IS NULL OR status IN ({_STATUS_LIST})),
      note       TEXT,
      test_task  TEXT,
      test_due   TEXT,
      updated_at TEXT NOT NULL
    );
    INSERT INTO applications_new (vacancy_id, status, note, test_task, test_due, updated_at)
      SELECT vacancy_id, status, note, test_task, test_due, updated_at FROM applications;
    DROP TABLE applications;
    ALTER TABLE applications_new RENAME TO applications;
    """,
    # v4: площадка и внешний id. id остаётся числовым суррогатным ключом,
    # поэтому hits/verdicts/applications/status_history и их запросы не меняются.
    # Старые строки — вакансии hh, внешний id совпадает с id.
    """
    ALTER TABLE vacancies ADD COLUMN source TEXT NOT NULL DEFAULT 'hh';
    ALTER TABLE vacancies ADD COLUMN external_id TEXT;
    UPDATE vacancies SET external_id = CAST(id AS TEXT);
    CREATE UNIQUE INDEX vacancies_source_external ON vacancies(source, external_id);
    DROP VIEW latest_scores;
    CREATE VIEW latest_scores AS
    SELECT vacancy_id, score, why, flags, scored_at FROM (
      SELECT v.id AS vacancy_id, s.score, s.why, s.flags, s.scored_at,
             ROW_NUMBER() OVER (PARTITION BY v.id ORDER BY s.scored_at DESC) AS n
      FROM scores s JOIN vacancies v ON s.key = v.source || ':' || v.external_id
    ) WHERE n = 1;
    """,
)
SCHEMA_VERSION = len(_MIGRATIONS)

# Колонки, которые приходят из карточки выдачи (или со страницы, если
# карточки не было) и обновляются при каждом повторном сборе.
_CARD_COLUMNS = (
    "name", "company", "company_id", "url", "area", "published_at",
    "salary_from", "salary_to", "currency", "gross", "salary_frequency",
    "experience", "work_formats", "employment_form",
    "employer_trusted", "employer_category", "antifraud_power",
    "responses_count", "card_json",
)


class CorpusError(Exception):
    """База недоступна или несовместима — сообщение для человека."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _migrate(conn: sqlite3.Connection) -> None:
    version = _version(conn)
    if version > SCHEMA_VERSION:
        raise CorpusError(
            f"База создана более новой версией job-hunter (схема v{version}, "
            f"код знает v{SCHEMA_VERSION}). Обновите код."
        )
    for number in range(version, SCHEMA_VERSION):
        # DDL в SQLite транзакционен: миграция применяется целиком или никак.
        # Без этого сбой между DROP TABLE applications и RENAME оставил бы
        # данные в недоступной таблице, а повторный запуск упал бы на CREATE.
        conn.executescript(
            f"BEGIN;\n{_MIGRATIONS[number]}\n"
            f"PRAGMA user_version = {number + 1};\nCOMMIT;"
        )


def _not_a_database(path: Path, exc: sqlite3.DatabaseError) -> CorpusError:
    return CorpusError(f"Файл {path} не похож на базу vacancies ({exc})")


# mode=ro защищает только саму базу: ATTACH открывает (и создаёт) другие
# файлы, VACUUM INTO пишет копию куда угодно. Для анализа хватает чтения.
_READ_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_RECURSIVE,
}


# Аргумент этих PRAGMA — имя таблицы или индекса, а не новое значение.
_SCHEMA_PRAGMAS = {
    "table_info", "table_xinfo", "table_list",
    "index_list", "index_info", "foreign_key_list",
}


def _read_only_authorizer(action, arg1, arg2, db_name, trigger) -> int:
    if action in _READ_ACTIONS:
        return sqlite3.SQLITE_OK
    # PRAGMA без значения (user_version) — чтение; с значением — запись.
    if action == sqlite3.SQLITE_PRAGMA and (arg2 is None or arg1 in _SCHEMA_PRAGMAS):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _missing(path: Path) -> CorpusError:
    return CorpusError(
        f"Нет базы {path}. Запустите команду в папке поиска работы "
        f"или создайте базу: vacancies init"
    )


class Corpus:
    """Корпус вакансий. Каждый метод записи коммитит сразу: прерванный
    сбор продолжается с места остановки, ничего не теряется."""

    def __init__(self, conn: sqlite3.Connection, clock: Callable[[], str] = _now_iso) -> None:
        conn.row_factory = sqlite3.Row
        self._conn = conn
        self._clock = clock

    @classmethod
    def open(cls, path: Path | str, *, create: bool = False,
             clock: Callable[[], str] = _now_iso) -> Corpus:
        path = Path(path)
        # Без явного init не создаём: команда, запущенная не в той папке,
        # иначе молча заведёт там пустую базу.
        if not create and not path.exists():
            raise _missing(path)
        conn = sqlite3.connect(str(path), timeout=30)
        try:
            _migrate(conn)
        except sqlite3.DatabaseError as exc:
            conn.close()
            raise _not_a_database(path, exc) from exc
        except BaseException:
            conn.close()
            raise
        return cls(conn, clock)

    @classmethod
    def open_readonly(cls, path: Path | str) -> Corpus:
        path = Path(path)
        if not path.exists():
            raise _missing(path)
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
        try:
            version = _version(conn)
        except sqlite3.DatabaseError as exc:
            conn.close()
            raise _not_a_database(path, exc) from exc
        if version != SCHEMA_VERSION:
            conn.close()
            raise CorpusError(
                f"Схема базы v{version}, код ждёт v{SCHEMA_VERSION}. "
                f"Обновите схему: vacancies init (данные сохранятся)"
            )
        conn.set_authorizer(_read_only_authorizer)
        return cls(conn)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Corpus:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def exists(self, vacancy_id: int) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM vacancies WHERE id = ?", (vacancy_id,)
        ).fetchone() is not None

    def find(self, source: str, external_id: str) -> int | None:
        """Суррогатный id вакансии по паре (площадка, внешний id)."""
        row = self._conn.execute(
            "SELECT id FROM vacancies WHERE source = ? AND external_id = ?",
            (source, str(external_id)),
        ).fetchone()
        return None if row is None else int(row[0])

    def resolve(self, source: str, external_id: str) -> int:
        vacancy_id = self.find(source, external_id)
        if vacancy_id is None:
            raise CorpusError(
                f"Вакансии {external_id} с площадки {source} нет в базе. "
                f"Сначала: vacancies fetch {external_id}"
            )
        return vacancy_id

    def _row_key(self, row: dict) -> tuple[str, str]:
        """Пара (площадка, внешний id) из строки карточки. Пока не все
        вызывающие перешли на external_id, его подставляет числовой id."""
        source = row.get("source") or "hh"
        external_id = row.get("external_id") or row["id"]
        return str(source), str(external_id)

    def _free_id(self, external_id: str) -> int | None:
        """Суррогатный id новой строки: числовой внешний id, если он свободен.

        Так у вакансий hh id строки совпадает с id вакансии — на этом держатся
        старые базы, ссылки в таблице и запросы владельца. Совпал с чужой
        строкой (другая площадка с тем же числом) — SQLite выдаст следующий.
        """
        if not external_id.isdigit():
            return None
        candidate = int(external_id)
        taken = self._conn.execute(
            "SELECT 1 FROM vacancies WHERE id = ?", (candidate,)
        ).fetchone()
        return None if taken else candidate

    def _write(self, row: dict, *, touch_all: bool) -> tuple[bool, int]:
        source, external_id = self._row_key(row)
        is_new = self.find(source, external_id) is None
        now = self._clock()
        columns = ["source", "external_id", *_CARD_COLUMNS, "first_seen_at", "last_seen_at"]
        values = [source, external_id, *(row[c] for c in _CARD_COLUMNS), now, now]
        vacancy_id = self._free_id(external_id)
        if vacancy_id is not None:
            columns.insert(0, "id")
            values.insert(0, vacancy_id)
        if touch_all:
            updates = ", ".join(f"{c} = excluded.{c}" for c in (*_CARD_COLUMNS, "last_seen_at"))
        else:
            updates = "last_seen_at = excluded.last_seen_at"
        vacancy_id = self._conn.execute(
            f"INSERT INTO vacancies ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' * len(columns))}) "
            f"ON CONFLICT(source, external_id) DO UPDATE SET {updates} "
            f"RETURNING id",
            values,
        ).fetchone()[0]
        self._conn.commit()
        return is_new, int(vacancy_id)

    def upsert_card(self, row: dict) -> tuple[bool, int]:
        return self._write(row, touch_all=True)

    def note_seen(self, row: dict) -> tuple[bool, int]:
        """Проход увидел вакансию: новую заводит, у известной трогает только
        last_seen_at — карточка из сбора богаче строки прохода и остаётся нетронутой."""
        return self._write(row, touch_all=False)

    def add_hit(self, vacancy_id: int, role: str, query: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO hits (vacancy_id, role, query, first_found_at) "
            "VALUES (?, ?, ?, ?)",
            (vacancy_id, role, query, self._clock()),
        )
        self._conn.commit()

    def has_detail(self, vacancy_id: int) -> bool:
        row = self._conn.execute(
            "SELECT detail_fetched_at IS NOT NULL OR status = 'deleted' "
            "FROM vacancies WHERE id = ?",
            (vacancy_id,),
        ).fetchone()
        return bool(row and row[0])

    def save_detail(self, detail: Detail) -> None:
        # Поля карточки у существующей строки не трогаем: выдача — основной
        # источник метаданных, страница лишь дополняет текстом.
        vacancy_id = self.find(detail.source, detail.external_id)
        if vacancy_id is None:
            vacancy_id = self.upsert_card(detail.row)[1]
        self._conn.execute(
            "UPDATE vacancies SET description = ?, key_skills = ?, status = ?, "
            "detail_json = ?, detail_fetched_at = ? WHERE id = ?",
            (
                detail.description,
                json.dumps(detail.key_skills, ensure_ascii=False),
                detail.status,
                json.dumps(detail.raw, ensure_ascii=False),
                self._clock(),
                vacancy_id,
            ),
        )
        self._conn.commit()

    def mark_deleted(self, vacancy_id: int) -> bool:
        cursor = self._conn.execute(
            "UPDATE vacancies SET status = 'deleted', detail_fetched_at = ? WHERE id = ?",
            (self._clock(), vacancy_id),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def set_verdict(self, vacancy_id: int, verdict: str, note: str | None = None) -> None:
        if verdict not in VERDICTS:
            raise CorpusError(f"Вердикт должен быть одним из: {', '.join(VERDICTS)}")
        if not self.exists(vacancy_id):
            raise CorpusError(
                f"Вакансии {vacancy_id} нет в базе. Сначала: vacancies fetch {vacancy_id}"
            )
        self._conn.execute(
            "INSERT INTO verdicts (vacancy_id, verdict, note, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(vacancy_id) DO UPDATE SET verdict = excluded.verdict, "
            "note = excluded.note, updated_at = excluded.updated_at",
            (vacancy_id, verdict, note, self._clock()),
        )
        self._conn.commit()

    def set_status(self, vacancy_id: int, status: str | None, note: str | None = None, *,
                   test_task: str | None = None, test_due: date | None = None) -> None:
        """Этап отклика. None в status — этап не менять (сохранить одну заметку),
        None в note/test_* — оставить прежнее значение. В историю пишется только
        смена этапа: повторный вызов с тем же статусом (правка заметки или срока)
        не сдвигает отсчёт напоминаний."""
        if status is not None and status not in STATUSES:
            raise CorpusError(f"Статус должен быть одним из: {', '.join(STATUSES)}")
        if not self.exists(vacancy_id):
            raise CorpusError(
                f"Вакансии {vacancy_id} нет в базе. Сначала: vacancies fetch {vacancy_id}"
            )
        now = self._clock()
        previous = self.application(vacancy_id)
        if status is None and previous is not None:
            status = previous["status"]
        self._conn.execute(
            "INSERT INTO applications (vacancy_id, status, note, test_task, test_due, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(vacancy_id) DO UPDATE SET status = excluded.status, "
            "note = COALESCE(excluded.note, note), "
            "test_task = COALESCE(excluded.test_task, test_task), "
            "test_due = COALESCE(excluded.test_due, test_due), "
            "updated_at = excluded.updated_at",
            (vacancy_id, status, note, test_task,
             test_due.isoformat() if test_due else None, now),
        )
        if status is not None and (previous is None or previous["status"] != status):
            self._conn.execute(
                "INSERT INTO status_history (vacancy_id, status, at) VALUES (?, ?, ?)",
                (vacancy_id, status, now),
            )
        self._conn.commit()

    def application(self, vacancy_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT status, note, test_task, test_due, updated_at "
            "FROM applications WHERE vacancy_id = ?",
            (vacancy_id,),
        ).fetchone()

    def vacancy(self, vacancy_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM vacancies WHERE id = ?", (vacancy_id,)
        ).fetchone()

    def hits(self, vacancy_id: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT role, query, first_found_at FROM hits WHERE vacancy_id = ? "
            "ORDER BY role, query",
            (vacancy_id,),
        ).fetchall()

    def verdict(self, vacancy_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT verdict, note, updated_at FROM verdicts WHERE vacancy_id = ?",
            (vacancy_id,),
        ).fetchone()

    def query(self, sql: str) -> tuple[list[str], list[tuple]]:
        cursor = self._conn.execute(sql)
        columns = [d[0] for d in cursor.description or []]
        return columns, [tuple(row) for row in cursor.fetchall()]
