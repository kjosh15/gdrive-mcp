from unittest.mock import patch, MagicMock

import pytest

from tests.fixtures.sample_docx import make_docx


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
async def test_gdoc_suggest_edit_exports_and_reuploads(mock_services):
    drive = mock_services["drive"]

    # Original file is a native Google Doc
    drive.files().get.return_value.execute.return_value = {
        "name": "My Report",
        "mimeType": "application/vnd.google-apps.document",
        "parents": ["parent_folder"],
    }

    # Export returns valid .docx bytes containing the find_text
    exported_docx = make_docx([("The quick brown fox", None)])
    drive.files().export.return_value.execute.return_value = exported_docx

    # Upload of modified .docx returns new file info
    drive.files().create.return_value.execute.return_value = {
        "id": "new_docx_id",
        "name": "My Report (with suggestions).docx",
        "webViewLink": "https://drive.google.com/file/d/new_docx_id/view",
        "version": "1",
        "modifiedTime": "2026-05-05T12:00:00Z",
    }

    from gsuite_mcp.server import gdoc_suggest_edit
    result = await gdoc_suggest_edit(
        file_id="original_id",
        find_text="quick",
        replace_text="slow",
    )

    assert result["file_id"] == "new_docx_id"
    assert result["original_file_id"] == "original_id"
    assert "suggestions" in result["note"].lower()

    # Verify export was called with .docx mime type
    export_call = drive.files().export.call_args
    assert export_call.kwargs["fileId"] == "original_id"
    assert "wordprocessingml" in export_call.kwargs["mimeType"]

    # Verify create (not update) was called for the new file
    drive.files().create.assert_called_once()
    create_call = drive.files().create.call_args
    body = create_call.kwargs["body"]
    assert body["name"] == "My Report (with suggestions).docx"
    assert body["parents"] == ["parent_folder"]


@pytest.mark.asyncio
async def test_gdoc_suggest_edit_rejects_non_gdoc(mock_services):
    drive = mock_services["drive"]
    drive.files().get.return_value.execute.return_value = {
        "name": "file.docx",
        "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }

    from gsuite_mcp.server import gdoc_suggest_edit
    result = await gdoc_suggest_edit(
        file_id="x", find_text="a", replace_text="b",
    )

    assert result["error"] == "NOT_A_GOOGLE_DOC"
    assert "docx_suggest_edit" in result["message"]


@pytest.mark.asyncio
async def test_gdoc_suggest_edit_find_text_not_found(mock_services):
    drive = mock_services["drive"]
    drive.files().get.return_value.execute.return_value = {
        "name": "Doc",
        "mimeType": "application/vnd.google-apps.document",
        "parents": ["p1"],
    }
    exported_docx = make_docx([("Hello world", None)])
    drive.files().export.return_value.execute.return_value = exported_docx

    from gsuite_mcp.server import gdoc_suggest_edit
    result = await gdoc_suggest_edit(
        file_id="d1", find_text="xyz", replace_text="abc",
    )

    assert result["error"] == "FIND_TEXT_NOT_FOUND"


@pytest.mark.asyncio
async def test_gdoc_suggest_edit_cross_paragraph(mock_services):
    drive = mock_services["drive"]
    drive.files().get.return_value.execute.return_value = {
        "name": "Doc",
        "mimeType": "application/vnd.google-apps.document",
        "parents": ["p1"],
    }
    exported_docx = make_docx([("Hello world", None)])
    drive.files().export.return_value.execute.return_value = exported_docx

    from gsuite_mcp.docx_edits import CrossParagraphError
    with patch(
        "gsuite_mcp.gdoc_ops.docx_edits.insert_tracked_change",
        side_effect=CrossParagraphError("spans boundary"),
    ):
        from gsuite_mcp.server import gdoc_suggest_edit
        result = await gdoc_suggest_edit(
            file_id="d1", find_text="Hello world", replace_text="Hi",
        )

    assert result["error"] == "CROSS_PARAGRAPH_MATCH"
    assert "per paragraph" in result["message"]
