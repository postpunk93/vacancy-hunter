from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import ConfigError, ScoringConfig
from .digest import format_salary
from .fulltext import fetch_texts
from .models import FullText, Score, Vacancy
from .store import Store

log = logging.getLogger(__name__)

# Текст промпта, схема и модель входят в хеш сами. Версию поднимать, когда
# меняется то, что в хеш не попадает, — например, состав полей вакансии во входе.
PROMPT_VERSION = 2
BATCH_SIZE = 10
DESCRIPTION_LIMIT = 6000
MODEL_TIMEOUT = 600
OPENCODE_AGENT = "vacancy-scorer"
ATTEMPTS = 2

_WHY_LIMIT = 300
_FLAGS_LIMIT = 6
_FLAG_LENGTH = 60

Ask = Callable[[str, str], object]

# Признаки исчерпанной квоты в тексте ошибки бэкенда: такую ошибку не
# повторяем, вторая попытка только потратит время.
_QUOTA_MARKERS = ("usage limit", "quota", "limit reached", "credits")


def _retryable(message: str) -> bool:
    lowered = message.lower()
    return not any(marker in lowered for marker in _QUOTA_MARKERS)


class ScoringError(Exception):
    """Модель не ответила или ответила не по схеме. Вакансии остаются без оценки.

    retryable — стоит ли повторять запрос: таймаут или сбой запуска повторяем,
    исчерпанную квоту или лимит — нет, вторая попытка только потратит время.
    """

    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class Criteria:
    """Текст profile.md: правится владельцем, код его не разбирает."""

    profile: str
    model: str = "opencode/big-pickle"
    backend: str = "opencode"


def load_criteria(cfg: ScoringConfig) -> Criteria:
    try:
        return Criteria(
            profile=cfg.profile.read_text(encoding="utf-8"),
            model=cfg.model,
            backend=cfg.backend,
        )
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"scoring: не удалось прочитать файл профиля: {exc}") from exc


def criteria_hash(criteria: Criteria) -> str:
    """Всё, от чего зависит оценка: после любой правки старые оценки не годятся."""
    digest = hashlib.sha256()
    parts = (
        str(PROMPT_VERSION), criteria.model, criteria.backend, _PROMPT,
        json.dumps(SCHEMA, sort_keys=True), criteria.profile,
    )
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


_PROMPT = """\
Ты помогаешь конкретному человеку искать работу: оцениваешь вакансии, насколько
каждая подходит именно ему, и смотришь на неё глазами конфликтолога — что в тексте
обещает будущие конфликты с работодателем.

На вход — JSON-массив вакансий: id, название, компания, зарплата, форматы работы,
опыт, описание (markdown, может быть пустым) и ключевые навыки.

# Профиль: кто ищет работу, что ему подходит и красные флаги

{profile}

В профиле «Кто я» — контекст: сам по себе оценку не меняет.
«Что ищу» — роли с признаками «да» и «нет» и общие рамки для всех ролей.
«Красные флаги» — признаки конфликтности, тяжёлые и мягкие. Служебные разделы
вроде команд сбора не критерии, их не учитывай.

# Как ставить оценку

score — целое от 1 до 10, выше — лучше.

1. Основа — совпадение с разделом «Что ищу»:
   - подходит под «да» одной из ролей — 7–10;
   - «может быть» — 5–6;
   - признаки «нет» или вне общих рамок (не удалёнка, деньги ниже порога,
     чужая роль) — 1–4.
2. Конфликтность из раздела «Красные флаги» только понижает оценку, повысить её не может:
   - мягкие признаки на оценку не влияют, только попадают во flags;
   - один-два тяжёлых признака — минус балл-два, но сами по себе не ниже 5;
   - три и больше тяжёлых признака — ниже 5, даже если вакансия подходит по роли.
3. Нехватка информации (короткое описание, нет зарплаты) — не повод снижать оценку.
   Если описание пустое, оценивай по остальным полям.

why — одна короткая фраза по-русски: почему такая оценка. Без воды и без пересказа названия.
flags — короткие названия сработавших признаков конфликтности из раздела «Красные флаги»
(тяжёлых и мягких); пустой список, если их нет.

Верни оценку для каждого id из входа.
"""


def build_system_prompt(criteria: Criteria) -> str:
    return _PROMPT.format(profile=criteria.profile.strip())


SCHEMA: dict = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "score": {"type": "integer", "minimum": 1, "maximum": 10},
                    "why": {"type": "string"},
                    "flags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "score", "why", "flags"],
            },
        },
    },
    "required": ["items"],
}


def _excerpt(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "…"


def vacancy_payload(vacancy: Vacancy, text: FullText | None) -> dict:
    return {
        "id": vacancy.key,
        "name": vacancy.name,
        "company": vacancy.company,
        "salary": format_salary(vacancy),
        "work_formats": list(vacancy.work_formats),
        "experience": vacancy.experience,
        "description": _excerpt(text.description, DESCRIPTION_LIMIT) if text else "",
        "key_skills": list(text.key_skills) if text else [],
    }


def parse_scores(data: object, keys: list[str]) -> dict[str, Score]:
    """Ответ модели → оценки только для вакансий пачки. Кривые строки
    пропускаются: такие вакансии останутся без оценки, но не потеряются."""
    allowed = set(keys)
    items = data.get("items") if isinstance(data, dict) else None
    scores: dict[str, Score] = {}
    for row in items if isinstance(items, list) else []:
        if not isinstance(row, dict):
            continue
        key, value, why = row.get("id"), row.get("score"), row.get("why")
        if key not in allowed or key in scores:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if not isinstance(why, str) or not why.strip():
            continue
        flags = row.get("flags")
        clean_flags = tuple(
            flag.strip()[:_FLAG_LENGTH]
            for flag in (flags if isinstance(flags, list) else [])
            if isinstance(flag, str) and flag.strip()
        )[:_FLAGS_LIMIT]
        scores[key] = Score(
            value=max(1, min(10, round(value))),
            why=_excerpt(why, _WHY_LIMIT),
            flags=clean_flags,
        )
    return scores


def ask_claude(
    system: str,
    payload: str,
    *,
    model: str,
    timeout: float = MODEL_TIMEOUT,
    run=subprocess.run,
) -> object:
    """Claude Code в неинтерактивном режиме на подписке владельца. --bare не
    подходит: в нём вход только по API-ключу. Поэтому лишнее выключаем
    по отдельности: инструменты, MCP, файлы настроек, сохранение сессии."""
    command = [
        "claude", "-p",
        "--model", model,
        "--tools", "",
        "--strict-mcp-config",
        "--setting-sources", "",
        "--no-session-persistence",
        "--system-prompt", system,
        "--output-format", "json",
        "--json-schema", json.dumps(SCHEMA, ensure_ascii=False),
    ]
    try:
        completed = run(
            command,
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            cwd=tempfile.gettempdir(),
        )
    except FileNotFoundError as exc:
        raise ScoringError("claude не найден — установлен ли Claude Code?") from exc
    except subprocess.TimeoutExpired as exc:
        raise ScoringError(f"claude не ответил за {timeout:.0f} с") from exc
    except OSError as exc:
        raise ScoringError(f"claude не запустился: {exc}") from exc

    try:
        out = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        out = None
    if not isinstance(out, dict):
        message = (
            f"claude завершился с кодом {completed.returncode}: "
            + _excerpt(completed.stderr or completed.stdout or "", 300)
        )
        raise ScoringError(message, retryable=_retryable(message))
    if completed.returncode != 0 or out.get("is_error"):
        message = "claude вернул ошибку: " + _excerpt(
            str(out.get("result") or completed.stderr), 300
        )
        raise ScoringError(message, retryable=_retryable(message))

    data = out.get("structured_output")
    if data is None:
        try:
            data = json.loads(str(out.get("result") or ""))
        except json.JSONDecodeError:
            data = None
    if not isinstance(data, dict):
        raise ScoringError("claude не вернул структурированный ответ: " + _excerpt(str(out.get("result") or ""), 300))
    return data


def _opencode_config(system: str) -> dict:
    """Конфиг агента во временной папке: свой системный промпт, один шаг,
    все инструменты запрещены — оценка не должна трогать файлы и сеть."""
    return {
        "$schema": "https://opencode.ai/config.json",
        "agents": {
            OPENCODE_AGENT: {
                "mode": "primary",
                "hidden": True,
                "system": system,
                "steps": 1,
                "permissions": [{"action": "*", "resource": "*", "effect": "deny"}],
            }
        },
    }


def _extract_json_object(text: str) -> dict | None:
    """Снять markdown-обёртку и достать JSON из ответа модели.

    Массив оценок без обёртки приводим к той же форме, что и {"items": [...]}.
    """
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2 and lines[-1].strip().startswith("```"):
            text = "\n".join(lines[1:-1]).strip()

    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            return {"items": data}
    return None


def _parse_opencode(completed: subprocess.CompletedProcess) -> object:
    """JSONL-события OpenCode → ответ модели. Битые строки пропускаем:
    из-за одной нечитаемой строки пачка не должна остаться без оценки."""
    texts: list[str] = []
    event_error: ScoringError | None = None
    for line in (completed.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "text":
            part = event.get("part")
            text = part.get("text") if isinstance(part, dict) else None
            if isinstance(text, str):
                texts.append(text)
        elif kind == "error" and event_error is None:
            error = event.get("error")
            error = error if isinstance(error, dict) else {}
            message = str(error.get("message") or error.get("type") or "неизвестная ошибка")
            retry_after = error.get("retryAfterMs")
            if retry_after is None and isinstance(error.get("data"), dict):
                retry_after = error["data"].get("retryAfterMs")
            if isinstance(retry_after, (int, float)) and not isinstance(retry_after, bool):
                message += f" (повтор через ~{retry_after / 3_600_000:.1f} ч)"
            event_error = ScoringError(message, retryable=_retryable(message))

    if event_error is not None:
        raise event_error
    if completed.returncode != 0:
        detail = completed.stderr or completed.stdout or ""
        raise ScoringError(
            f"opencode завершился с кодом {completed.returncode}: " + _excerpt(detail, 300)
        )

    text = "".join(texts).strip()
    if not text:
        raise ScoringError("opencode не вернул ответ: " + _excerpt(completed.stderr or "", 300))
    data = _extract_json_object(text)
    if data is None:
        raise ScoringError(
            "opencode не вернул структурированный ответ: " + _excerpt(text, 300)
        )
    return data


def ask_opencode(
    system: str,
    payload: str,
    *,
    model: str,
    timeout: float = MODEL_TIMEOUT,
    run=subprocess.run,
) -> object:
    """OpenCode CLI во временной папке со своим конфигом агента. Инструменты
    запрещены конфигом, поэтому ответ — только текст с JSON-массивом оценок."""
    try:
        with tempfile.TemporaryDirectory(prefix="hunter-scorer-") as workdir:
            Path(workdir, "opencode.json").write_text(
                json.dumps(_opencode_config(system), ensure_ascii=False), encoding="utf-8"
            )
            command = [
                "opencode", "run", "--agent", OPENCODE_AGENT,
                "--model", model, "--format", "json",
            ]
            completed = run(
                command,
                input=payload,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout,
                cwd=workdir,
            )
            return _parse_opencode(completed)
    except FileNotFoundError as exc:
        raise ScoringError("opencode не найден — установлен ли OpenCode?") from exc
    except subprocess.TimeoutExpired as exc:
        raise ScoringError(f"opencode не ответил за {timeout:.0f} с") from exc
    except OSError as exc:
        raise ScoringError(f"opencode не запустился: {exc}") from exc


def ask_model(
    system: str,
    payload: str,
    *,
    criteria: Criteria,
    run=subprocess.run,
) -> object:
    """Один вопрос модели, бэкенд — из конфига владельца."""
    if criteria.backend == "claude":
        return ask_claude(system, payload, model=criteria.model, run=run)
    if criteria.backend == "opencode":
        return ask_opencode(system, payload, model=criteria.model, run=run)
    raise ValueError(f"scoring: неизвестный бэкенд: {criteria.backend!r}")


def _ask_with_retry(ask: Ask, system: str, payload: str) -> object:
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return ask(system, payload)
        except ScoringError as exc:
            if not exc.retryable or attempt >= ATTEMPTS:
                raise
            log.info("Повтор запроса к модели: %s", exc)
    raise AssertionError("недостижимо")


def score_vacancies(
    vacancies: list[Vacancy],
    texts: dict[str, FullText],
    *,
    ask: Ask,
    criteria: Criteria,
    store: Store,
) -> tuple[dict[str, Score], list[str]]:
    """Оценка для каждой вакансии. Уже оценённые по тем же критериям берутся
    из кеша. Без текста — оценка по карточке, она не кешируется. Первая
    пачка, не оценённая и с повтором, прекращает оценку остальных."""
    digest = criteria_hash(criteria)
    scores: dict[str, Score] = {}
    pending: list[Vacancy] = []
    for vacancy in vacancies:
        cached = store.get_score(vacancy.key, digest)
        if cached is not None:
            scores[vacancy.key] = cached
        else:
            pending.append(vacancy)

    system = build_system_prompt(criteria)
    last_error: str | None = None
    for start in range(0, len(pending), BATCH_SIZE):
        batch = pending[start:start + BATCH_SIZE]
        payload = json.dumps(
            [vacancy_payload(v, texts.get(v.key)) for v in batch], ensure_ascii=False
        )
        try:
            data = _ask_with_retry(ask, system, payload)
        except ScoringError as exc:
            # Пачка не прошла и с повтором — модель недоступна или зависла.
            # Остальные пачки ждали бы по таймауту каждая: проход растянулся
            # бы на часы. Оставшиеся вакансии уходят без оценки.
            last_error = str(exc)
            log.warning("Оценка прекращена, пачка не оценена: %s", exc)
            break
        parsed = parse_scores(data, [v.key for v in batch])
        for vacancy in batch:
            score = parsed.get(vacancy.key)
            if score is None:
                continue
            if vacancy.key in texts:
                store.save_score(vacancy.key, digest, score)
            else:
                score = Score(score.value, score.why, score.flags, from_card=True)
            scores[vacancy.key] = score

    reason = last_error or "нет в ответе модели"
    missing = [v for v in vacancies if v.key not in scores]
    for vacancy in missing:
        scores[vacancy.key] = Score(None, f"модель не оценила: {reason}")
    if not missing:
        return scores, []
    log.warning("Не оценено вакансий: %d (%s)", len(missing), reason)
    return scores, [f"не оценено вакансий: {len(missing)} — {reason}"]


def score_pass(
    vacancies: list[Vacancy],
    *,
    fetch_page: Callable[[str], str],
    store: Store,
    pause: Callable[[float], None],
    ask: Ask,
    criteria: Criteria,
) -> tuple[dict[str, Score], list[str]]:
    """Полные тексты, затем оценка. Предупреждения обоих шагов — для дайджеста."""
    texts, warnings = fetch_texts(vacancies, fetch_page=fetch_page, store=store, pause=pause)
    scores, more = score_vacancies(vacancies, texts, ask=ask, criteria=criteria, store=store)
    return scores, warnings + more
