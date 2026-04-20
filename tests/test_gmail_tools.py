"""Tests for the create_reply_draft MCP tool in server.py."""

from unittest.mock import patch, MagicMock, AsyncMock

import pytest


@pytest.fixture
def mock_gmail():
    with patch("gsuite_mcp.auth.get_gmail_service") as mock:
        service = MagicMock()
        mock.return_value = service
        yield service


@pytest.fixture
def mock_create_reply_draft():
    with patch("gsuite_mcp.gmail_ops.create_reply_draft", new_callable=AsyncMock) as m:
        m.return_value = {
            "draft_id": "d1",
            "message_id": "m1",
            "thread_id": "t1",
            "in_reply_to": "<orig@mail.gmail.com>",
            "subject": "Re: Test",
            "to": "alice@example.com",
            "confirmation": "Draft created in thread t1",
        }
        yield m


@pytest.mark.asyncio
async def test_create_reply_draft_happy_path(mock_gmail, mock_create_reply_draft):
    """Tool delegates to gmail_ops.create_reply_draft with correct args."""
    from gsuite_mcp.server import create_reply_draft

    result = await create_reply_draft(
        thread_id="t1",
        in_reply_to_message_id="m_orig",
        to="alice@example.com",
        body="Sounds good!",
    )

    assert result["draft_id"] == "d1"
    assert result["thread_id"] == "t1"
    mock_create_reply_draft.assert_awaited_once()
    call_kwargs = mock_create_reply_draft.call_args.kwargs
    assert call_kwargs["thread_id"] == "t1"
    assert call_kwargs["to"] == "alice@example.com"
    assert call_kwargs["body"] == "Sounds good!"


@pytest.mark.asyncio
async def test_create_reply_draft_optional_params(mock_gmail, mock_create_reply_draft):
    """Optional params (cc, bcc, subject, content_type) pass through."""
    from gsuite_mcp.server import create_reply_draft

    await create_reply_draft(
        thread_id="t1",
        in_reply_to_message_id="m1",
        to="a@example.com",
        body="FYI",
        cc="b@example.com",
        bcc="c@example.com",
        subject="Custom Subject",
        content_type="html",
    )

    call_kwargs = mock_create_reply_draft.call_args.kwargs
    assert call_kwargs["cc"] == "b@example.com"
    assert call_kwargs["bcc"] == "c@example.com"
    assert call_kwargs["subject"] == "Custom Subject"
    assert call_kwargs["content_type"] == "html"


@pytest.mark.asyncio
async def test_create_reply_draft_api_error_propagates(mock_gmail):
    """API errors bubble up, not swallowed."""
    with patch(
        "gsuite_mcp.gmail_ops.create_reply_draft",
        new_callable=AsyncMock,
        side_effect=RuntimeError("Gmail API error"),
    ):
        from gsuite_mcp.server import create_reply_draft

        with pytest.raises(RuntimeError, match="Gmail API error"):
            await create_reply_draft(
                thread_id="t1",
                in_reply_to_message_id="m1",
                to="a@example.com",
                body="test",
            )
