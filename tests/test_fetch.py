import httpx
import pytest

from hunter.fetch import RETRY_DELAYS, Blocked, FetchError, NotFound, get_html, new_client

URL = "https://hh.ru/search/vacancy?text=python"


def test_default_timeout_keeps_the_retry_budget_under_the_scheduler_kill_timer():
    # 4 попытки × таймаут + паузы между ними не должны подбираться
    # к -ExecutionTimeLimit (10 минут) из install_task.ps1.
    client = new_client()
    assert client.timeout.connect == 15.0


def client_returning(*responses):
    """Клиент, отдающий заготовленные ответы по очереди."""
    queue = list(responses)
    calls = []

    def handler(request):
        calls.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return client, calls


def test_successful_response_returns_the_body():
    client, calls = client_returning(httpx.Response(200, text="<html>ok</html>"))

    assert get_html(URL, client=client, sleep=lambda _: None) == "<html>ok</html>"
    assert len(calls) == 1


def test_server_error_is_retried_then_succeeds():
    client, calls = client_returning(
        httpx.Response(503),
        httpx.Response(500),
        httpx.Response(200, text="ok"),
    )
    slept = []

    assert get_html(URL, client=client, sleep=slept.append) == "ok"
    assert len(calls) == 3
    assert slept == list(RETRY_DELAYS[:2]), "паузы растут между попытками"


def test_network_error_is_retried():
    client, calls = client_returning(
        httpx.ConnectError("boom"),
        httpx.Response(200, text="ok"),
    )

    assert get_html(URL, client=client, sleep=lambda _: None) == "ok"
    assert len(calls) == 2


def test_gives_up_after_all_retries():
    client, calls = client_returning(*[httpx.Response(503)] * 4)

    with pytest.raises(FetchError):
        get_html(URL, client=client, sleep=lambda _: None)
    assert len(calls) == 4, "первая попытка плюс три ретрая"


def test_forbidden_is_not_retried():
    client, calls = client_returning(httpx.Response(403), httpx.Response(200, text="ok"))

    with pytest.raises(Blocked, match="403"):
        get_html(URL, client=client, sleep=lambda _: None)
    assert len(calls) == 1, "долбиться в блокировку бессмысленно и вредно"


def test_client_error_is_not_retried():
    client, calls = client_returning(httpx.Response(404))

    with pytest.raises(FetchError, match="404"):
        get_html(URL, client=client, sleep=lambda _: None)
    assert len(calls) == 1


def test_not_found_is_its_own_error_and_is_not_retried():
    client, calls = client_returning(httpx.Response(404))

    with pytest.raises(NotFound) as caught:
        get_html(URL, client=client, sleep=lambda _: None)

    assert isinstance(caught.value, FetchError), "бот ловит FetchError — 404 должен остаться им"
    assert len(calls) == 1
