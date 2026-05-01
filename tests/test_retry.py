"""Tests for retry_transient helper."""

from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError

from gsuite_mcp.retry import retry_transient


def _make_http_error(status: int) -> HttpError:
    resp = MagicMock()
    resp.status = status
    return HttpError(resp, b"error")


@pytest.mark.asyncio
async def test_succeeds_first_try():
    result = await retry_transient(lambda: "ok", base_delay=0)
    assert result == "ok"


@pytest.mark.asyncio
async def test_retries_on_500_then_succeeds():
    calls = {"count": 0}

    def flaky():
        calls["count"] += 1
        if calls["count"] < 3:
            raise _make_http_error(500)
        return "recovered"

    result = await retry_transient(flaky, max_retries=3, base_delay=0)
    assert result == "recovered"
    assert calls["count"] == 3


@pytest.mark.asyncio
async def test_retries_on_429():
    calls = {"count": 0}

    def rate_limited():
        calls["count"] += 1
        if calls["count"] == 1:
            raise _make_http_error(429)
        return "ok"

    result = await retry_transient(rate_limited, max_retries=2, base_delay=0)
    assert result == "ok"


@pytest.mark.asyncio
async def test_gives_up_after_max_retries():
    def always_fails():
        raise _make_http_error(500)

    with pytest.raises(HttpError) as exc_info:
        await retry_transient(always_fails, max_retries=2, base_delay=0)
    assert exc_info.value.resp.status == 500


@pytest.mark.asyncio
async def test_does_not_retry_on_400():
    def bad_request():
        raise _make_http_error(400)

    with pytest.raises(HttpError) as exc_info:
        await retry_transient(bad_request, max_retries=3, base_delay=0)
    assert exc_info.value.resp.status == 400


@pytest.mark.asyncio
async def test_does_not_retry_on_404():
    def not_found():
        raise _make_http_error(404)

    with pytest.raises(HttpError) as exc_info:
        await retry_transient(not_found, max_retries=3, base_delay=0)
    assert exc_info.value.resp.status == 404


@pytest.mark.asyncio
async def test_retries_502_503_504():
    for code in (502, 503, 504):
        calls = {"count": 0}

        def flaky(c=code):
            calls["count"] += 1
            if calls["count"] == 1:
                raise _make_http_error(c)
            return "ok"

        calls["count"] = 0
        result = await retry_transient(flaky, max_retries=2, base_delay=0)
        assert result == "ok"
