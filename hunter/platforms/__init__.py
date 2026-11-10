"""Площадки поиска работы: hh (он же zarplata) и superjob."""

from __future__ import annotations

from .base import Captcha, Card, Detail, Platform, UnknownPlatform
from .hh import Hh
from .superjob import Superjob

PLATFORMS: dict[str, Platform] = {
    "hh": Hh("hh"),
    "zarplata": Hh("zarplata"),
    "superjob": Superjob(),
}


def get(name: str) -> Platform:
    try:
        return PLATFORMS[name]
    except KeyError as exc:
        raise UnknownPlatform(
            f"Неизвестная площадка: {name}. Доступны: {', '.join(PLATFORMS)}"
        ) from exc


# Площадки на движке hh делят пространство id: ссылка на zarplata ведёт
# на ту же вакансию, что и на hh, и страница у неё одна.
_HH_ENGINE = frozenset({"hh", "zarplata"})


def parse_ref(value: str) -> tuple[str, str]:
    """Ссылка на вакансию → пара (площадка, внешний id).

    Формы: `139289794` (это hh), `superjob:52295791`, ссылка на любую площадку.
    """
    value = value.strip()
    if value.isdigit():
        return ("hh", value)
    source, sep, external_id = value.partition(":")
    if sep and source in PLATFORMS and external_id.isdigit():
        return (source, external_id)
    for name, platform in PLATFORMS.items():
        found = platform.parse_ref(value)
        if found is not None:
            return ("hh" if name in _HH_ENGINE else name, found)
    raise ValueError(f"Не похоже на вакансию: {value!r}")


def format_ref(source: str, external_id: str) -> str:
    """Обратное к parse_ref: у hh — голый id, у остальных — `площадка:id`."""
    if source == "hh":
        return str(external_id)
    return f"{source}:{external_id}"


__all__ = [
    "PLATFORMS",
    "format_ref",
    "parse_ref",
    "Captcha",
    "Card",
    "Detail",
    "Platform",
    "UnknownPlatform",
    "get",
]
