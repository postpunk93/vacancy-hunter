"""Площадки поиска работы: hh (он же zarplata) и superjob."""

from __future__ import annotations

from .base import Captcha, Card, Detail, Platform, UnknownPlatform
from .hh import Hh

PLATFORMS: dict[str, Platform] = {
    "hh": Hh("hh"),
    "zarplata": Hh("zarplata"),
}


def get(name: str) -> Platform:
    try:
        return PLATFORMS[name]
    except KeyError as exc:
        raise UnknownPlatform(
            f"Неизвестная площадка: {name}. Доступны: {', '.join(PLATFORMS)}"
        ) from exc


__all__ = [
    "PLATFORMS",
    "Captcha",
    "Card",
    "Detail",
    "Platform",
    "UnknownPlatform",
    "get",
]
