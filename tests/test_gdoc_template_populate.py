from unittest.mock import patch, MagicMock

import pytest


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
async def test_template_populate_copies_and_replaces(mock_services):
    drive = mock_services["drive"]
    docs = mock_services["docs"]

    drive.files().copy.return_value.execute.return_value = {
        "id": "new123",
        "name": "My New Doc",
        "webViewLink": "https://docs.google.com/document/d/new123/edit",
    }
    docs.documents().batchUpdate.return_value.execute.return_value = {
        "replies": [
            {"replaceAllText": {"occurrencesChanged": 1}},
            {"replaceAllText": {"occurrencesChanged": 2}},
        ]
    }

    from gsuite_mcp.server import gdoc_template_populate
    result = await gdoc_template_populate(
        template_file_id="tmpl1",
        parent_folder_id="folder1",
        new_title="My New Doc",
        replacements={"{{NAME}}": "Alice", "{{DATE}}": "2026-05-05"},
    )

    assert result["file_id"] == "new123"
    assert result["web_view_link"] == "https://docs.google.com/document/d/new123/edit"
    assert result["replacements_made"] == {"{{NAME}}": 1, "{{DATE}}": 2}

    copy_call = drive.files().copy.call_args
    assert copy_call.kwargs["fileId"] == "tmpl1"
    body = copy_call.kwargs["body"]
    assert body["name"] == "My New Doc"
    assert body["parents"] == ["folder1"]
    assert body["mimeType"] == "application/vnd.google-apps.document"

    batch_call = docs.documents().batchUpdate.call_args
    requests = batch_call.kwargs["body"]["requests"]
    assert len(requests) == 2
    assert requests[0]["replaceAllText"]["containsText"]["text"] == "{{NAME}}"
    assert requests[0]["replaceAllText"]["replaceText"] == "Alice"
