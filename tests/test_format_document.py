"""Tests for format_document in docs_ops and the server tool wrapper."""

import pytest
from unittest.mock import MagicMock, patch

from googleapiclient.errors import HttpError

from gsuite_mcp.docs_ops import format_document, VALID_NAMED_STYLES


def _make_doc(*paragraphs):
    """Build a minimal Google Docs body structure.

    Each paragraph is a tuple: (start, end, text, named_style).
    """
    content = []
    for start, end, text, named_style in paragraphs:
        content.append({
            "startIndex": start,
            "endIndex": end,
            "paragraph": {
                "paragraphStyle": {"namedStyleType": named_style},
                "elements": [
                    {
                        "startIndex": start,
                        "endIndex": end,
                        "textRun": {"content": text},
                    }
                ],
            },
        })
    return {"body": {"content": content}}


def _mock_docs_service(doc, batch_response=None):
    svc = MagicMock()
    svc.documents().get.return_value.execute.return_value = doc
    if batch_response is None:
        batch_response = {"replies": []}
    svc.documents().batchUpdate.return_value.execute.return_value = batch_response
    return svc


def _make_http_error(status: int) -> HttpError:
    resp = MagicMock()
    resp.status = status
    return HttpError(resp, b"error")


# -------------------------------------------------------------------
# VALID_NAMED_STYLES constant
# -------------------------------------------------------------------

def test_valid_named_styles_includes_all():
    assert "NORMAL_TEXT" in VALID_NAMED_STYLES
    assert "TITLE" in VALID_NAMED_STYLES
    assert "SUBTITLE" in VALID_NAMED_STYLES
    for i in range(1, 7):
        assert f"HEADING_{i}" in VALID_NAMED_STYLES


# -------------------------------------------------------------------
# format_document — validation
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_format_invalid_action():
    svc = _mock_docs_service(_make_doc())
    result = await format_document(svc, "f1", [
        {"action": "unknown", "find_text": "x"},
    ])
    assert result["error"] == "INVALID_ACTION"
    assert result["retryable"] is False


@pytest.mark.asyncio
async def test_format_invalid_style():
    svc = _mock_docs_service(_make_doc())
    result = await format_document(svc, "f1", [
        {"action": "set_style", "find_text": "x", "style": "BOLD_TEXT"},
    ])
    assert result["error"] == "INVALID_STYLE"
    assert result["retryable"] is False


@pytest.mark.asyncio
async def test_format_missing_find_text():
    svc = _mock_docs_service(_make_doc())
    result = await format_document(svc, "f1", [
        {"action": "delete"},
    ])
    assert result["error"] == "MISSING_FIND_TEXT"
    assert result["retryable"] is False


@pytest.mark.asyncio
async def test_format_empty_find_text():
    svc = _mock_docs_service(_make_doc())
    result = await format_document(svc, "f1", [
        {"action": "delete", "find_text": "   "},
    ])
    assert result["error"] == "MISSING_FIND_TEXT"


@pytest.mark.asyncio
async def test_format_set_style_missing_style():
    svc = _mock_docs_service(_make_doc())
    result = await format_document(svc, "f1", [
        {"action": "set_style", "find_text": "x"},
    ])
    assert result["error"] == "INVALID_STYLE"


@pytest.mark.asyncio
async def test_format_empty_operations():
    svc = _mock_docs_service(_make_doc())
    result = await format_document(svc, "f1", [])
    assert result["error"] == "EMPTY_OPERATIONS"


# -------------------------------------------------------------------
# format_document — set_style
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_style_basic():
    doc = _make_doc(
        (0, 14, "Introduction\n", "NORMAL_TEXT"),
        (14, 30, "Some body text.\n", "NORMAL_TEXT"),
    )
    svc = _mock_docs_service(doc)
    result = await format_document(svc, "f1", [
        {"action": "set_style", "find_text": "Introduction", "style": "HEADING_1"},
    ])

    assert "error" not in result
    assert result["file_id"] == "f1"
    assert result["operations_applied"] == 1
    assert result["results"][0]["status"] == "applied"
    assert result["results"][0]["style"] == "HEADING_1"

    # Verify batchUpdate request
    call_args = svc.documents().batchUpdate.call_args
    requests = call_args.kwargs["body"]["requests"]
    assert len(requests) == 1
    style_req = requests[0]["updateParagraphStyle"]
    assert style_req["range"]["startIndex"] == 0
    assert style_req["range"]["endIndex"] == 14
    assert style_req["paragraphStyle"]["namedStyleType"] == "HEADING_1"
    assert style_req["fields"] == "namedStyleType"


@pytest.mark.asyncio
async def test_set_style_case_insensitive():
    doc = _make_doc(
        (0, 14, "Introduction\n", "NORMAL_TEXT"),
    )
    svc = _mock_docs_service(doc)
    result = await format_document(svc, "f1", [
        {"action": "set_style", "find_text": "introduction", "style": "HEADING_2"},
    ])
    assert result["operations_applied"] == 1
    assert result["results"][0]["status"] == "applied"


@pytest.mark.asyncio
async def test_set_style_substring_match():
    """find_text matches a substring within the paragraph text."""
    doc = _make_doc(
        (0, 30, "Chapter 1: Introduction\n", "NORMAL_TEXT"),
    )
    svc = _mock_docs_service(doc)
    result = await format_document(svc, "f1", [
        {"action": "set_style", "find_text": "Introduction", "style": "HEADING_1"},
    ])
    assert result["operations_applied"] == 1


@pytest.mark.asyncio
async def test_set_style_not_found():
    doc = _make_doc(
        (0, 14, "Introduction\n", "NORMAL_TEXT"),
    )
    svc = _mock_docs_service(doc)
    result = await format_document(svc, "f1", [
        {"action": "set_style", "find_text": "Conclusion", "style": "HEADING_1"},
    ])
    assert result["operations_applied"] == 0
    assert result["results"][0]["status"] == "not_found"
    # No batchUpdate call since nothing to apply
    svc.documents().batchUpdate.assert_not_called()


# -------------------------------------------------------------------
# format_document — delete
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_paragraph():
    doc = _make_doc(
        (0, 14, "Introduction\n", "HEADING_1"),
        (14, 36, "Paragraph to remove.\n", "NORMAL_TEXT"),
        (36, 50, "Keep this text.\n", "NORMAL_TEXT"),
    )
    svc = _mock_docs_service(doc)
    result = await format_document(svc, "f1", [
        {"action": "delete", "find_text": "Paragraph to remove"},
    ])

    assert result["operations_applied"] == 1
    assert result["results"][0]["status"] == "applied"
    assert result["results"][0]["characters_deleted"] == 36 - 14

    call_args = svc.documents().batchUpdate.call_args
    requests = call_args.kwargs["body"]["requests"]
    delete_req = requests[0]["deleteContentRange"]["range"]
    assert delete_req["startIndex"] == 14
    assert delete_req["endIndex"] == 36


@pytest.mark.asyncio
async def test_delete_first_match_only():
    """When multiple paragraphs match, only the first is deleted."""
    doc = _make_doc(
        (0, 8, "Note: A\n", "NORMAL_TEXT"),
        (8, 16, "Note: B\n", "NORMAL_TEXT"),
    )
    svc = _mock_docs_service(doc)
    result = await format_document(svc, "f1", [
        {"action": "delete", "find_text": "Note"},
    ])

    assert result["operations_applied"] == 1
    call_args = svc.documents().batchUpdate.call_args
    requests = call_args.kwargs["body"]["requests"]
    # Should delete the first match (startIndex=0)
    assert requests[0]["deleteContentRange"]["range"]["startIndex"] == 0


# -------------------------------------------------------------------
# format_document — delete_empty_after
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_empty_after():
    doc = _make_doc(
        (0, 14, "Introduction\n", "HEADING_1"),
        (14, 15, "\n", "NORMAL_TEXT"),              # empty
        (15, 16, "\n", "NORMAL_TEXT"),              # empty
        (16, 30, "Real content.\n", "NORMAL_TEXT"),
    )
    svc = _mock_docs_service(doc)
    result = await format_document(svc, "f1", [
        {"action": "delete_empty_after", "find_text": "Introduction"},
    ])

    assert result["operations_applied"] == 1
    assert result["results"][0]["empty_paragraphs_deleted"] == 2

    call_args = svc.documents().batchUpdate.call_args
    requests = call_args.kwargs["body"]["requests"]
    # Two deletes, sorted descending by startIndex
    assert len(requests) == 2
    assert requests[0]["deleteContentRange"]["range"]["startIndex"] == 15
    assert requests[1]["deleteContentRange"]["range"]["startIndex"] == 14


@pytest.mark.asyncio
async def test_delete_empty_after_no_empties():
    doc = _make_doc(
        (0, 14, "Introduction\n", "HEADING_1"),
        (14, 30, "Real content.\n", "NORMAL_TEXT"),
    )
    svc = _mock_docs_service(doc)
    result = await format_document(svc, "f1", [
        {"action": "delete_empty_after", "find_text": "Introduction"},
    ])

    assert result["operations_applied"] == 1
    assert result["results"][0]["empty_paragraphs_deleted"] == 0
    # No batchUpdate call when no empties found
    svc.documents().batchUpdate.assert_not_called()


@pytest.mark.asyncio
async def test_delete_empty_after_whitespace_only():
    """Paragraphs with only spaces/tabs count as empty."""
    doc = _make_doc(
        (0, 14, "Introduction\n", "HEADING_1"),
        (14, 18, "   \n", "NORMAL_TEXT"),     # whitespace-only
        (18, 30, "Real content.\n", "NORMAL_TEXT"),
    )
    svc = _mock_docs_service(doc)
    result = await format_document(svc, "f1", [
        {"action": "delete_empty_after", "find_text": "Introduction"},
    ])

    assert result["results"][0]["empty_paragraphs_deleted"] == 1


# -------------------------------------------------------------------
# format_document — multiple operations in one call
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multiple_operations():
    """set_style + delete in one call, requests sorted descending."""
    doc = _make_doc(
        (0, 14, "Introduction\n", "NORMAL_TEXT"),
        (14, 30, "Old paragraph.\n", "NORMAL_TEXT"),
        (30, 42, "Conclusion\n", "NORMAL_TEXT"),
    )
    svc = _mock_docs_service(doc)
    result = await format_document(svc, "f1", [
        {"action": "set_style", "find_text": "Introduction", "style": "HEADING_1"},
        {"action": "delete", "find_text": "Old paragraph"},
        {"action": "set_style", "find_text": "Conclusion", "style": "HEADING_1"},
    ])

    assert result["operations_applied"] == 3
    assert all(r["status"] == "applied" for r in result["results"])

    call_args = svc.documents().batchUpdate.call_args
    requests = call_args.kwargs["body"]["requests"]
    assert len(requests) == 3

    # Requests should be sorted by startIndex descending
    indices = []
    for req in requests:
        if "updateParagraphStyle" in req:
            indices.append(req["updateParagraphStyle"]["range"]["startIndex"])
        elif "deleteContentRange" in req:
            indices.append(req["deleteContentRange"]["range"]["startIndex"])
    assert indices == sorted(indices, reverse=True)


@pytest.mark.asyncio
async def test_partial_success():
    """One operation succeeds, one not_found — still applies what it can."""
    doc = _make_doc(
        (0, 14, "Introduction\n", "NORMAL_TEXT"),
    )
    svc = _mock_docs_service(doc)
    result = await format_document(svc, "f1", [
        {"action": "set_style", "find_text": "Introduction", "style": "HEADING_1"},
        {"action": "delete", "find_text": "Nonexistent"},
    ])

    assert result["operations_applied"] == 1
    assert result["results"][0]["status"] == "applied"
    assert result["results"][1]["status"] == "not_found"


# -------------------------------------------------------------------
# Server-level tests for format_document tool wrapper
# -------------------------------------------------------------------

@pytest.fixture
def mock_services():
    with patch("gsuite_mcp.auth.get_drive_service") as mock_drive, \
         patch("gsuite_mcp.auth.get_docs_service") as mock_docs:
        drive = MagicMock()
        docs = MagicMock()
        mock_drive.return_value = drive
        mock_docs.return_value = docs
        yield {"drive": drive, "docs": docs}


@pytest.mark.asyncio
async def test_server_format_not_a_google_doc(mock_services):
    from gsuite_mcp.server import format_document as server_format_document

    drive = mock_services["drive"]
    drive.files().get.return_value.execute.return_value = {
        "name": "file.docx",
        "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "modifiedTime": "2026-05-10T12:00:00Z",
    }

    result = await server_format_document(
        file_id="d1",
        operations=[{"action": "set_style", "find_text": "x", "style": "HEADING_1"}],
    )

    assert result["error"] == "NOT_A_GOOGLE_DOC"
    assert result["retryable"] is False


@pytest.mark.asyncio
async def test_server_format_success(mock_services):
    from gsuite_mcp.server import format_document as server_format_document

    drive = mock_services["drive"]
    docs = mock_services["docs"]

    drive.files().get.return_value.execute.return_value = {
        "name": "My Doc",
        "mimeType": "application/vnd.google-apps.document",
        "modifiedTime": "2026-05-12T14:00:00Z",
    }
    docs.documents().get.return_value.execute.return_value = _make_doc(
        (0, 14, "Introduction\n", "NORMAL_TEXT"),
        (14, 30, "Some body text.\n", "NORMAL_TEXT"),
    )
    docs.documents().batchUpdate.return_value.execute.return_value = {"replies": []}

    result = await server_format_document(
        file_id="d1",
        operations=[{"action": "set_style", "find_text": "Introduction", "style": "HEADING_1"}],
    )

    assert "error" not in result
    assert result["file_id"] == "d1"
    assert result["operations_applied"] == 1
    assert result["modified_time"] == "2026-05-12T14:00:00Z"


@pytest.mark.asyncio
async def test_server_format_catches_http_error(mock_services):
    from gsuite_mcp.server import format_document as server_format_document

    drive = mock_services["drive"]
    docs = mock_services["docs"]

    drive.files().get.return_value.execute.return_value = {
        "name": "My Doc",
        "mimeType": "application/vnd.google-apps.document",
        "modifiedTime": "2026-05-12T14:00:00Z",
    }
    docs.documents().get.return_value.execute.side_effect = _make_http_error(500)

    result = await server_format_document(
        file_id="d1",
        operations=[{"action": "set_style", "find_text": "x", "style": "HEADING_1"}],
    )

    assert result["error"] == "GOOGLE_API_ERROR"
    assert result["retryable"] is True
    assert result["http_status"] == 500
