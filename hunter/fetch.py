from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger(__name__)

# Оба сайта отдают выдачу только браузерным клиентам.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
}
RETRY_DELAYS = (2.0, 5.0, 12.0)


class FetchError(Exception):
    """Страницу не удалось получить. Проход продолжается без неё."""


class Blocked(FetchError):
    """Нас заблокировали. Ретраи только усугубят — прекращаем проход."""


class NotFound(FetchError):
    """404: страницы нет — вакансию удалили. Повтор ничего не изменит."""


def new_client(timeout: float = 15.0) -> httpx.Client:
    return httpx.Client(follow_redirects=True, timeout=timeout, headers=HEADERS)


def get_html(
    url: str,
    *,
    client: httpx.Client | None = None,
    sleep=time.sleep,
    timeout: float = 15.0,
) -> str:
    own_client = client is None
    client = client or new_client(timeout)
    try:
        last_error: FetchError | None = None
        for delay in (0.0, *RETRY_DELAYS):
            if delay:
                log.info("Повтор через %.0f с: %s", delay, url)
                sleep(delay)
            try:
                response = client.get(url)
            except httpx.HTTPError as exc:
                last_error = FetchError(f"{url}: {exc}")
                continue

            if response.status_code == 403:
                raise Blocked(f"{url}: 403 — сайт заблокировал запрос")
            if response.status_code == 404:
                raise NotFound(f"{url}: 404 — страницы нет")
            if response.status_code >= 500:
                last_error = FetchError(f"{url}: HTTP {response.status_code}")
                continue
            if response.status_code != 200:
                raise FetchError(f"{url}: HTTP {response.status_code}")
            return response.text

        raise last_error or FetchError(f"{url}: страницу получить не удалось")
    finally:
        if own_client:
            client.close()
