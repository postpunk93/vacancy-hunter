from __future__ import annotations


def plural(count: int, one: str, few: str, many: str) -> str:
    """Число + русское согласование с ним: `plural(1, "запросом", "запросами",
    "запросами")` → «1 запросом», `plural(3, ...)` → «3 запросами»."""
    tail = count % 100
    if 11 <= tail <= 14:
        word = many
    elif count % 10 == 1:
        word = one
    elif count % 10 in (2, 3, 4):
        word = few
    else:
        word = many
    return f"{count} {word}"
