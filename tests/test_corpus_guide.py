import argparse
import sqlite3

from hunter.corpus.cli import GUIDE_PATH, build_parser
from hunter.corpus.store import Corpus


def guide():
    return GUIDE_PATH.read_text(encoding="utf-8")


def test_every_subcommand_is_documented():
    sub = next(a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    missing = [name for name in sub.choices if f"vacancies {name}" not in guide()]
    assert not missing, f"в GUIDE.md не описаны подкоманды: {missing}"


def test_every_column_is_documented(tmp_path):
    Corpus.open(tmp_path / "v.db", create=True).close()
    conn = sqlite3.connect(tmp_path / "v.db")
    text = guide()
    missing = [
        f"{table}.{column}"
        for table in (
            "vacancies", "hits", "verdicts",
            "seen", "meta", "scores", "applications", "status_history", "latest_scores",
        )
        for (_, column, *_rest) in conn.execute(f"PRAGMA table_info({table})")
        if f"`{column}`" not in text
    ]
    assert not missing, f"в GUIDE.md не описаны колонки: {missing}"
