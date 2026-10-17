"""Где лежат личные данные владельца. Всё личное — в папке me/ проекта
(она в .gitignore), и не зависит от того, откуда запущена команда.

ME читается в момент вызова, а не при импорте: тесты подменяют paths.ME,
а значения по умолчанию в argparse считаются уже в main()."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ME = ROOT / "me"


def db() -> Path:
    return ME / "job-hunter.db"


def config() -> Path:
    return ME / "config.yaml"


def env() -> Path:
    return ME / ".env"


def profile() -> Path:
    return ME / "profile.md"


def logs() -> Path:
    return ME / "logs"


def debug() -> Path:
    return ME / "debug"


def exports() -> Path:
    return ME / "exports"


def applications() -> Path:
    return ME / "applications"
