# gdoc_template_populate + gdoc_suggest_edit Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add two new MCP tools: `gdoc_template_populate` (copy a template → native Google Doc, replace placeholders) and `gdoc_suggest_edit` (export Google Doc as .docx, apply tracked change, re-upload as new .docx with suggestions).

**Architecture:** New `gdoc_ops.py` module houses both operations as pure async functions. `server.py` gets two new thin `@mcp.tool()` wrappers. Tests follow existing mock pattern (patch `auth.get_*_service`). Integration tests use `@pytest.mark.integration`.

**Tech Stack:** Python, Google Drive API v3, Google Docs API v1, existing `docx_edits.py`, pytest + pytest-asyncio

---

### Task 1: Create gdoc_ops module with template_populate

**Files:**
- Create: `src/gsuite_mcp/gdoc_ops.py`
- Test: `tests/test_gdoc_template_populate.py`

**Step 1: Write the failing test**

Create `tests/test_gdoc_template_populate.py`:

```python
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

    # files.copy returns new file metadata
    drive.files().copy.return_value.execute.return_value = {
        "id": "new123",
        "name": "My New Doc",
        "webViewLink": "https://docs.google.com/document/d/new123/edit",
    }
    # batchUpdate returns replacement counts
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

    # Verify files.copy was called with correct args
    copy_call = drive.files().copy.call_args
    assert copy_call.kwargs["fileId"] == "tmpl1"
    body = copy_call.kwargs["body"]
    assert body["name"] == "My New Doc"
    assert body["parents"] == ["folder1"]
    assert body["mimeType"] == "application/vnd.google-apps.document"

    # Verify batchUpdate had 2 replaceAllText requests
    batch_call = docs.documents().batchUpdate.call_args
    requests = batch_call.kwargs["body"]["requests"]
    assert len(requests) == 2
    assert requests[0]["replaceAllText"]["containsText"]["text"] == "{{NAME}}"
    assert requests[0]["replaceAllText"]["replaceText"] == "Alice"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gdoc_template_populate.py -v`
Expected: FAIL — `gdoc_template_populate` not found in server module

**Step 3: Write minimal implementation**

Create `src/gsuite_mcp/gdoc_ops.py`:

```python
"""Google Doc operations — template populate and suggest-edit via .docx export."""

import asyncio
from typing import Any

from gsuite_mcp.retry import retry_transient


GOOGLE_DOC_MIME = "application/vnd.google-apps.document"


async def template_populate(
    drive_service,
    docs_service,
    template_file_id: str,
    parent_folder_id: str,
    new_title: str,
    replacements: dict[str, str],
) -> dict[str, Any]:
    """Copy a template file as a native Google Doc and replace placeholders.

    Uses Drive files.copy with mimeType conversion, then a single
    Docs batchUpdate with replaceAllText for each placeholder.
    """
    copy_body = {
        "name": new_title,
        "parents": [parent_folder_id],
        "mimeType": GOOGLE_DOC_MIME,
    }
    copied = await asyncio.to_thread(
        lambda: drive_service.files()
        .copy(
            fileId=template_file_id,
            body=copy_body,
            fields="id,name,webViewLink",
        )
        .execute()
    )
    new_file_id = copied["id"]

    if not replacements:
        return {
            "file_id": new_file_id,
            "web_view_link": copied.get("webViewLink", ""),
            "replacements_made": {},
        }

    requests = [
        {
            "replaceAllText": {
                "containsText": {"text": placeholder, "matchCase": True},
                "replaceText": value,
            }
        }
        for placeholder, value in replacements.items()
    ]

    resp = await retry_transient(
        lambda: docs_service.documents()
        .batchUpdate(documentId=new_file_id, body={"requests": requests})
        .execute()
    )

    replacements_made = {}
    for (placeholder, _), reply in zip(replacements.items(), resp.get("replies", [])):
        count = reply.get("replaceAllText", {}).get("occurrencesChanged", 0)
        replacements_made[placeholder] = count

    return {
        "file_id": new_file_id,
        "web_view_link": copied.get("webViewLink", ""),
        "replacements_made": replacements_made,
    }
```

Add to `server.py` — import and tool wrapper:

Add import at top of `server.py`:
```python
from gsuite_mcp import auth, docs_ops, docx_edits, drive_ops, gdoc_ops, gmail_ops, sheets_ops
```

Add tool after `docx_suggest_edit`:
```python
@mcp.tool()
async def gdoc_template_populate(
    template_file_id: str,
    parent_folder_id: str,
    new_title: str,
    replacements: dict[str, str],
) -> dict[str, Any]:
    """Copy a template file as a native Google Doc and replace placeholders.

    Copies the template using Drive files.copy with automatic .docx-to-Google-Doc
    conversion, places it in the specified parent folder, then issues a single
    documents.batchUpdate with replaceAllText for each placeholder.

    Returns {file_id, web_view_link, replacements_made: {placeholder: count}}.
    """
    return await gdoc_ops.template_populate(
        drive_service=auth.get_drive_service(),
        docs_service=auth.get_docs_service(),
        template_file_id=template_file_id,
        parent_folder_id=parent_folder_id,
        new_title=new_title,
        replacements=replacements,
    )
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gdoc_template_populate.py -v`
Expected: PASS

**Step 5: Commit**

Commit message: `feat: add gdoc_template_populate tool`

---

### Task 2: Add edge-case tests for template_populate

**Files:**
- Modify: `tests/test_gdoc_template_populate.py`

**Step 1: Write additional failing tests**

Append to `tests/test_gdoc_template_populate.py`:

```python
@pytest.mark.asyncio
async def test_template_populate_empty_replacements(mock_services):
    drive = mock_services["drive"]
    docs = mock_services["docs"]

    drive.files().copy.return_value.execute.return_value = {
        "id": "new456",
        "name": "Empty",
        "webViewLink": "https://docs.google.com/document/d/new456/edit",
    }

    from gsuite_mcp.server import gdoc_template_populate
    result = await gdoc_template_populate(
        template_file_id="tmpl1",
        parent_folder_id="folder1",
        new_title="Empty",
        replacements={},
    )

    assert result["file_id"] == "new456"
    assert result["replacements_made"] == {}
    # batchUpdate should NOT be called with empty replacements
    docs.documents().batchUpdate.assert_not_called()


@pytest.mark.asyncio
async def test_template_populate_zero_occurrences(mock_services):
    drive = mock_services["drive"]
    docs = mock_services["docs"]

    drive.files().copy.return_value.execute.return_value = {
        "id": "new789",
        "name": "NoMatch",
        "webViewLink": "https://docs.google.com/document/d/new789/edit",
    }
    docs.documents().batchUpdate.return_value.execute.return_value = {
        "replies": [{"replaceAllText": {}}]  # no occurrencesChanged key
    }

    from gsuite_mcp.server import gdoc_template_populate
    result = await gdoc_template_populate(
        template_file_id="tmpl1",
        parent_folder_id="folder1",
        new_title="NoMatch",
        replacements={"{{MISSING}}": "value"},
    )

    assert result["replacements_made"] == {"{{MISSING}}": 0}
```

**Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/test_gdoc_template_populate.py -v`
Expected: PASS (implementation already handles these cases)

**Step 3: Commit**

Commit message: `test: add edge-case tests for gdoc_template_populate`

---

### Task 3: Create gdoc_suggest_edit ops function and tests

**Files:**
- Modify: `src/gsuite_mcp/gdoc_ops.py`
- Create: `tests/test_gdoc_suggest_edit.py`

**Step 1: Write the failing test**

Create `tests/test_gdoc_suggest_edit.py`:

```python
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
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gdoc_suggest_edit.py -v`
Expected: FAIL — `gdoc_suggest_edit` not found in server module

**Step 3: Write minimal implementation**

Add to `src/gsuite_mcp/gdoc_ops.py`:

```python
from gsuite_mcp import docx_edits

DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


async def suggest_edit(
    drive_service,
    file_id: str,
    find_text: str,
    replace_text: str,
    author: str = "Claude",
) -> dict[str, Any]:
    """Export a Google Doc as .docx, apply a tracked change, re-upload as new .docx.

    The original Google Doc is untouched. A new .docx file with tracked-change
    revision marks is created alongside it in the same parent folder.
    """
    meta = await asyncio.to_thread(
        lambda: drive_service.files()
        .get(fileId=file_id, fields="name,mimeType,parents")
        .execute()
    )

    if meta.get("mimeType") != GOOGLE_DOC_MIME:
        return {
            "error": "NOT_A_GOOGLE_DOC",
            "retryable": False,
            "message": (
                f"gdoc_suggest_edit only works on native Google Docs. "
                f"This file is {meta.get('mimeType')}. "
                f"For .docx files, use docx_suggest_edit directly."
            ),
        }

    exported = await asyncio.to_thread(
        lambda: drive_service.files()
        .export(fileId=file_id, mimeType=DOCX_MIME)
        .execute()
    )

    try:
        modified = docx_edits.insert_tracked_change(
            exported, find_text, replace_text, author
        )
    except docx_edits.NotFoundError as e:
        return {
            "error": "FIND_TEXT_NOT_FOUND",
            "retryable": False,
            "message": str(e),
        }
    except docx_edits.CrossParagraphError as e:
        return {
            "error": "CROSS_PARAGRAPH_MATCH",
            "retryable": False,
            "message": (
                f"{e} Split into per-paragraph edits and call this tool once "
                f"per paragraph."
            ),
        }

    import base64 as _b64
    from googleapiclient.http import MediaIoBaseUpload
    import io

    original_name = meta.get("name", "Untitled")
    new_name = f"{original_name} (with suggestions).docx"
    parents = meta.get("parents", [])

    file_bytes = modified
    media = MediaIoBaseUpload(
        io.BytesIO(file_bytes), mimetype=DOCX_MIME, resumable=True
    )
    body: dict[str, Any] = {"name": new_name}
    if parents:
        body["parents"] = parents

    result = await asyncio.to_thread(
        lambda: drive_service.files()
        .create(
            body=body,
            media_body=media,
            fields="id,name,webViewLink,version,modifiedTime",
        )
        .execute()
    )

    return {
        "file_id": result["id"],
        "file_name": result["name"],
        "web_view_link": result.get("webViewLink", ""),
        "original_file_id": file_id,
        "modified_time": result.get("modifiedTime", ""),
        "note": (
            "A new .docx file with tracked-change suggestions has been created. "
            "Open it to review the suggestions. The original Google Doc is unchanged."
        ),
    }
```

Add tool wrapper to `server.py` after `gdoc_template_populate`:

```python
@mcp.tool()
async def gdoc_suggest_edit(
    file_id: str,
    find_text: str,
    replace_text: str,
    author: str = "Claude",
) -> dict[str, Any]:
    """Create a .docx copy of a Google Doc with tracked-change suggestions.

    Exports the Google Doc as .docx, applies tracked-change revision marks
    for find_text → replace_text, and uploads the result as a new .docx file
    in the same folder. The original Google Doc is unchanged.

    Open the new .docx in Google Docs or Word to review suggestions.
    For .docx files already in Drive, use docx_suggest_edit instead.
    """
    return await gdoc_ops.suggest_edit(
        drive_service=auth.get_drive_service(),
        file_id=file_id,
        find_text=find_text,
        replace_text=replace_text,
        author=author,
    )
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gdoc_suggest_edit.py -v`
Expected: PASS

**Step 5: Commit**

Commit message: `feat: add gdoc_suggest_edit tool (export → tracked change → re-upload)`

---

### Task 4: Update CLAUDE.md and run full test suite

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Update CLAUDE.md**

In the Project Structure section, add:
```
- `src/gsuite_mcp/gdoc_ops.py` — Google Doc operations (template populate, suggest edit via .docx export)
```

In the Tools section, add entries 11 and 12:
```
11. `gdoc_template_populate` — copy template → native Google Doc, replace placeholders
12. `gdoc_suggest_edit` — export Google Doc as .docx, apply tracked change, re-upload as new .docx
```

Update test count to reflect new tests.

**Step 2: Run the full test suite**

Run: `uv run pytest -q`
Expected: All tests pass (existing 64 + new tests)

**Step 3: Lint**

Run: `uv run ruff check .`
Expected: No errors

**Step 4: Commit**

Commit message: `docs: update CLAUDE.md with new gdoc tools`

---

### Task 5: Add pytest integration marker and integration tests

**Files:**
- Modify: `pyproject.toml` (add integration marker)
- Create: `tests/test_integration_gdoc.py`

**Step 1: Register the integration marker**

In `pyproject.toml`, add under `[tool.pytest.ini_options]`:
```toml
markers = [
    "integration: tests that hit real Google APIs (require credentials)",
]
```

**Step 2: Write integration tests**

Create `tests/test_integration_gdoc.py`:

```python
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

    # Get template metadata to find its parent folder
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
```

**Step 3: Verify integration tests are skipped without creds**

Run: `uv run pytest tests/test_integration_gdoc.py -v`
Expected: Tests skipped with "No Google OAuth credentials"

**Step 4: Commit**

Commit message: `test: add integration tests for gdoc tools`
