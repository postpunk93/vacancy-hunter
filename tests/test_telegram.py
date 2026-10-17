import json

import httpx
import pytest

from hunter.telegram import TelegramError, send_messages


def recording_client(*status_codes):
    queue = list(status_codes) or [200]
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        code = queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx.Response(code, json={"ok": code == 200})

    return httpx.Client(transport=httpx.MockTransport(handler)), sent


def test_every_message_is_sent_with_html_and_no_preview():
    client, sent = recording_client(200)

    send_messages("123:abc", "456", ["первое", "второе"], client=client)

    assert [m["text"] for m in sent] == ["первое", "второе"]
    assert all(m["chat_id"] == "456" for m in sent)
    assert all(m["parse_mode"] == "HTML" for m in sent)
    assert all(m["disable_web_page_preview"] is True for m in sent)


def test_empty_list_sends_nothing():
    client, sent = recording_client(200)

    send_messages("123:abc", "456", [], client=client)

    assert sent == []


def test_failure_raises_and_stops():
    client, sent = recording_client(400, 200)

    with pytest.raises(TelegramError, match="400"):
        send_messages("123:abc", "456", ["первое", "второе"], client=client)

    assert len(sent) == 1, "после отказа продолжать нельзя"


def test_transport_error_is_wrapped_and_does_not_leak_the_token():
    def handler(request):
        raise httpx.ConnectError("boom")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    token = "123456:supersecrettoken"

    with pytest.raises(TelegramError) as excinfo:
        send_messages(token, "456", ["первое"], client=client)

    assert token not in str(excinfo.value)
