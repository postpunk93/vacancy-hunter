from __future__ import annotations

import html as html_module
import json
import re

# У тега есть атрибут style до id, поэтому [^>]* обязателен с обеих сторон.
_STATE_RE = re.compile(
    r'<template[^>]*id="HH-Lux-InitialState"[^>]*>(.*?)</template>',
    re.DOTALL,
)


class ParseError(Exception):
    """Страница не разобралась. Скорее всего сайт сменил вёрстку —
    это должно быть громкой ошибкой, а не тихим пустым списком."""


def extract_state(page_html: str) -> dict:
    match = _STATE_RE.search(page_html)
    if not match:
        raise ParseError(
            "В странице нет HH-Lux-InitialState — вёрстка изменилась "
            "или пришла заглушка вместо выдачи"
        )
    try:
        return json.loads(html_module.unescape(match.group(1)))
    except json.JSONDecodeError as exc:
        raise ParseError(f"HH-Lux-InitialState не разбирается как JSON: {exc}") from exc
