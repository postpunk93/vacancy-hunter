from __future__ import annotations

import logging
import random
from typing import Callable

from .platforms import Captcha, get
from .fetch import Blocked, FetchError
from .models import FullText, Vacancy
from .parse import ParseError
from .store import Store

log = logging.getLogger(__name__)

# Пауза между страницами вакансий, с. Скорость не важна, важно не попасть
# под блокировку hh: страниц за проход до сотни.
TEXT_PAUSE_RANGE = (3.0, 6.0)


def fetch_texts(
    vacancies: list[Vacancy],
    *,
    fetch_page: Callable[[str], str],
    store: Store,
    pause: Callable[[float], None],
    jitter: Callable[[float, float], float] = random.uniform,
) -> tuple[dict[str, FullText], list[str]]:
    """Полные тексты вакансий по ключу. Уже скачанные берутся из кеша.
    Вакансия без текста в словарь не попадает — её оценят по карточке.
    При блокировке скачивание прекращается (повтор только усугубит),
    но тексты из кеша для оставшихся вакансий всё равно берутся."""
    texts: dict[str, FullText] = {}
    requested = False
    blocked: str | None = None
    not_fetched = 0
    for vacancy in vacancies:
        cached = store.get_text(vacancy.key)
        if cached is not None:
            texts[vacancy.key] = cached
            continue
        if blocked is not None:
            not_fetched += 1
            continue

        if requested:
            pause(jitter(*TEXT_PAUSE_RANGE))
        requested = True
        platform = get(vacancy.source)
        url = platform.vacancy_url(vacancy.vacancy_id)
        try:
            detail = platform.parse_detail(fetch_page(url))
        except (Blocked, Captcha) as exc:
            log.error("Скачивание текстов остановлено: %s", exc)
            blocked = str(exc)
            not_fetched += 1
            continue
        except (FetchError, ParseError, KeyError, TypeError, ValueError) as exc:
            log.warning("Текст вакансии %s не получен: %s", vacancy.key, exc)
            continue

        store.corpus.save_detail(detail)
        texts[vacancy.key] = FullText(detail.description, tuple(detail.key_skills))

    if blocked is None:
        return texts, []
    return texts, [
        f"hh заблокировал скачивание текстов вакансий: {blocked}; "
        f"не скачано {not_fetched}, оценка по карточке"
    ]
