from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from .models import SearchQuery

KNOWN_SOURCES = ("hh", "zarplata")

_QUERY_FIELDS = {
    "name",
    "text",
    "role",
    "area",
    "salary",
    "experience",
    "work_format",
    "only_with_salary",
    "search_period",
    "max_pages",
}


MAX_PERIOD_DAYS = 30

_SCORING_FIELDS = {"enabled", "profile", "threshold", "backend", "model"}
_OLD_SCORING_FIELDS = ("portrait", "conflict")

_BACKENDS = ("opencode", "claude")
_DEFAULT_MODELS = {"opencode": "opencode/big-pickle", "claude": "sonnet"}


class ConfigError(Exception):
    """Конфигурация непригодна. Текст исключения показывается пользователю."""


@dataclass(frozen=True, slots=True)
class ScoringConfig:
    """Оценка вакансий: файл профиля, порог для Телеграма, бэкенд и модель."""

    profile: Path
    threshold: int = 5
    backend: str = "opencode"
    model: str = "opencode/big-pickle"


@dataclass(frozen=True, slots=True)
class Config:
    sources: tuple[str, ...]
    queries: tuple[SearchQuery, ...]
    scoring: ScoringConfig | None = None
    telegram: bool = False


def load_config(path: Path) -> Config:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise ConfigError(
            f"Файл конфигурации не найден: {path} — пройдите настройку "
            f"(скажите Claude «давай настроим»)"
        ) from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Не удалось разобрать {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: ожидался словарь на верхнем уровне")

    sources = tuple(raw.get("sources") or ["hh"])
    unknown = [s for s in sources if s not in KNOWN_SOURCES]
    if unknown:
        raise ConfigError(
            f"Неизвестный источник: {', '.join(unknown)}. "
            f"Доступны: {', '.join(KNOWN_SOURCES)}"
        )

    defaults = raw.get("defaults") or {}
    entries = raw.get("queries") or []
    if not entries:
        raise ConfigError(f"{path}: не задано ни одного запроса (queries)")

    queries = []
    for number, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ConfigError(f"Запрос №{number}: ожидался словарь")
        merged = {**defaults, **entry}
        extra = sorted(set(merged) - _QUERY_FIELDS)
        if extra:
            raise ConfigError(
                f"Запрос №{number}: неизвестные поля: {', '.join(extra)}. "
                f"Допустимы: {', '.join(sorted(_QUERY_FIELDS))}"
            )
        for required in ("name", "text"):
            if not merged.get(required):
                raise ConfigError(f"Запрос №{number}: не заполнено поле {required}")

        role = merged.get("role", merged["name"])
        if not isinstance(role, str) or not role.strip():
            raise ConfigError(
                f"Запрос №{number}: role — непустая строка, получено: {role!r}"
            )
        merged["role"] = role

        experience = merged.get("experience")
        if experience is None:
            merged["experience"] = ()
        elif isinstance(experience, str):
            merged["experience"] = (experience,)
        elif isinstance(experience, (list, tuple)):
            merged["experience"] = tuple(experience)
        else:
            raise ConfigError(
                f"Запрос №{number}: experience должен быть строкой или списком строк, "
                f"получено: {experience!r}"
            )

        period = merged.get("search_period", 1)
        if isinstance(period, bool) or not isinstance(period, int) or not 1 <= period <= MAX_PERIOD_DAYS:
            raise ConfigError(
                f"Запрос №{number}: search_period — число дней от 1 до {MAX_PERIOD_DAYS}, "
                f"получено: {period!r}"
            )

        queries.append(SearchQuery(**merged))

    telegram = raw.get("telegram", False)
    if not isinstance(telegram, bool):
        raise ConfigError(f"telegram: только true или false, получено: {telegram!r}")

    return Config(
        sources=sources,
        queries=tuple(queries),
        scoring=_load_scoring(raw.get("scoring"), path.parent),
        telegram=telegram,
    )


def _load_scoring(raw: object, base: Path) -> ScoringConfig | None:
    """Секции нет или enabled: false — бот работает без оценки."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError("scoring: ожидался словарь")
    old = [name for name in _OLD_SCORING_FIELDS if name in raw]
    if old:
        raise ConfigError(
            f"scoring: {', '.join(old)} — устарело: критерии теперь в одном profile.md "
            f"(разделы «Что ищу» и «Красные флаги»), путь к нему — поле profile"
        )
    extra = sorted(set(raw) - _SCORING_FIELDS)
    if extra:
        raise ConfigError(
            f"scoring: неизвестные поля: {', '.join(extra)}. "
            f"Допустимы: {', '.join(sorted(_SCORING_FIELDS))}"
        )
    if not raw.get("enabled", True):
        return None

    value = raw.get("profile", "profile.md")
    if not value or not isinstance(value, str):
        raise ConfigError(f"scoring: profile — путь к файлу, получено: {value!r}")
    profile = base / Path(value).expanduser()  # абсолютный путь вытесняет base
    if not profile.is_file():
        raise ConfigError(f"scoring: файл profile не найден: {profile}")

    threshold = raw.get("threshold", 5)
    if isinstance(threshold, bool) or not isinstance(threshold, int) or not 1 <= threshold <= 10:
        raise ConfigError(f"scoring: threshold — число от 1 до 10, получено: {threshold!r}")

    backend = raw.get("backend", "opencode")
    if not isinstance(backend, str) or backend not in _BACKENDS:
        raise ConfigError(
            f"scoring: backend — opencode или claude, получено: {backend!r}"
        )

    model = raw.get("model", _DEFAULT_MODELS[backend])
    if not model or not isinstance(model, str):
        raise ConfigError(f"scoring: model — название модели, получено: {model!r}")
    if backend == "opencode" and "/" not in model:
        raise ConfigError(
            f"scoring: model {model!r} для backend opencode — в формате provider/model, "
            "например opencode/big-pickle; для Claude поставьте backend: claude"
        )

    return ScoringConfig(profile=profile, threshold=threshold, backend=backend, model=model)


def with_search_period(config: Config, days: int) -> Config:
    """Период из командной строки (--days) перебивает search_period
    всех запросов: первый сбор за неделю, дальше — за сутки-двое."""
    return replace(
        config,
        queries=tuple(replace(q, search_period=days) for q in config.queries),
    )


def load_secrets(path: Path) -> tuple[str, str]:
    """Читает TG_BOT_TOKEN и TG_CHAT_ID. Переменные окружения имеют приоритет
    над .env — так удобнее подменять их в Планировщике задач."""
    from_file: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            from_file[key.strip()] = value.strip().strip('"').strip("'")

    token = os.environ.get("TG_BOT_TOKEN") or from_file.get("TG_BOT_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID") or from_file.get("TG_CHAT_ID")
    missing = [
        name
        for name, value in (("TG_BOT_TOKEN", token), ("TG_CHAT_ID", chat_id))
        if not value
    ]
    if missing:
        raise ConfigError(
            f"Не заданы {', '.join(missing)}. Укажите их в файле {path} "
            f"или в переменных окружения."
        )
    return token, chat_id
