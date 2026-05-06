"""Integration tests for gdoc tools — requires real Google credentials.

Run with: uv run pytest tests/test_integration_gdoc.py -v -m integration
Skip if GOOGLE_OAUTH_REFRESH_TOKEN is not set.
"""

import os

import pytest

pytestmark = pytest.mark.integration

HAVE_CREDS = bool(os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN"))
BTR_TEMPLATE_ID = "1Q1avdQF1gP2hWtIV3VOQg9Exd7zQtu_s"


@pytest.mark.skipif(not HAVE_CREDS, reason="No Google OAuth credentials")
@pytest.mark.asyncio
async def test_template_populate_btr_template():
    """Copy BTR_Template, replace 4 placeholders, verify result exists."""
    from gsuite_mcp.server import gdoc_template_populate, download_file
    from gsuite_mcp import auth

    drive = auth.get_drive_service()
    import asyncio
    meta = await asyncio.to_thread(
        lambda: drive.files()
        .get(fileId=BTR_TEMPLATE_ID, fields="parents")
        .execute()
    )
    parent = meta.get("parents", ["root"])[0]

    result = await gdoc_template_populate(
        template_file_id=BTR_TEMPLATE_ID,
        parent_folder_id=parent,
        new_title="Integration Test - BTR Populated",
        replacements={
            "NAME OF CLIENT": "Acme Corp",
            "This Is A Template": "Integration Test Document",
        },
    )

    assert result["file_id"]
    assert result["web_view_link"]
    assert "NAME OF CLIENT" in result["replacements_made"]

    # Export both as PDF for visual comparison
    source_pdf = await download_file(
        file_id=BTR_TEMPLATE_ID, export_format="application/pdf"
    )
    result_pdf = await download_file(
        file_id=result["file_id"], export_format="application/pdf"
    )

    assert source_pdf["size_bytes"] > 0
    assert result_pdf["size_bytes"] > 0

    # Clean up: delete the test file
    await asyncio.to_thread(
        lambda: drive.files().delete(fileId=result["file_id"]).execute()
    )


@pytest.mark.skipif(not HAVE_CREDS, reason="No Google OAuth credentials")
@pytest.mark.asyncio
async def test_gdoc_suggest_edit_on_real_doc():
    """Create a temp Google Doc, apply suggest_edit, verify .docx created."""
    from gsuite_mcp import auth
    from gsuite_mcp.server import gdoc_suggest_edit
    import asyncio

    drive = auth.get_drive_service()
    docs = auth.get_docs_service()

    # Create a temp Google Doc with known content
    doc = await asyncio.to_thread(
        lambda: docs.documents()
        .create(body={"title": "Integration Test - Suggest Edit Source"})
        .execute()
    )
    doc_id = doc["documentId"]

    # Insert text
    await asyncio.to_thread(
        lambda: docs.documents()
        .batchUpdate(
            documentId=doc_id,
            body={"requests": [{"insertText": {"location": {"index": 1}, "text": "The quick brown fox jumps."}}]},
        )
        .execute()
    )

    try:
        result = await gdoc_suggest_edit(
            file_id=doc_id,
            find_text="quick",
            replace_text="slow",
        )

        assert result["file_id"]
        assert result["original_file_id"] == doc_id
        assert "suggestions" in result["note"].lower()

        # Clean up the new .docx
        await asyncio.to_thread(
            lambda: drive.files().delete(fileId=result["file_id"]).execute()
        )
    finally:
        # Clean up the temp Google Doc
        await asyncio.to_thread(
            lambda: drive.files().delete(fileId=doc_id).execute()
        )
