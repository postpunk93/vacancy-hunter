from __future__ import annotations

import httpx

API_BASE = "https://api.telegram.org"


class TelegramError(Exception):
    """Сообщение не ушло. Вызывающий обязан не помечать вакансии отправленными."""


def send_messages(
    token: str,
    chat_id: str,
    texts: list[str],
    *,
    client: httpx.Client | None = None,
    base_url: str = API_BASE,
) -> None:
    own_client = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        for text in texts:
            try:
                response = client.post(
                    f"{base_url}/bot{token}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                )
            except httpx.HTTPError as exc:
                # Не подставляем URL запроса и текст исключения httpx: оба
                # могут содержать токен бота, а это уйдёт в лог целиком.
                raise TelegramError(
                    f"Телеграм недоступен: {exc.__class__.__name__}"
                ) from exc
            if response.status_code != 200:
                raise TelegramError(
                    f"Телеграм ответил {response.status_code}: {response.text[:300]}"
                )
    finally:
        if own_client:
            client.close()
