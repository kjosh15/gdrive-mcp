# gdrive-mcp Expansion Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Expand gdrive-mcp from 4 tools to 9 tools, closing feedback gaps around native append, Google Docs formatting preservation, tracked changes for .docx, OAuth scope coverage, and batch metadata.

**Architecture:** Replace service-account auth with user OAuth. Split single `server.py` into per-API-surface modules (`auth.py`, `drive_ops.py`, `docs_ops.py`, `sheets_ops.py`, `docx_edits.py`). Thin `server.py` remains as FastMCP tool-decorator layer. Tests mirror module split with TDD for each new tool.

**Tech Stack:** Python 3.12+, FastMCP, google-api-python-client (Drive v3 + Docs v1 + Sheets v4), google-auth-oauthlib (setup CLI only), pytest, pytest-asyncio, ruff. `.docx` manipulation uses stdlib (`zipfile` + `xml.etree.ElementTree`) — no `python-docx`.

**Design doc:** `docs/plans/2026-04-10-gdrive-mcp-expansion-design.md`

**Implementation order:**
1. Auth migration (Tasks 1-4) — foundation; everything else depends on it
2. Module split without behavior changes (Tasks 5-7) — refactor existing 4 tools
3. Batch metadata (Task 8) — simplest new tool; warmup
4. Append tool (Tasks 9-11) — three paths, one per mime type
5. Replace text tool (Tasks 12-13) — simple + regex paths
6. Manage comments tool (Task 14) — consolidated CRUD
7. Docx suggest edit (Tasks 15-17) — OOXML pure function + integration
8. Documentation + cleanup (Task 18)

---

### Task 1: Create `auth.py` with OAuth credential loading

**Files:**
- Create: `src/gdrive_mcp/auth.py`
- Create: `tests/test_auth.py`
- Delete (later, Task 4): `src/gdrive_mcp/drive.py`
- Delete (later, Task 4): `tests/test_drive.py`

**Step 1: Write the failing tests**

Create `tests/test_auth.py`:

```python
import json
from unittest.mock import patch, MagicMock

import pytest


def test_get_credentials_from_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client123")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret456")
    monkeypatch.setenv("GOOGLE_OAUTH_REFRESH_TOKEN", "refresh789")

    with patch("gdrive_mcp.auth.Credentials") as mock_creds_cls, \
         patch("gdrive_mcp.auth.Request") as mock_request:
        mock_creds = MagicMock()
        mock_creds_cls.return_value = mock_creds

        from gdrive_mcp.auth import get_credentials, _reset_cache
        _reset_cache()
        creds = get_credentials()

        mock_creds_cls.assert_called_once_with(
            token=None,
            refresh_token="refresh789",
            token_uri="https://oauth2.googleapis.com/token",
            client_id="client123",
            client_secret="secret456",
            scopes=[
                "https://www.googleapis.com/auth/drive",
                "https://www.googleapis.com/auth/documents",
                "https://www.googleapis.com/auth/spreadsheets",
            ],
        )
        mock_creds.refresh.assert_called_once()
        assert creds is mock_creds


def test_get_credentials_caches(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client123")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret456")
    monkeypatch.setenv("GOOGLE_OAUTH_REFRESH_TOKEN", "refresh789")

    with patch("gdrive_mcp.auth.Credentials") as mock_creds_cls, \
         patch("gdrive_mcp.auth.Request"):
        mock_creds_cls.return_value = MagicMock()

        from gdrive_mcp.auth import get_credentials, _reset_cache
        _reset_cache()
        get_credentials()
        get_credentials()

        mock_creds_cls.assert_called_once()


def test_get_credentials_missing_env_raises(monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_REFRESH_TOKEN", raising=False)

    from gdrive_mcp.auth import get_credentials, _reset_cache, AuthError
    _reset_cache()
    with pytest.raises(AuthError, match="GOOGLE_OAUTH_"):
        get_credentials()


def test_service_factories_use_credentials(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "c")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "s")
    monkeypatch.setenv("GOOGLE_OAUTH_REFRESH_TOKEN", "r")

    with patch("gdrive_mcp.auth.Credentials"), \
         patch("gdrive_mcp.auth.Request"), \
         patch("gdrive_mcp.auth.build") as mock_build:
        from gdrive_mcp.auth import (
            get_drive_service, get_docs_service, get_sheets_service, _reset_cache,
        )
        _reset_cache()

        get_drive_service()
        get_docs_service()
        get_sheets_service()

        assert mock_build.call_args_list[0][0] == ("drive", "v3")
        assert mock_build.call_args_list[1][0] == ("docs", "v1")
        assert mock_build.call_args_list[2][0] == ("sheets", "v4")
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gdrive_mcp.auth'`

**Step 3: Write minimal implementation**

Create `src/gdrive_mcp/auth.py`:

```python
"""OAuth user credential loading for Google Drive, Docs, and Sheets APIs."""

import os
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
]

_cached_credentials: Optional[Credentials] = None


class AuthError(RuntimeError):
    """Raised when OAuth credentials cannot be loaded or refreshed."""


def _reset_cache() -> None:
    """Test helper to clear cached credentials."""
    global _cached_credentials
    _cached_credentials = None


def get_credentials() -> Credentials:
    """Load OAuth user credentials from env vars. Cached after first call."""
    global _cached_credentials
    if _cached_credentials is not None:
        return _cached_credentials

    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    refresh_token = os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN")

    missing = [
        name for name, val in [
            ("GOOGLE_OAUTH_CLIENT_ID", client_id),
            ("GOOGLE_OAUTH_CLIENT_SECRET", client_secret),
            ("GOOGLE_OAUTH_REFRESH_TOKEN", refresh_token),
        ]
        if not val
    ]
    if missing:
        raise AuthError(
            f"Missing required OAuth env vars: {', '.join(missing)}. "
            "Run `python -m gdrive_mcp.auth_setup` to generate a refresh token."
        )

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    creds.refresh(Request())
    _cached_credentials = creds
    return creds


def get_drive_service():
    return build("drive", "v3", credentials=get_credentials())


def get_docs_service():
    return build("docs", "v1", credentials=get_credentials())


def get_sheets_service():
    return build("sheets", "v4", credentials=get_credentials())
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_auth.py -v`
Expected: 4 tests PASS.

**Step 5: Commit**

Auto-commit hook will handle staging/committing after Edit/Write. If not, manually:

```bash
git add src/gdrive_mcp/auth.py tests/test_auth.py
git commit -m "feat: add OAuth user credential loader (auth.py)"
```

---

### Task 2: Add `google-auth-oauthlib` dependency to pyproject.toml

**Files:**
- Modify: `pyproject.toml`

**Step 1: Edit pyproject.toml**

Add `"google-auth-oauthlib>=1.2.0",` to the `dependencies` list (NOT dev deps — it's imported by `auth_setup.py` which ships with the package).

After edit, `dependencies` should be:
```toml
dependencies = [
    "fastmcp>=2.0.0",
    "google-api-python-client>=2.110.0",
    "google-auth>=2.25.0",
    "google-auth-oauthlib>=1.2.0",
    "uvicorn>=0.30.0",
]
```

Also update the project description:
```toml
description = "Google Drive, Docs, and Sheets MCP server with append, replace, tracked-changes, and comment support"
```

**Step 2: Sync dependencies**

Run: `uv sync --all-extras`
Expected: installs `google-auth-oauthlib`. No errors.

**Step 3: Verify import works**

Run: `uv run python -c "from google_auth_oauthlib.flow import InstalledAppFlow; print('ok')"`
Expected: prints `ok`.

**Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add google-auth-oauthlib dependency for OAuth setup CLI"
```

---

### Task 3: Create `auth_setup.py` CLI for one-time OAuth consent

**Files:**
- Create: `src/gdrive_mcp/auth_setup.py`

**Step 1: Write the implementation (no tests — it's an interactive CLI)**

Create `src/gdrive_mcp/auth_setup.py`:

```python
"""One-time OAuth consent flow CLI. Prints a refresh token to stdout.

Usage:
    GOOGLE_OAUTH_CLIENT_ID=... GOOGLE_OAUTH_CLIENT_SECRET=... \
        python -m gdrive_mcp.auth_setup
"""

import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

from gdrive_mcp.auth import SCOPES


def main() -> int:
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    if not client_id or not client_secret:
        print(
            "ERROR: Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET "
            "before running this command.\n"
            "Create a Desktop OAuth client at "
            "https://console.cloud.google.com/apis/credentials",
            file=sys.stderr,
        )
        return 1

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

    print("\n=== SUCCESS ===")
    print("Set this in your environment:")
    print(f"\nexport GOOGLE_OAUTH_REFRESH_TOKEN='{creds.refresh_token}'\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

**Step 2: Smoke-test the error path**

Run: `unset GOOGLE_OAUTH_CLIENT_ID GOOGLE_OAUTH_CLIENT_SECRET && uv run python -m gdrive_mcp.auth_setup`
Expected: prints the ERROR message to stderr, exits 1.

(Do NOT smoke-test the happy path — it requires real GCP credentials and opens a browser.)

**Step 3: Commit**

```bash
git add src/gdrive_mcp/auth_setup.py
git commit -m "feat: add auth_setup CLI for one-time OAuth consent flow"
```

---

### Task 4: Delete old `drive.py` and `test_drive.py`

**Files:**
- Delete: `src/gdrive_mcp/drive.py`
- Delete: `tests/test_drive.py`
- Modify: any imports in `server.py` (deferred to Task 5 which rewrites server.py)

**Step 1: Verify nothing still imports from the old module**

Run: `uv run python -c "from gdrive_mcp.auth import get_drive_service; print('ok')"`
Expected: prints `ok`.

Do NOT delete yet if `server.py` still imports from `gdrive_mcp.drive`. Check:
Run: `grep -n 'from gdrive_mcp.drive' src/gdrive_mcp/server.py`

If grep finds anything, STOP — Task 5 must land first. Otherwise proceed.

**Step 2: Delete the files**

```bash
rm src/gdrive_mcp/drive.py tests/test_drive.py
```

**Step 3: Run full test suite**

Run: `uv run pytest -q`
Expected: all tests pass (or the pre-existing tests still work via `server.py`'s imports).

**Step 4: Commit**

```bash
git add -u src/gdrive_mcp/drive.py tests/test_drive.py
git commit -m "chore: remove legacy service-account drive.py in favor of auth.py"
```

---

### Task 5: Create `drive_ops.py` with the 4 existing Drive operations

**Files:**
- Create: `src/gdrive_mcp/drive_ops.py`
- Modify: `src/gdrive_mcp/server.py` (thin wrapper only)

**Goal:** Move the existing `download_file`, `upload_file`, `search_files`, `get_file_metadata` logic out of `server.py` and into `drive_ops.py` as plain async functions that accept a `service` argument. `server.py` becomes a thin `@mcp.tool()` wrapper. Behavior is unchanged EXCEPT `upload_file` loses its `storageQuotaExceeded` branch (dead code under OAuth).

**Step 1: Create `src/gdrive_mcp/drive_ops.py`**

```python
"""Google Drive v3 operations — pure async functions that accept a service."""

import asyncio
import base64
import io
from typing import Any, Optional

from googleapiclient.http import MediaIoBaseUpload


async def download_file(
    service,
    file_id: str,
    export_format: Optional[str] = None,
) -> dict[str, Any]:
    metadata = await asyncio.to_thread(
        lambda: service.files()
        .get(fileId=file_id, fields="name,mimeType,size")
        .execute()
    )
    if export_format:
        content = await asyncio.to_thread(
            lambda: service.files()
            .export(fileId=file_id, mimeType=export_format)
            .execute()
        )
    else:
        content = await asyncio.to_thread(
            lambda: service.files().get_media(fileId=file_id).execute()
        )
    return {
        "file_id": file_id,
        "file_name": metadata["name"],
        "mime_type": metadata.get("mimeType", ""),
        "size_bytes": len(content),
        "content_base64": base64.b64encode(content).decode(),
    }


async def upload_file(
    service,
    content_base64: str,
    file_name: str,
    mime_type: str,
    file_id: Optional[str] = None,
    parent_folder_id: Optional[str] = None,
) -> dict[str, Any]:
    file_bytes = base64.b64decode(content_base64)
    media = MediaIoBaseUpload(
        io.BytesIO(file_bytes), mimetype=mime_type, resumable=True
    )
    if file_id:
        result = await asyncio.to_thread(
            lambda: service.files()
            .update(
                fileId=file_id,
                media_body=media,
                fields="id,name,webViewLink,version,modifiedTime",
            )
            .execute()
        )
    else:
        body: dict[str, Any] = {"name": file_name}
        if parent_folder_id:
            body["parents"] = [parent_folder_id]
        result = await asyncio.to_thread(
            lambda: service.files()
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
        "version": result.get("version", ""),
        "modified_time": result.get("modifiedTime", ""),
    }


async def search_files(service, query: str, max_results: int = 10) -> dict[str, Any]:
    response = await asyncio.to_thread(
        lambda: service.files()
        .list(
            q=query,
            pageSize=max_results,
            fields="files(id,name,mimeType,modifiedTime,webViewLink,parents)",
        )
        .execute()
    )
    return {
        "files": [
            {
                "file_id": f["id"],
                "name": f["name"],
                "mime_type": f.get("mimeType", ""),
                "modified_time": f.get("modifiedTime", ""),
                "web_view_link": f.get("webViewLink", ""),
                "parents": f.get("parents", []),
            }
            for f in response.get("files", [])
        ]
    }


async def get_file_metadata(service, file_id: str) -> dict[str, Any]:
    metadata = await asyncio.to_thread(
        lambda: service.files()
        .get(
            fileId=file_id,
            fields="id,name,mimeType,size,modifiedTime,webViewLink,parents,capabilities",
        )
        .execute()
    )
    return {
        "file_id": metadata["id"],
        "name": metadata["name"],
        "mime_type": metadata.get("mimeType", ""),
        "size_bytes": int(metadata.get("size", 0)),
        "modified_time": metadata.get("modifiedTime", ""),
        "web_view_link": metadata.get("webViewLink", ""),
        "parents": metadata.get("parents", []),
        "capabilities": metadata.get("capabilities", {}),
    }
```

**Step 2: Rewrite `src/gdrive_mcp/server.py` as thin wrappers**

```python
"""Google Drive MCP server — thin wrappers over *_ops modules."""

import logging
import os
import sys
from typing import Any, Optional

from fastmcp import FastMCP

from gdrive_mcp import drive_ops
from gdrive_mcp.auth import get_drive_service

mcp = FastMCP("gdrive-mcp")


@mcp.tool()
async def download_file(
    file_id: str,
    export_format: Optional[str] = None,
) -> dict[str, Any]:
    """Download a file from Google Drive by file ID.

    For native Google formats (Docs, Sheets), use export_format to convert.
    """
    return await drive_ops.download_file(
        get_drive_service(), file_id, export_format
    )


@mcp.tool()
async def upload_file(
    content_base64: str,
    file_name: str,
    mime_type: str,
    file_id: Optional[str] = None,
    parent_folder_id: Optional[str] = None,
) -> dict[str, Any]:
    """Upload a file to Google Drive (create or update)."""
    return await drive_ops.upload_file(
        get_drive_service(),
        content_base64,
        file_name,
        mime_type,
        file_id,
        parent_folder_id,
    )


@mcp.tool()
async def search_files(query: str, max_results: int = 10) -> dict[str, Any]:
    """Search Google Drive for files. Uses Drive API query syntax."""
    return await drive_ops.search_files(get_drive_service(), query, max_results)


@mcp.tool()
async def get_file_metadata(file_id: str) -> dict[str, Any]:
    """Get metadata for a Google Drive file without downloading its content."""
    return await drive_ops.get_file_metadata(get_drive_service(), file_id)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
        force=True,
    )
    import uvicorn

    app = mcp.http_app(stateless_http=True)
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
```

**Step 3: Update existing tests to patch `auth.get_drive_service` instead of `server.get_drive_service`**

In each of `tests/test_upload.py`, `tests/test_download.py`, `tests/test_search.py`, `tests/test_metadata.py`, the `mock_drive` fixture currently patches `"gdrive_mcp.server.get_drive_service"`. Change each to `"gdrive_mcp.auth.get_drive_service"`.

Also in `tests/test_upload.py`, DELETE these tests (they test the dead quota-branch):
- `test_upload_create_on_personal_drive_returns_terminal_error`
- `_quota_exceeded_error` helper
- The import of `HttpError` if only used by those deleted tests

Keep:
- `test_upload_new_file`
- `test_upload_update_existing`
- `test_upload_update_still_raises_unknown_errors`

**Step 4: Run the full test suite**

Run: `uv run pytest -q`
Expected: all tests PASS. Test count drops by 1 (deleted quota test).

**Step 5: Run ruff**

Run: `uv run ruff check .`
Expected: clean.

**Step 6: Commit**

```bash
git add src/gdrive_mcp/drive_ops.py src/gdrive_mcp/server.py tests/
git commit -m "refactor: split drive operations into drive_ops module; remove dead quota branch"
```

---

### Task 6: Create empty `docs_ops.py` and `sheets_ops.py` module skeletons

**Files:**
- Create: `src/gdrive_mcp/docs_ops.py`
- Create: `src/gdrive_mcp/sheets_ops.py`

**Step 1: Create both files with just a module docstring**

`src/gdrive_mcp/docs_ops.py`:
```python
"""Google Docs v1 operations — append, replace_text."""
```

`src/gdrive_mcp/sheets_ops.py`:
```python
"""Google Sheets v4 operations — append rows."""
```

This keeps imports working as later tasks add real functions.

**Step 2: Commit**

```bash
git add src/gdrive_mcp/docs_ops.py src/gdrive_mcp/sheets_ops.py
git commit -m "chore: add docs_ops and sheets_ops module skeletons"
```

---

### Task 7: Verify the refactored app still starts

**Files:** none

**Step 1: Dry-run module import**

Run: `uv run python -c "from gdrive_mcp.server import mcp; print(list(mcp._tool_manager._tools.keys()) if hasattr(mcp, '_tool_manager') else 'ok')"`

Expected: prints `ok` or a list of 4 tool names (`download_file`, `upload_file`, `search_files`, `get_file_metadata`). Exact attribute may vary by FastMCP version; any non-error output is acceptable.

**Step 2: Run all tests one more time**

Run: `uv run pytest -q && uv run ruff check .`
Expected: tests pass, ruff clean.

No commit (no code change).

---

### Task 8: Add `get_files_metadata` batch tool

**Files:**
- Modify: `src/gdrive_mcp/drive_ops.py`
- Modify: `src/gdrive_mcp/server.py`
- Create: `tests/test_get_files_metadata.py`

**Step 1: Write the failing tests**

Create `tests/test_get_files_metadata.py`:

```python
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def mock_drive():
    with patch("gdrive_mcp.auth.get_drive_service") as mock:
        service = MagicMock()
        mock.return_value = service
        yield service


@pytest.mark.asyncio
async def test_get_files_metadata_batch_success(mock_drive):
    """Batch get metadata for multiple file IDs returns all results."""
    def fake_get(fileId, fields):
        mock_exec = MagicMock()
        mock_exec.execute.return_value = {
            "id": fileId,
            "name": f"file_{fileId}.docx",
            "mimeType": "application/vnd.google-apps.document",
            "size": "100",
            "modifiedTime": "2026-04-01T10:00:00Z",
        }
        return mock_exec

    mock_drive.files().get.side_effect = fake_get

    from gdrive_mcp.server import get_files_metadata

    result = await get_files_metadata(file_ids=["a", "b", "c"])

    assert len(result["results"]) == 3
    assert {r["file_id"] for r in result["results"]} == {"a", "b", "c"}
    assert result["errors"] == []


@pytest.mark.asyncio
async def test_get_files_metadata_partial_failure(mock_drive):
    """One failing file doesn't abort the rest."""
    def fake_get(fileId, fields):
        mock_exec = MagicMock()
        if fileId == "bad":
            mock_exec.execute.side_effect = RuntimeError("boom")
        else:
            mock_exec.execute.return_value = {
                "id": fileId,
                "name": fileId,
                "mimeType": "x",
                "size": "0",
                "modifiedTime": "2026-04-01T10:00:00Z",
            }
        return mock_exec

    mock_drive.files().get.side_effect = fake_get

    from gdrive_mcp.server import get_files_metadata

    result = await get_files_metadata(file_ids=["good1", "bad", "good2"])

    assert len(result["results"]) == 2
    assert {r["file_id"] for r in result["results"]} == {"good1", "good2"}
    assert len(result["errors"]) == 1
    assert result["errors"][0]["file_id"] == "bad"
    assert "boom" in result["errors"][0]["error"]


@pytest.mark.asyncio
async def test_get_files_metadata_empty_list(mock_drive):
    from gdrive_mcp.server import get_files_metadata
    result = await get_files_metadata(file_ids=[])
    assert result == {"results": [], "errors": []}
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_get_files_metadata.py -v`
Expected: FAIL with `ImportError: cannot import name 'get_files_metadata'`.

**Step 3: Add the ops function to `drive_ops.py`**

Append to `src/gdrive_mcp/drive_ops.py`:

```python
async def get_files_metadata(
    service, file_ids: list[str]
) -> dict[str, Any]:
    """Batch get metadata for N file IDs concurrently."""
    async def one(fid: str) -> dict[str, Any]:
        return await get_file_metadata(service, fid)

    gathered = await asyncio.gather(
        *(one(fid) for fid in file_ids),
        return_exceptions=True,
    )
    results = []
    errors = []
    for fid, outcome in zip(file_ids, gathered):
        if isinstance(outcome, Exception):
            errors.append({"file_id": fid, "error": str(outcome)})
        else:
            results.append(outcome)
    return {"results": results, "errors": errors}
```

**Step 4: Add the MCP tool to `server.py`**

Append below `get_file_metadata`:

```python
@mcp.tool()
async def get_files_metadata(file_ids: list[str]) -> dict[str, Any]:
    """Batch get metadata for multiple file IDs concurrently.

    Returns {results: [...], errors: [{file_id, error}]}. Partial failures
    do not abort the whole batch — failed IDs appear in errors.
    """
    return await drive_ops.get_files_metadata(get_drive_service(), file_ids)
```

**Step 5: Run tests**

Run: `uv run pytest tests/test_get_files_metadata.py -v`
Expected: 3 tests PASS.

**Step 6: Commit**

```bash
git add src/gdrive_mcp/drive_ops.py src/gdrive_mcp/server.py tests/test_get_files_metadata.py
git commit -m "feat: add get_files_metadata batch tool"
```

---

### Task 9: `append_to_file` — Google Docs native path

**Files:**
- Modify: `src/gdrive_mcp/docs_ops.py`
- Create: `tests/test_append.py`

**Step 1: Write the first failing test (docs_native path only)**

Create `tests/test_append.py`:

```python
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def mock_services():
    """Mock drive, docs, and sheets services."""
    with patch("gdrive_mcp.auth.get_drive_service") as mock_drive, \
         patch("gdrive_mcp.auth.get_docs_service") as mock_docs, \
         patch("gdrive_mcp.auth.get_sheets_service") as mock_sheets:
        drive = MagicMock()
        docs = MagicMock()
        sheets = MagicMock()
        mock_drive.return_value = drive
        mock_docs.return_value = docs
        mock_sheets.return_value = sheets
        yield {"drive": drive, "docs": docs, "sheets": sheets}


@pytest.mark.asyncio
async def test_append_to_google_doc_uses_docs_api(mock_services):
    """Appending to a Google Doc uses Docs API batchUpdate with InsertTextRequest."""
    drive = mock_services["drive"]
    docs = mock_services["docs"]

    # files.get returns a Google Doc
    drive.files().get.return_value.execute.return_value = {
        "name": "Index",
        "mimeType": "application/vnd.google-apps.document",
        "modifiedTime": "2026-04-10T12:00:00Z",
    }
    # documents.get returns a doc body with endIndex
    docs.documents().get.return_value.execute.return_value = {
        "body": {
            "content": [
                {"endIndex": 1},
                {"endIndex": 42},
            ]
        }
    }
    docs.documents().batchUpdate.return_value.execute.return_value = {}

    from gdrive_mcp.server import append_to_file
    result = await append_to_file(
        file_id="doc123", content="new line", separator="\n"
    )

    assert result["mode"] == "docs_native"
    assert result["file_id"] == "doc123"
    assert result["bytes_appended"] > 0

    # verify batchUpdate was called with InsertTextRequest
    call_args = docs.documents().batchUpdate.call_args
    requests = call_args.kwargs["body"]["requests"]
    assert len(requests) == 1
    assert "insertText" in requests[0]
    assert requests[0]["insertText"]["text"] == "\nnew line"
```

**Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_append.py::test_append_to_google_doc_uses_docs_api -v`
Expected: FAIL — `append_to_file` doesn't exist in `server`.

**Step 3: Implement `append_text_to_doc` in `docs_ops.py`**

Replace `src/gdrive_mcp/docs_ops.py` with:

```python
"""Google Docs v1 operations — append, replace_text."""

import asyncio
from typing import Any


async def append_text_to_doc(
    docs_service, file_id: str, text: str
) -> dict[str, Any]:
    """Append text at end-of-body of a Google Doc. Preserves formatting."""
    doc = await asyncio.to_thread(
        lambda: docs_service.documents()
        .get(documentId=file_id, fields="body(content(endIndex))")
        .execute()
    )
    end_index = 1
    for element in doc.get("body", {}).get("content", []):
        end_index = max(end_index, element.get("endIndex", 1))
    insert_index = max(1, end_index - 1)
    requests = [
        {
            "insertText": {
                "location": {"index": insert_index},
                "text": text,
            }
        }
    ]
    await asyncio.to_thread(
        lambda: docs_service.documents()
        .batchUpdate(documentId=file_id, body={"requests": requests})
        .execute()
    )
    return {"bytes_appended": len(text.encode("utf-8"))}
```

**Step 4: Add `append_to_file` MCP tool to `server.py`**

Add imports:
```python
from gdrive_mcp import drive_ops, docs_ops, sheets_ops
from gdrive_mcp.auth import (
    get_drive_service, get_docs_service, get_sheets_service,
)
```

Add tool:

```python
GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
GOOGLE_SHEET_MIME = "application/vnd.google-apps.spreadsheet"


@mcp.tool()
async def append_to_file(
    file_id: str,
    content: str,
    separator: str = "\n",
) -> dict[str, Any]:
    """Append content to a file. Uses native API where possible.

    - Google Docs: Docs API batchUpdate InsertText (preserves formatting)
    - Google Sheets: Sheets API values.append (rows split on newline, cols on comma)
    - Other files: download-concat-upload fallback

    Returns {file_id, file_name, mime_type, bytes_appended, modified_time, mode}.
    """
    drive = get_drive_service()
    meta = await asyncio.to_thread(
        lambda: drive.files()
        .get(fileId=file_id, fields="name,mimeType,modifiedTime")
        .execute()
    )
    mime = meta.get("mimeType", "")
    name = meta.get("name", "")

    if mime == GOOGLE_DOC_MIME:
        docs = get_docs_service()
        ops_result = await docs_ops.append_text_to_doc(
            docs, file_id, separator + content
        )
        mode = "docs_native"
        # refresh modifiedTime
        meta2 = await asyncio.to_thread(
            lambda: drive.files()
            .get(fileId=file_id, fields="modifiedTime")
            .execute()
        )
        modified_time = meta2.get("modifiedTime", "")
    elif mime == GOOGLE_SHEET_MIME:
        # implemented in Task 10
        raise NotImplementedError("Sheets path added in Task 10")
    else:
        # implemented in Task 11
        raise NotImplementedError("Plain-file path added in Task 11")

    return {
        "file_id": file_id,
        "file_name": name,
        "mime_type": mime,
        "bytes_appended": ops_result["bytes_appended"],
        "modified_time": modified_time,
        "mode": mode,
    }
```

Also add `import asyncio` at top of `server.py` if not already present.

**Step 5: Run the one test**

Run: `uv run pytest tests/test_append.py::test_append_to_google_doc_uses_docs_api -v`
Expected: PASS.

**Step 6: Commit**

```bash
git add src/gdrive_mcp/docs_ops.py src/gdrive_mcp/server.py tests/test_append.py
git commit -m "feat: append_to_file (Google Docs native path)"
```

---

### Task 10: `append_to_file` — Google Sheets native path

**Files:**
- Modify: `src/gdrive_mcp/sheets_ops.py`
- Modify: `src/gdrive_mcp/server.py`
- Modify: `tests/test_append.py`

**Step 1: Add the failing test**

Append to `tests/test_append.py`:

```python
@pytest.mark.asyncio
async def test_append_to_google_sheet_uses_sheets_api(mock_services):
    """Appending to a Google Sheet uses Sheets API values.append."""
    drive = mock_services["drive"]
    sheets = mock_services["sheets"]

    drive.files().get.return_value.execute.return_value = {
        "name": "Pipeline",
        "mimeType": "application/vnd.google-apps.spreadsheet",
        "modifiedTime": "2026-04-10T12:00:00Z",
    }
    # spreadsheets.get returns sheet metadata so we can find the first sheet title
    sheets.spreadsheets().get.return_value.execute.return_value = {
        "sheets": [{"properties": {"title": "Sheet1"}}],
    }
    sheets.spreadsheets().values().append.return_value.execute.return_value = {
        "updates": {"updatedRange": "Sheet1!A42:C42"}
    }

    from gdrive_mcp.server import append_to_file
    result = await append_to_file(
        file_id="sheet123",
        content="col1,col2,col3\nrow2c1,row2c2,row2c3",
        separator="",
    )

    assert result["mode"] == "sheets_native"
    assert result["bytes_appended"] > 0

    # verify values.append was called with parsed rows
    call_args = sheets.spreadsheets().values().append.call_args
    assert call_args.kwargs["range"] == "Sheet1"
    assert call_args.kwargs["valueInputOption"] == "USER_ENTERED"
    body = call_args.kwargs["body"]
    assert body["values"] == [
        ["col1", "col2", "col3"],
        ["row2c1", "row2c2", "row2c3"],
    ]
```

**Step 2: Run to verify failure**

Run: `uv run pytest tests/test_append.py::test_append_to_google_sheet_uses_sheets_api -v`
Expected: FAIL with `NotImplementedError: Sheets path added in Task 10`.

**Step 3: Implement `append_rows` in `sheets_ops.py`**

Replace `src/gdrive_mcp/sheets_ops.py`:

```python
"""Google Sheets v4 operations — append rows."""

import asyncio
from typing import Any


async def append_rows(
    sheets_service, file_id: str, content: str
) -> dict[str, Any]:
    """Append rows to the first sheet of a spreadsheet.

    Content is split on newlines into rows, then on commas into columns.
    Uses USER_ENTERED so formulas are evaluated.
    """
    meta = await asyncio.to_thread(
        lambda: sheets_service.spreadsheets()
        .get(spreadsheetId=file_id, fields="sheets(properties(title))")
        .execute()
    )
    first_sheet_title = meta["sheets"][0]["properties"]["title"]

    rows = [
        [cell.strip() for cell in line.split(",")]
        for line in content.splitlines()
        if line.strip()
    ]

    await asyncio.to_thread(
        lambda: sheets_service.spreadsheets()
        .values()
        .append(
            spreadsheetId=file_id,
            range=first_sheet_title,
            valueInputOption="USER_ENTERED",
            body={"values": rows},
        )
        .execute()
    )
    return {"bytes_appended": len(content.encode("utf-8")), "rows_added": len(rows)}
```

**Step 4: Wire up the sheets branch in `server.py`**

Replace the `NotImplementedError` in the sheets branch with:

```python
elif mime == GOOGLE_SHEET_MIME:
    sheets = get_sheets_service()
    ops_result = await sheets_ops.append_rows(sheets, file_id, content)
    mode = "sheets_native"
    meta2 = await asyncio.to_thread(
        lambda: drive.files()
        .get(fileId=file_id, fields="modifiedTime")
        .execute()
    )
    modified_time = meta2.get("modifiedTime", "")
```

**Step 5: Run tests**

Run: `uv run pytest tests/test_append.py -v`
Expected: both tests PASS.

**Step 6: Commit**

```bash
git add src/gdrive_mcp/sheets_ops.py src/gdrive_mcp/server.py tests/test_append.py
git commit -m "feat: append_to_file (Google Sheets native path)"
```

---

### Task 11: `append_to_file` — plain-file download-concat-upload fallback

**Files:**
- Modify: `src/gdrive_mcp/server.py`
- Modify: `tests/test_append.py`

**Step 1: Add failing test**

Append to `tests/test_append.py`:

```python
import base64


@pytest.mark.asyncio
async def test_append_to_plain_file_roundtrips(mock_services):
    """Appending to a plain file downloads, concats, and re-uploads."""
    drive = mock_services["drive"]

    drive.files().get.return_value.execute.return_value = {
        "name": "notes.md",
        "mimeType": "text/markdown",
        "modifiedTime": "2026-04-10T12:00:00Z",
    }
    drive.files().get_media.return_value.execute.return_value = b"existing content"
    drive.files().update.return_value.execute.return_value = {
        "id": "plain1",
        "name": "notes.md",
        "webViewLink": "https://example.com",
        "version": "2",
        "modifiedTime": "2026-04-10T12:05:00Z",
    }

    from gdrive_mcp.server import append_to_file
    result = await append_to_file(
        file_id="plain1", content="new line", separator="\n"
    )

    assert result["mode"] == "plain_roundtrip"
    assert result["bytes_appended"] == len(b"\nnew line")
    # verify update was called with concatenated content
    drive.files().update.assert_called_once()
```

**Step 2: Run to verify failure**

Run: `uv run pytest tests/test_append.py::test_append_to_plain_file_roundtrips -v`
Expected: FAIL with `NotImplementedError: Plain-file path added in Task 11`.

**Step 3: Implement the fallback in `server.py`**

Replace the plain-file `NotImplementedError` branch with:

```python
else:
    # Plain file: download, concat, upload
    current = await asyncio.to_thread(
        lambda: drive.files().get_media(fileId=file_id).execute()
    )
    to_append = (separator + content).encode("utf-8")
    new_bytes = current + to_append
    import base64 as _b64
    upload_result = await drive_ops.upload_file(
        drive,
        content_base64=_b64.b64encode(new_bytes).decode(),
        file_name=name,
        mime_type=mime,
        file_id=file_id,
    )
    mode = "plain_roundtrip"
    modified_time = upload_result.get("modified_time", "")
    ops_result = {"bytes_appended": len(to_append)}
```

**Step 4: Run all append tests**

Run: `uv run pytest tests/test_append.py -v`
Expected: 3 tests PASS.

**Step 5: Commit**

```bash
git add src/gdrive_mcp/server.py tests/test_append.py
git commit -m "feat: append_to_file (plain-file roundtrip fallback)"
```

---

### Task 12: `replace_text` — exact match path

**Files:**
- Modify: `src/gdrive_mcp/docs_ops.py`
- Modify: `src/gdrive_mcp/server.py`
- Create: `tests/test_replace_text.py`

**Step 1: Write failing tests for exact-match replace_text**

Create `tests/test_replace_text.py`:

```python
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def mock_services():
    with patch("gdrive_mcp.auth.get_drive_service") as mock_drive, \
         patch("gdrive_mcp.auth.get_docs_service") as mock_docs:
        drive = MagicMock()
        docs = MagicMock()
        mock_drive.return_value = drive
        mock_docs.return_value = docs
        yield {"drive": drive, "docs": docs}


@pytest.mark.asyncio
async def test_replace_text_exact_match(mock_services):
    drive = mock_services["drive"]
    docs = mock_services["docs"]
    drive.files().get.return_value.execute.return_value = {
        "name": "doc", "mimeType": "application/vnd.google-apps.document",
        "modifiedTime": "2026-04-10T12:00:00Z",
    }
    docs.documents().batchUpdate.return_value.execute.return_value = {
        "replies": [{"replaceAllText": {"occurrencesChanged": 3}}]
    }

    from gdrive_mcp.server import replace_text
    result = await replace_text(
        file_id="d1", find="foo", replace="bar", match_case=True, regex=False
    )

    assert result["replacements_made"] == 3
    assert result["regex_mode"] is False
    call_args = docs.documents().batchUpdate.call_args
    req = call_args.kwargs["body"]["requests"][0]
    assert req["replaceAllText"]["containsText"] == {"text": "foo", "matchCase": True}
    assert req["replaceAllText"]["replaceText"] == "bar"


@pytest.mark.asyncio
async def test_replace_text_case_insensitive(mock_services):
    drive = mock_services["drive"]
    docs = mock_services["docs"]
    drive.files().get.return_value.execute.return_value = {
        "name": "doc", "mimeType": "application/vnd.google-apps.document",
        "modifiedTime": "2026-04-10T12:00:00Z",
    }
    docs.documents().batchUpdate.return_value.execute.return_value = {
        "replies": [{"replaceAllText": {"occurrencesChanged": 1}}]
    }

    from gdrive_mcp.server import replace_text
    await replace_text(file_id="d1", find="Foo", replace="bar", match_case=False)

    req = docs.documents().batchUpdate.call_args.kwargs["body"]["requests"][0]
    assert req["replaceAllText"]["containsText"]["matchCase"] is False


@pytest.mark.asyncio
async def test_replace_text_not_a_google_doc_returns_error(mock_services):
    drive = mock_services["drive"]
    drive.files().get.return_value.execute.return_value = {
        "name": "file.docx",
        "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "modifiedTime": "2026-04-10T12:00:00Z",
    }

    from gdrive_mcp.server import replace_text
    result = await replace_text(file_id="d1", find="x", replace="y")

    assert result["error"] == "NOT_A_GOOGLE_DOC"
    assert result["retryable"] is False
    assert "docx_suggest_edit" in result["message"]


@pytest.mark.asyncio
async def test_replace_text_zero_matches(mock_services):
    drive = mock_services["drive"]
    docs = mock_services["docs"]
    drive.files().get.return_value.execute.return_value = {
        "name": "doc", "mimeType": "application/vnd.google-apps.document",
        "modifiedTime": "2026-04-10T12:00:00Z",
    }
    docs.documents().batchUpdate.return_value.execute.return_value = {
        "replies": [{"replaceAllText": {}}]  # no occurrencesChanged key
    }

    from gdrive_mcp.server import replace_text
    result = await replace_text(file_id="d1", find="nothing", replace="y")
    assert result["replacements_made"] == 0
```

**Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_replace_text.py -v`
Expected: FAIL with ImportError.

**Step 3: Implement `replace_all_text` in `docs_ops.py`**

Append to `src/gdrive_mcp/docs_ops.py`:

```python
async def replace_all_text(
    docs_service, file_id: str, find: str, replace: str, match_case: bool
) -> int:
    """Exact-match replace across a Google Doc. Returns occurrence count."""
    requests = [
        {
            "replaceAllText": {
                "containsText": {"text": find, "matchCase": match_case},
                "replaceText": replace,
            }
        }
    ]
    resp = await asyncio.to_thread(
        lambda: docs_service.documents()
        .batchUpdate(documentId=file_id, body={"requests": requests})
        .execute()
    )
    reply = resp.get("replies", [{}])[0]
    return reply.get("replaceAllText", {}).get("occurrencesChanged", 0)
```

**Step 4: Add `replace_text` tool to `server.py`**

Append to `server.py`:

```python
@mcp.tool()
async def replace_text(
    file_id: str,
    find: str,
    replace: str,
    match_case: bool = True,
    regex: bool = False,
) -> dict[str, Any]:
    """Replace text in a Google Doc. Exact match by default; regex optional.

    Only works on Google Docs (mimeType application/vnd.google-apps.document).
    For real .docx files, use docx_suggest_edit instead.
    """
    drive = get_drive_service()
    meta = await asyncio.to_thread(
        lambda: drive.files()
        .get(fileId=file_id, fields="name,mimeType,modifiedTime")
        .execute()
    )
    if meta.get("mimeType") != GOOGLE_DOC_MIME:
        return {
            "error": "NOT_A_GOOGLE_DOC",
            "retryable": False,
            "message": (
                f"replace_text only works on Google Docs. This file is "
                f"{meta.get('mimeType')}. For real .docx files, use "
                f"docx_suggest_edit. For other files, download/edit/upload."
            ),
        }
    docs = get_docs_service()

    if regex:
        # Task 13: regex fallback
        raise NotImplementedError("regex path added in Task 13")

    count = await docs_ops.replace_all_text(docs, file_id, find, replace, match_case)
    meta2 = await asyncio.to_thread(
        lambda: drive.files()
        .get(fileId=file_id, fields="modifiedTime")
        .execute()
    )
    return {
        "file_id": file_id,
        "replacements_made": count,
        "regex_mode": False,
        "modified_time": meta2.get("modifiedTime", ""),
    }
```

**Step 5: Run tests**

Run: `uv run pytest tests/test_replace_text.py -v`
Expected: 4 tests PASS.

**Step 6: Commit**

```bash
git add src/gdrive_mcp/docs_ops.py src/gdrive_mcp/server.py tests/test_replace_text.py
git commit -m "feat: replace_text for Google Docs (exact match)"
```

---

### Task 13: `replace_text` — regex path

**Files:**
- Modify: `src/gdrive_mcp/docs_ops.py`
- Modify: `src/gdrive_mcp/server.py`
- Modify: `tests/test_replace_text.py`

**Background:** Docs API's `ReplaceAllTextRequest` doesn't support regex. We fetch the document body text with index offsets, run `re.finditer` client-side, and build pairs of `DeleteContentRangeRequest` + `InsertTextRequest` in one batchUpdate. IMPORTANT: we must process matches in reverse order (highest index first) so earlier index deletions don't invalidate later indices.

**Step 1: Add failing tests**

Append to `tests/test_replace_text.py`:

```python
@pytest.mark.asyncio
async def test_replace_text_regex_mode(mock_services):
    drive = mock_services["drive"]
    docs = mock_services["docs"]
    drive.files().get.return_value.execute.return_value = {
        "name": "doc", "mimeType": "application/vnd.google-apps.document",
        "modifiedTime": "2026-04-10T12:00:00Z",
    }
    # documents.get returns body with textRuns containing plain text
    docs.documents().get.return_value.execute.return_value = {
        "body": {
            "content": [
                {
                    "startIndex": 1, "endIndex": 20,
                    "paragraph": {
                        "elements": [
                            {
                                "startIndex": 1, "endIndex": 20,
                                "textRun": {"content": "version v1.2 text\n"},
                            }
                        ]
                    },
                }
            ]
        }
    }
    docs.documents().batchUpdate.return_value.execute.return_value = {"replies": []}

    from gdrive_mcp.server import replace_text
    result = await replace_text(
        file_id="d1", find=r"v\d+\.\d+", replace="vNEW", regex=True
    )
    assert result["regex_mode"] is True
    assert result["replacements_made"] == 1

    req = docs.documents().batchUpdate.call_args.kwargs["body"]["requests"]
    # Should contain a delete + insert pair
    kinds = [list(r.keys())[0] for r in req]
    assert "deleteContentRange" in kinds
    assert "insertText" in kinds


@pytest.mark.asyncio
async def test_replace_text_invalid_regex_returns_error(mock_services):
    drive = mock_services["drive"]
    drive.files().get.return_value.execute.return_value = {
        "name": "doc", "mimeType": "application/vnd.google-apps.document",
        "modifiedTime": "2026-04-10T12:00:00Z",
    }

    from gdrive_mcp.server import replace_text
    result = await replace_text(
        file_id="d1", find="[unclosed", replace="y", regex=True
    )
    assert result["error"] == "INVALID_REGEX"
    assert result["retryable"] is False
```

**Step 2: Run failing tests**

Run: `uv run pytest tests/test_replace_text.py::test_replace_text_regex_mode tests/test_replace_text.py::test_replace_text_invalid_regex_returns_error -v`
Expected: FAIL with `NotImplementedError: regex path added in Task 13`.

**Step 3: Implement regex replace in `docs_ops.py`**

Append:

```python
import re


async def replace_regex(
    docs_service, file_id: str, pattern: str, replacement: str, match_case: bool
) -> int:
    """Regex replace client-side via batched delete+insert requests."""
    flags = 0 if match_case else re.IGNORECASE
    regex = re.compile(pattern, flags)

    doc = await asyncio.to_thread(
        lambda: docs_service.documents()
        .get(documentId=file_id)
        .execute()
    )

    # Build (absolute_index, text) segments from all textRuns
    segments: list[tuple[int, str]] = []
    for block in doc.get("body", {}).get("content", []):
        para = block.get("paragraph")
        if not para:
            continue
        for elem in para.get("elements", []):
            tr = elem.get("textRun")
            if not tr:
                continue
            segments.append((elem["startIndex"], tr.get("content", "")))

    # Flatten into one big string with an index map
    flat_parts: list[str] = []
    index_map: list[int] = []  # index_map[i] = absolute doc index of char i
    for start_idx, text in segments:
        for offset, _ch in enumerate(text):
            flat_parts.append(_ch)
            index_map.append(start_idx + offset)
    flat = "".join(flat_parts)

    matches = list(regex.finditer(flat))
    if not matches:
        return 0

    # Build requests in REVERSE order so earlier-index edits don't shift later ones
    requests: list[dict] = []
    for m in reversed(matches):
        abs_start = index_map[m.start()]
        abs_end = index_map[m.end() - 1] + 1
        requests.append({
            "deleteContentRange": {
                "range": {"startIndex": abs_start, "endIndex": abs_end}
            }
        })
        requests.append({
            "insertText": {
                "location": {"index": abs_start},
                "text": m.expand(replacement),
            }
        })

    await asyncio.to_thread(
        lambda: docs_service.documents()
        .batchUpdate(documentId=file_id, body={"requests": requests})
        .execute()
    )
    return len(matches)
```

**Step 4: Wire regex branch in `server.py`**

Replace the `NotImplementedError` in `replace_text` with:

```python
if regex:
    try:
        count = await docs_ops.replace_regex(
            docs, file_id, find, replace, match_case
        )
    except re.error as e:
        return {
            "error": "INVALID_REGEX",
            "retryable": False,
            "message": f"Invalid regex pattern: {e}",
        }
    meta2 = await asyncio.to_thread(
        lambda: drive.files()
        .get(fileId=file_id, fields="modifiedTime")
        .execute()
    )
    return {
        "file_id": file_id,
        "replacements_made": count,
        "regex_mode": True,
        "modified_time": meta2.get("modifiedTime", ""),
    }
```

Also add `import re` at the top of `server.py`.

**Step 5: Run tests**

Run: `uv run pytest tests/test_replace_text.py -v`
Expected: 6 tests PASS.

**Step 6: Commit**

```bash
git add src/gdrive_mcp/docs_ops.py src/gdrive_mcp/server.py tests/test_replace_text.py
git commit -m "feat: replace_text regex mode via client-side batch requests"
```

---

### Task 14: `manage_comments` — CRUD on comments and replies

**Files:**
- Modify: `src/gdrive_mcp/drive_ops.py`
- Modify: `src/gdrive_mcp/server.py`
- Create: `tests/test_manage_comments.py`

**Step 1: Write failing tests**

Create `tests/test_manage_comments.py`:

```python
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def mock_drive():
    with patch("gdrive_mcp.auth.get_drive_service") as mock:
        service = MagicMock()
        mock.return_value = service
        yield service


@pytest.mark.asyncio
async def test_manage_comments_list(mock_drive):
    mock_drive.comments().list.return_value.execute.return_value = {
        "comments": [
            {
                "id": "c1", "content": "first",
                "createdTime": "2026-04-01T10:00:00Z",
                "author": {"displayName": "Josh"},
                "resolved": False, "anchor": None,
                "replies": [
                    {"id": "r1", "content": "reply",
                     "createdTime": "2026-04-01T11:00:00Z",
                     "author": {"displayName": "Claude"}}
                ],
            }
        ]
    }

    from gdrive_mcp.server import manage_comments
    result = await manage_comments(file_id="f1", action="list")

    assert len(result["comments"]) == 1
    assert result["comments"][0]["comment_id"] == "c1"
    assert result["comments"][0]["replies"][0]["reply_id"] == "r1"


@pytest.mark.asyncio
async def test_manage_comments_create_unanchored(mock_drive):
    mock_drive.comments().create.return_value.execute.return_value = {
        "id": "new1", "content": "hi",
        "createdTime": "2026-04-10T12:00:00Z",
        "author": {"displayName": "Claude"},
    }
    from gdrive_mcp.server import manage_comments
    result = await manage_comments(file_id="f1", action="create", content="hi")
    assert result["comment_id"] == "new1"
    mock_drive.comments().create.assert_called()


@pytest.mark.asyncio
async def test_manage_comments_reply(mock_drive):
    mock_drive.replies().create.return_value.execute.return_value = {
        "id": "r9", "content": "ack",
        "createdTime": "2026-04-10T12:00:00Z",
        "author": {"displayName": "Claude"},
    }
    from gdrive_mcp.server import manage_comments
    result = await manage_comments(
        file_id="f1", action="reply", comment_id="c1", content="ack"
    )
    assert result["reply_id"] == "r9"


@pytest.mark.asyncio
async def test_manage_comments_resolve(mock_drive):
    mock_drive.comments().update.return_value.execute.return_value = {
        "id": "c1", "content": "orig", "resolved": True,
    }
    from gdrive_mcp.server import manage_comments
    result = await manage_comments(
        file_id="f1", action="resolve", comment_id="c1"
    )
    assert result["resolved"] is True


@pytest.mark.asyncio
async def test_manage_comments_missing_required_param(mock_drive):
    from gdrive_mcp.server import manage_comments
    # reply without comment_id
    result = await manage_comments(file_id="f1", action="reply", content="hi")
    assert result["error"] == "MISSING_PARAM"
    # create without content
    result = await manage_comments(file_id="f1", action="create")
    assert result["error"] == "MISSING_PARAM"


@pytest.mark.asyncio
async def test_manage_comments_invalid_action(mock_drive):
    from gdrive_mcp.server import manage_comments
    result = await manage_comments(file_id="f1", action="nonsense")
    assert result["error"] == "INVALID_ACTION"
```

**Step 2: Run to verify failure**

Run: `uv run pytest tests/test_manage_comments.py -v`
Expected: FAIL with ImportError.

**Step 3: Add comment ops to `drive_ops.py`**

Append:

```python
async def list_comments(
    service, file_id: str, include_resolved: bool
) -> dict[str, Any]:
    resp = await asyncio.to_thread(
        lambda: service.comments()
        .list(
            fileId=file_id,
            includeDeleted=False,
            fields=(
                "comments(id,content,createdTime,author,resolved,anchor,"
                "replies(id,content,createdTime,author))"
            ),
        )
        .execute()
    )
    comments = resp.get("comments", [])
    if not include_resolved:
        comments = [c for c in comments if not c.get("resolved", False)]
    return {
        "comments": [
            {
                "comment_id": c["id"],
                "content": c.get("content", ""),
                "created_time": c.get("createdTime", ""),
                "author": c.get("author", {}).get("displayName", ""),
                "resolved": c.get("resolved", False),
                "anchor": c.get("anchor"),
                "replies": [
                    {
                        "reply_id": r["id"],
                        "content": r.get("content", ""),
                        "created_time": r.get("createdTime", ""),
                        "author": r.get("author", {}).get("displayName", ""),
                    }
                    for r in c.get("replies", [])
                ],
            }
            for c in comments
        ]
    }


async def create_comment(
    service, file_id: str, content: str, anchor_text: Optional[str] = None
) -> dict[str, Any]:
    body: dict[str, Any] = {"content": content}
    # anchor_text currently best-effort: Drive's anchor format is complex;
    # we store it in the comment content if anchor_text is provided but
    # not a full structured anchor. Future v2 could implement structured anchors.
    if anchor_text:
        body["content"] = f"[re: '{anchor_text}'] {content}"
    resp = await asyncio.to_thread(
        lambda: service.comments()
        .create(
            fileId=file_id,
            body=body,
            fields="id,content,createdTime,author",
        )
        .execute()
    )
    return {
        "comment_id": resp["id"],
        "content": resp.get("content", ""),
        "created_time": resp.get("createdTime", ""),
        "author": resp.get("author", {}).get("displayName", ""),
    }


async def reply_to_comment(
    service, file_id: str, comment_id: str, content: str
) -> dict[str, Any]:
    resp = await asyncio.to_thread(
        lambda: service.replies()
        .create(
            fileId=file_id,
            commentId=comment_id,
            body={"content": content},
            fields="id,content,createdTime,author",
        )
        .execute()
    )
    return {
        "reply_id": resp["id"],
        "content": resp.get("content", ""),
        "created_time": resp.get("createdTime", ""),
        "author": resp.get("author", {}).get("displayName", ""),
    }


async def resolve_comment(
    service, file_id: str, comment_id: str
) -> dict[str, Any]:
    resp = await asyncio.to_thread(
        lambda: service.comments()
        .update(
            fileId=file_id,
            commentId=comment_id,
            body={"resolved": True},
            fields="id,content,resolved",
        )
        .execute()
    )
    return {
        "comment_id": resp["id"],
        "content": resp.get("content", ""),
        "resolved": resp.get("resolved", False),
    }
```

**Step 4: Add `manage_comments` tool to `server.py`**

```python
@mcp.tool()
async def manage_comments(
    file_id: str,
    action: str,
    comment_id: Optional[str] = None,
    content: Optional[str] = None,
    anchor_text: Optional[str] = None,
    include_resolved: bool = False,
) -> dict[str, Any]:
    """Manage comments on a Drive file. Actions: list, create, reply, resolve.

    Parameter requirements per action:
    - list: no extra params (include_resolved optional)
    - create: content required (anchor_text optional)
    - reply: comment_id and content required
    - resolve: comment_id required
    """
    drive = get_drive_service()

    if action == "list":
        return await drive_ops.list_comments(drive, file_id, include_resolved)

    if action == "create":
        if not content:
            return {
                "error": "MISSING_PARAM", "retryable": False,
                "message": "action='create' requires 'content'",
            }
        return await drive_ops.create_comment(drive, file_id, content, anchor_text)

    if action == "reply":
        if not comment_id or not content:
            return {
                "error": "MISSING_PARAM", "retryable": False,
                "message": "action='reply' requires 'comment_id' and 'content'",
            }
        return await drive_ops.reply_to_comment(drive, file_id, comment_id, content)

    if action == "resolve":
        if not comment_id:
            return {
                "error": "MISSING_PARAM", "retryable": False,
                "message": "action='resolve' requires 'comment_id'",
            }
        return await drive_ops.resolve_comment(drive, file_id, comment_id)

    return {
        "error": "INVALID_ACTION", "retryable": False,
        "message": f"Unknown action '{action}'. Valid: list, create, reply, resolve.",
    }
```

**Step 5: Run tests**

Run: `uv run pytest tests/test_manage_comments.py -v`
Expected: 6 tests PASS.

**Step 6: Commit**

```bash
git add src/gdrive_mcp/drive_ops.py src/gdrive_mcp/server.py tests/test_manage_comments.py
git commit -m "feat: manage_comments (list/create/reply/resolve CRUD)"
```

---

### Task 15: Create `docx_edits.py` — OOXML tracked-changes (single-run matches)

**Files:**
- Create: `src/gdrive_mcp/docx_edits.py`
- Create: `tests/fixtures/__init__.py` (empty)
- Create: `tests/test_docx_edits.py`
- Create: `tests/conftest.py` fixture or `tests/fixtures/sample_docx_bytes.py` helper

**Background on OOXML tracked changes:** A `.docx` is a ZIP file. `word/document.xml` contains the body. The relevant namespace is `w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"`. Paragraphs are `<w:p>`, runs are `<w:r>` with text inside `<w:t>`. Tracked changes are represented by wrapping:
- Deletions: `<w:del w:id="N" w:author="..." w:date="..."><w:r>...<w:delText>...</w:delText></w:r></w:del>`
- Insertions: `<w:ins w:id="N" w:author="..." w:date="..."><w:r>...<w:t>new text</w:t></w:r></w:ins>`

IDs must be unique integers. The inserted run should inherit `<w:rPr>` (run properties) from the run where the match starts so formatting is preserved.

This task handles the **single-run case only**: the entire `find_text` exists within one `<w:t>` element. Task 16 extends to multi-run.

**Step 1: Build the sample .docx fixture helper**

Create `tests/fixtures/__init__.py` (empty file).

Create `tests/fixtures/sample_docx.py`:

```python
"""Helpers to generate minimal .docx byte fixtures for tests."""

import io
import zipfile


def make_docx(runs: list[tuple[str, dict | None]]) -> bytes:
    """Build a minimal .docx with one paragraph containing the given runs.

    Each run is (text, run_properties_dict_or_none). run_properties_dict
    becomes XML attribs inside <w:rPr><w:rFonts w:ascii="..."/></w:rPr> —
    kept simple for tests.
    """
    W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    run_xml_parts: list[str] = []
    for text, props in runs:
        rpr = ""
        if props and "bold" in props:
            rpr = "<w:rPr><w:b/></w:rPr>"
        run_xml_parts.append(
            f'<w:r>{rpr}<w:t xml:space="preserve">{text}</w:t></w:r>'
        )
    runs_xml = "".join(run_xml_parts)
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}">'
        f'<w:body><w:p>{runs_xml}</w:p></w:body>'
        '</w:document>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        '</Relationships>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document_xml)
    return buf.getvalue()
```

**Step 2: Write failing tests for single-run case**

Create `tests/test_docx_edits.py`:

```python
import io
import zipfile

import pytest

from tests.fixtures.sample_docx import make_docx


def _extract_document_xml(docx_bytes: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as z:
        return z.read("word/document.xml").decode("utf-8")


def test_insert_tracked_change_single_run_match():
    from gdrive_mcp.docx_edits import insert_tracked_change

    original = make_docx([("The quick brown fox", None)])
    modified = insert_tracked_change(
        original, find_text="quick", replace_text="slow", author="Claude"
    )

    xml = _extract_document_xml(modified)
    assert "<w:del " in xml
    assert "<w:delText" in xml
    assert "quick" in xml  # inside delText
    assert "<w:ins " in xml
    assert "slow" in xml
    assert 'w:author="Claude"' in xml


def test_insert_tracked_change_preserves_surrounding_text():
    from gdrive_mcp.docx_edits import insert_tracked_change

    original = make_docx([("Hello beautiful world", None)])
    modified = insert_tracked_change(
        original, "beautiful", "cruel", "Claude"
    )
    xml = _extract_document_xml(modified)
    # "Hello " and " world" must still be present in plain runs
    assert ">Hello </w:t>" in xml or "Hello" in xml
    assert "world" in xml


def test_insert_tracked_change_not_found_raises():
    from gdrive_mcp.docx_edits import insert_tracked_change, NotFoundError

    original = make_docx([("Hello world", None)])
    with pytest.raises(NotFoundError):
        insert_tracked_change(original, "xyz", "abc", "Claude")
```

**Step 3: Run to verify failure**

Run: `uv run pytest tests/test_docx_edits.py -v`
Expected: FAIL with `ModuleNotFoundError: gdrive_mcp.docx_edits`.

**Step 4: Implement `insert_tracked_change` (single-run only)**

Create `src/gdrive_mcp/docx_edits.py`:

```python
"""OOXML tracked-change manipulation for .docx files.

Pure functions: input bytes → output bytes. No I/O. No Drive API.
"""

import copy
import datetime as _dt
import io
import zipfile
from typing import Optional
from xml.etree import ElementTree as ET

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"


class NotFoundError(ValueError):
    """find_text was not located in the document."""


class CrossParagraphError(ValueError):
    """find_text spans a paragraph boundary (not supported in v1)."""


def _register_namespace() -> None:
    ET.register_namespace("w", W_NS)


def _next_id(counter: list[int]) -> int:
    counter[0] += 1
    return counter[0]


def _make_del(author: str, date: str, rev_id: int, deleted_text: str,
              rpr: Optional[ET.Element]) -> ET.Element:
    del_el = ET.Element(f"{W}del", {
        f"{W}id": str(rev_id),
        f"{W}author": author,
        f"{W}date": date,
    })
    r = ET.SubElement(del_el, f"{W}r")
    if rpr is not None:
        r.append(copy.deepcopy(rpr))
    dt = ET.SubElement(r, f"{W}delText", {"xml:space": "preserve"})
    dt.text = deleted_text
    return del_el


def _make_ins(author: str, date: str, rev_id: int, inserted_text: str,
              rpr: Optional[ET.Element]) -> ET.Element:
    ins_el = ET.Element(f"{W}ins", {
        f"{W}id": str(rev_id),
        f"{W}author": author,
        f"{W}date": date,
    })
    r = ET.SubElement(ins_el, f"{W}r")
    if rpr is not None:
        r.append(copy.deepcopy(rpr))
    t = ET.SubElement(r, f"{W}t", {"xml:space": "preserve"})
    t.text = inserted_text
    return ins_el


def insert_tracked_change(
    docx_bytes: bytes,
    find_text: str,
    replace_text: str,
    author: str,
) -> bytes:
    """Insert tracked-change revision marks for find_text → replace_text.

    Single-run case: entire find_text exists within one <w:t>. Extended in
    Task 16 to handle multi-run matches within a paragraph.
    Raises NotFoundError if find_text is not located.
    Raises CrossParagraphError if the match spans paragraph boundaries.
    """
    _register_namespace()

    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as z:
        document_xml = z.read("word/document.xml")
        all_names = z.namelist()
        other_files = {
            name: z.read(name) for name in all_names if name != "word/document.xml"
        }

    root = ET.fromstring(document_xml)
    rev_counter = [100]  # arbitrary starting ID
    date = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    found = False
    # Iterate paragraphs
    for para in root.iter(f"{W}p"):
        runs = list(para.findall(f"{W}r"))
        # Single-run scan: does any single run's <w:t> text contain find_text?
        for run in runs:
            t_elems = run.findall(f"{W}t")
            if not t_elems:
                continue
            t = t_elems[0]
            if t.text and find_text in t.text:
                # Found in this single run. Split the text.
                before, after = t.text.split(find_text, 1)
                rpr = run.find(f"{W}rPr")
                # Mutate the current run to contain only "before"
                t.text = before
                # Build del and ins elements
                del_el = _make_del(author, date, _next_id(rev_counter), find_text, rpr)
                ins_el = _make_ins(author, date, _next_id(rev_counter), replace_text, rpr)
                # Build a trailing run with "after"
                trailing_run = None
                if after:
                    trailing_run = ET.Element(f"{W}r")
                    if rpr is not None:
                        trailing_run.append(copy.deepcopy(rpr))
                    trailing_t = ET.SubElement(
                        trailing_run, f"{W}t", {"xml:space": "preserve"}
                    )
                    trailing_t.text = after
                # Insert del, ins, (trailing_run?) immediately after `run` in para
                para_children = list(para)
                insert_at = para_children.index(run) + 1
                para.insert(insert_at, del_el)
                para.insert(insert_at + 1, ins_el)
                if trailing_run is not None:
                    para.insert(insert_at + 2, trailing_run)
                found = True
                break
        if found:
            break

    if not found:
        raise NotFoundError(
            f"find_text not located in a single run (multi-run lookup in Task 16): {find_text!r}"
        )

    # Re-serialize and rebuild the zip
    new_document_xml = ET.tostring(root, xml_declaration=True, encoding="UTF-8")

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", new_document_xml)
        for name, data in other_files.items():
            z.writestr(name, data)
    return out.getvalue()
```

**Step 5: Run tests**

Run: `uv run pytest tests/test_docx_edits.py -v`
Expected: 3 tests PASS.

**Step 6: Commit**

```bash
git add src/gdrive_mcp/docx_edits.py tests/fixtures/ tests/test_docx_edits.py
git commit -m "feat: docx_edits.insert_tracked_change (single-run matches)"
```

---

### Task 16: Extend `docx_edits` to support multi-run matches

**Files:**
- Modify: `src/gdrive_mcp/docx_edits.py`
- Modify: `tests/test_docx_edits.py`

**Background:** In real .docx files, formatting changes cause runs to split. `"The bold word"` where "bold" is bold becomes three runs: `"The "`, `"bold"` (bold), `" word"`. A find of `"bold word"` spans two runs; a find of `"The bold word"` spans three.

**Algorithm:**
1. For each paragraph, build a flat string by concatenating all `<w:t>` text in order, tracking `(run_index, offset_within_run)` for each character.
2. Run `str.find(find_text)` on the flat string.
3. If found, locate the starting run (run at the match's start offset) and the ending run (run at the match's end offset).
4. Split the starting run: mutate its `<w:t>` to contain only the text before the match; capture its `<w:rPr>` for the inserted run's formatting.
5. If the starting and ending runs are the same: after splitting, build a trailing run with the text after the match (delegates to the single-run code path).
6. If different: delete all intermediate full runs from the paragraph; mutate the ending run's `<w:t>` to contain only the text after the match within that run; wrap all the deleted content (starting-run-tail + intermediates + ending-run-head) inside a single `<w:del>` and insert a `<w:ins>` after it.

**Step 1: Add failing tests**

Append to `tests/test_docx_edits.py`:

```python
def test_insert_tracked_change_spans_two_runs():
    from gdrive_mcp.docx_edits import insert_tracked_change

    # Three runs: "The ", "bold" (bold), " word"
    original = make_docx([
        ("The ", None),
        ("bold", {"bold": True}),
        (" word", None),
    ])
    modified = insert_tracked_change(
        original, find_text="bold word", replace_text="brave word", author="Claude"
    )
    xml = _extract_document_xml(modified)
    assert "<w:del " in xml
    assert "<w:ins " in xml
    assert "brave word" in xml
    # "The " must still be present as ordinary text (not inside del)
    assert "The " in xml


def test_insert_tracked_change_spans_three_runs():
    from gdrive_mcp.docx_edits import insert_tracked_change

    original = make_docx([
        ("The ", None),
        ("bold", {"bold": True}),
        (" word here", None),
    ])
    modified = insert_tracked_change(
        original, find_text="The bold word", replace_text="A brave word", author="Claude"
    )
    xml = _extract_document_xml(modified)
    assert "<w:del " in xml
    assert "A brave word" in xml
    assert " here" in xml  # trailing text preserved


def test_insert_tracked_change_match_at_run_boundary():
    from gdrive_mcp.docx_edits import insert_tracked_change

    original = make_docx([
        ("Hello", None),
        (" world", None),
    ])
    modified = insert_tracked_change(
        original, "Hello world", "Goodbye world", "Claude"
    )
    xml = _extract_document_xml(modified)
    assert "Goodbye world" in xml
    assert "<w:del " in xml
```

**Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_docx_edits.py -v`
Expected: The three new tests FAIL (old single-run code can't span runs).

**Step 3: Rewrite `insert_tracked_change` with multi-run support**

Replace the `insert_tracked_change` function in `src/gdrive_mcp/docx_edits.py` with:

```python
def insert_tracked_change(
    docx_bytes: bytes,
    find_text: str,
    replace_text: str,
    author: str,
) -> bytes:
    """Insert tracked-change revision marks for find_text → replace_text.

    Handles matches within a single paragraph, spanning any number of runs.
    Cross-paragraph matches raise CrossParagraphError.
    """
    _register_namespace()

    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as z:
        document_xml = z.read("word/document.xml")
        all_names = z.namelist()
        other_files = {
            name: z.read(name) for name in all_names if name != "word/document.xml"
        }

    root = ET.fromstring(document_xml)
    rev_counter = [100]
    date = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    # Also scan whether find_text spans paragraph boundaries by building a
    # whole-body flat string. If a match exists across paragraphs but not
    # within any single paragraph, raise CrossParagraphError.
    def paragraph_flat(p: ET.Element) -> tuple[str, list[tuple[ET.Element, int, int]]]:
        """Return (concatenated_text, list of (run_element, start_in_flat, end_in_flat))."""
        flat = []
        runs: list[tuple[ET.Element, int, int]] = []
        cursor = 0
        for run in p.findall(f"{W}r"):
            t = run.find(f"{W}t")
            if t is None:
                continue
            text = t.text or ""
            runs.append((run, cursor, cursor + len(text)))
            flat.append(text)
            cursor += len(text)
        return "".join(flat), runs

    found = False
    for para in list(root.iter(f"{W}p")):
        flat, runs = paragraph_flat(para)
        idx = flat.find(find_text)
        if idx < 0:
            continue

        # Found within this paragraph. Locate start and end runs.
        match_end = idx + len(find_text)
        start_run_entry = None
        end_run_entry = None
        for entry in runs:
            run_el, r_start, r_end = entry
            if r_start <= idx < r_end and start_run_entry is None:
                start_run_entry = entry
            if r_start < match_end <= r_end:
                end_run_entry = entry
        if start_run_entry is None or end_run_entry is None:
            # Should not happen, but defensive
            raise NotFoundError(f"internal: could not locate runs for {find_text!r}")

        start_run, start_r_start, _ = start_run_entry
        end_run, end_r_start, end_r_end = end_run_entry
        start_offset = idx - start_r_start
        end_offset = match_end - end_r_start

        start_t = start_run.find(f"{W}t")
        end_t = end_run.find(f"{W}t")
        start_text = start_t.text or ""
        end_text = end_t.text or ""

        # Capture rPr from the starting run for formatting inheritance
        start_rpr = start_run.find(f"{W}rPr")

        # Build deleted text = tail of start_run + all intermediate run text + head of end_run
        if start_run is end_run:
            deleted_text = start_text[start_offset:end_offset]
            head = start_text[:start_offset]
            tail = end_text[end_offset:]
        else:
            head = start_text[:start_offset]
            tail = end_text[end_offset:]
            deleted_parts = [start_text[start_offset:]]
            start_idx_in_runs = runs.index(start_run_entry)
            end_idx_in_runs = runs.index(end_run_entry)
            for entry in runs[start_idx_in_runs + 1:end_idx_in_runs]:
                mid_run = entry[0]
                mid_t = mid_run.find(f"{W}t")
                if mid_t is not None and mid_t.text:
                    deleted_parts.append(mid_t.text)
            deleted_parts.append(end_text[:end_offset])
            deleted_text = "".join(deleted_parts)

        # Mutate start_run: keep only "head" (may be empty string)
        start_t.text = head
        # Mark the start run's <w:t> to preserve whitespace
        start_t.set("xml:space", "preserve")

        # Remove intermediate runs (between start and end, exclusive) from the paragraph
        if start_run is not end_run:
            start_idx_in_runs = runs.index(start_run_entry)
            end_idx_in_runs = runs.index(end_run_entry)
            for entry in runs[start_idx_in_runs + 1:end_idx_in_runs]:
                para.remove(entry[0])
            # Mutate end_run: keep only "tail" (may be empty)
            end_t.text = tail
            end_t.set("xml:space", "preserve")

        # Build del and ins
        del_el = _make_del(author, date, _next_id(rev_counter), deleted_text, start_rpr)
        ins_el = _make_ins(author, date, _next_id(rev_counter), replace_text, start_rpr)

        # Insert del + ins right after start_run
        para_children = list(para)
        insert_at = para_children.index(start_run) + 1
        para.insert(insert_at, del_el)
        para.insert(insert_at + 1, ins_el)

        # If single-run case and there's a non-empty tail, we also need to split
        # the start_run into a trailing run (because we already mutated start_t
        # to contain only `head`, the tail is lost without a new trailing run).
        if start_run is end_run and tail:
            trailing_run = ET.Element(f"{W}r")
            if start_rpr is not None:
                trailing_run.append(copy.deepcopy(start_rpr))
            trailing_t = ET.SubElement(
                trailing_run, f"{W}t", {"xml:space": "preserve"}
            )
            trailing_t.text = tail
            para.insert(insert_at + 2, trailing_run)

        # If start_run is now empty (head == "") and it's a different run from end_run,
        # it's acceptable to leave an empty run; Word handles it. Remove for cleanliness.
        if not head and start_run is not end_run:
            para.remove(start_run)

        # If end_run is now empty (tail == "") and different from start_run, remove it too.
        if start_run is not end_run and not tail:
            if end_run in list(para):
                para.remove(end_run)

        found = True
        break

    if not found:
        # Check if it spans paragraphs (for better error message)
        whole = "".join(
            (t.text or "")
            for t in root.iter(f"{W}t")
        )
        if find_text in whole:
            raise CrossParagraphError(
                f"find_text spans a paragraph boundary (not supported): {find_text!r}"
            )
        raise NotFoundError(f"find_text not located: {find_text!r}")

    new_document_xml = ET.tostring(root, xml_declaration=True, encoding="UTF-8")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", new_document_xml)
        for name, data in other_files.items():
            z.writestr(name, data)
    return out.getvalue()
```

**Step 4: Run all docx_edits tests**

Run: `uv run pytest tests/test_docx_edits.py -v`
Expected: all tests PASS (single-run + multi-run + boundary cases).

**Step 5: Commit**

```bash
git add src/gdrive_mcp/docx_edits.py tests/test_docx_edits.py
git commit -m "feat: docx_edits multi-run + cross-paragraph detection"
```

---

### Task 17: `docx_suggest_edit` MCP tool — wire docx_edits to Drive upload

**Files:**
- Modify: `src/gdrive_mcp/server.py`
- Create: `tests/test_docx_suggest_edit.py`

**Step 1: Write failing tests**

Create `tests/test_docx_suggest_edit.py`:

```python
import base64
from unittest.mock import patch, MagicMock

import pytest

from tests.fixtures.sample_docx import make_docx


@pytest.fixture
def mock_drive():
    with patch("gdrive_mcp.auth.get_drive_service") as mock:
        service = MagicMock()
        mock.return_value = service
        yield service


@pytest.mark.asyncio
async def test_docx_suggest_edit_roundtrips(mock_drive):
    original = make_docx([("The quick brown fox", None)])

    mock_drive.files().get.return_value.execute.return_value = {
        "name": "doc.docx",
        "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "size": str(len(original)),
    }
    mock_drive.files().get_media.return_value.execute.return_value = original
    mock_drive.files().update.return_value.execute.return_value = {
        "id": "d1", "name": "doc.docx",
        "webViewLink": "https://example.com",
        "version": "2",
        "modifiedTime": "2026-04-10T12:00:00Z",
    }

    from gdrive_mcp.server import docx_suggest_edit
    result = await docx_suggest_edit(
        file_id="d1", find_text="quick", replace_text="slow", author="Claude"
    )
    assert result["file_id"] == "d1"
    assert result["occurrences_edited"] == 1
    mock_drive.files().update.assert_called_once()


@pytest.mark.asyncio
async def test_docx_suggest_edit_errors_on_google_doc(mock_drive):
    mock_drive.files().get.return_value.execute.return_value = {
        "name": "native",
        "mimeType": "application/vnd.google-apps.document",
    }
    from gdrive_mcp.server import docx_suggest_edit
    result = await docx_suggest_edit(
        file_id="x", find_text="a", replace_text="b"
    )
    assert result["error"] == "NOT_A_DOCX"
    assert "replace_text" in result["message"]


@pytest.mark.asyncio
async def test_docx_suggest_edit_find_text_not_found(mock_drive):
    original = make_docx([("Hello world", None)])
    mock_drive.files().get.return_value.execute.return_value = {
        "name": "doc.docx",
        "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "size": str(len(original)),
    }
    mock_drive.files().get_media.return_value.execute.return_value = original

    from gdrive_mcp.server import docx_suggest_edit
    result = await docx_suggest_edit(
        file_id="d1", find_text="xyz", replace_text="abc"
    )
    assert result["error"] == "FIND_TEXT_NOT_FOUND"
```

**Step 2: Run to verify failure**

Run: `uv run pytest tests/test_docx_suggest_edit.py -v`
Expected: FAIL — `docx_suggest_edit` not in server.

**Step 3: Add `docx_suggest_edit` tool to `server.py`**

Add import:
```python
from gdrive_mcp import docx_edits
```

Add tool:

```python
DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


@mcp.tool()
async def docx_suggest_edit(
    file_id: str,
    find_text: str,
    replace_text: str,
    author: str = "Claude",
) -> dict[str, Any]:
    """Insert tracked-change revision marks into a .docx file.

    Only works on real .docx files in Drive (mimeType
    application/vnd.openxmlformats-officedocument.wordprocessingml.document).
    For Google Docs, use replace_text. Matches must fit within a single
    paragraph (cross-paragraph is v2).
    """
    drive = get_drive_service()
    meta = await asyncio.to_thread(
        lambda: drive.files()
        .get(fileId=file_id, fields="name,mimeType,size")
        .execute()
    )
    if meta.get("mimeType") != DOCX_MIME:
        return {
            "error": "NOT_A_DOCX",
            "retryable": False,
            "message": (
                f"docx_suggest_edit only works on .docx files. This file is "
                f"{meta.get('mimeType')}. Use replace_text for Google Docs."
            ),
        }

    original = await asyncio.to_thread(
        lambda: drive.files().get_media(fileId=file_id).execute()
    )
    try:
        modified = docx_edits.insert_tracked_change(
            original, find_text, replace_text, author
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
    upload_result = await drive_ops.upload_file(
        drive,
        content_base64=_b64.b64encode(modified).decode(),
        file_name=meta["name"],
        mime_type=DOCX_MIME,
        file_id=file_id,
    )
    return {
        "file_id": file_id,
        "file_name": meta["name"],
        "occurrences_edited": 1,  # v1 does one edit per call
        "modified_time": upload_result.get("modified_time", ""),
    }
```

**Step 4: Run all tests**

Run: `uv run pytest -q`
Expected: everything passes. (Full suite run to catch any regressions.)

**Step 5: Commit**

```bash
git add src/gdrive_mcp/server.py tests/test_docx_suggest_edit.py
git commit -m "feat: docx_suggest_edit MCP tool (tracked changes for .docx)"
```

---

### Task 18: Update CLAUDE.md and run final verification

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Update CLAUDE.md**

Replace the current `CLAUDE.md` content with:

```markdown
# CLAUDE.md

## Commands

```bash
# Install
uv sync --all-extras

# Test
uv run pytest -q

# Lint
uv run ruff check .

# Run locally
uv run python -m gdrive_mcp

# One-time OAuth setup (generates GOOGLE_OAUTH_REFRESH_TOKEN)
uv run python -m gdrive_mcp.auth_setup
```

## Project Structure

- `src/gdrive_mcp/auth.py` — OAuth user credential loader + service factories
- `src/gdrive_mcp/auth_setup.py` — one-time OAuth consent CLI
- `src/gdrive_mcp/drive_ops.py` — Drive v3 operations (download, upload, search, metadata, comments)
- `src/gdrive_mcp/docs_ops.py` — Docs v1 operations (append, replace_text)
- `src/gdrive_mcp/sheets_ops.py` — Sheets v4 operations (append rows)
- `src/gdrive_mcp/docx_edits.py` — OOXML tracked-changes (pure functions)
- `src/gdrive_mcp/server.py` — FastMCP server exposing 9 tools
- `tests/` — pytest suite mirroring the module split

## Tools

1. `download_file` — download or export a file
2. `upload_file` — create or update a file
3. `search_files` — Drive query syntax search
4. `get_file_metadata` — single-file metadata
5. `get_files_metadata` — batch metadata for N files
6. `append_to_file` — native append for Docs/Sheets; roundtrip fallback for plain files
7. `replace_text` — exact + regex replace in Google Docs
8. `manage_comments` — list/create/reply/resolve on Drive comments
9. `docx_suggest_edit` — tracked-change revision marks in .docx files

## Environment Variables

Required:
- `GOOGLE_OAUTH_CLIENT_ID` — OAuth 2.0 client ID from GCP console
- `GOOGLE_OAUTH_CLIENT_SECRET` — OAuth 2.0 client secret
- `GOOGLE_OAUTH_REFRESH_TOKEN` — long-lived refresh token (generate via `auth_setup`)

Optional:
- `PORT` — HTTP port for the FastMCP server (default 8080)

## Key Constraints

- No database, no state, no LLM calls
- Single-user OAuth only (service accounts removed)
- Streamable HTTP transport for Cloud Run
- `docx_suggest_edit` requires matches to fit within one paragraph (v1)
```

**Step 2: Run the full test suite + ruff**

Run: `uv run pytest -q && uv run ruff check .`
Expected:
- All tests pass
- ruff is clean

**Step 3: Verify all 9 tools are discoverable**

Run:
```bash
uv run python -c "
from gdrive_mcp.server import mcp
# FastMCP internals vary; this is a sanity import
print('mcp server loaded OK')
"
```
Expected: prints `mcp server loaded OK`.

**Step 4: Verify error path for missing env**

Run:
```bash
unset GOOGLE_OAUTH_CLIENT_ID GOOGLE_OAUTH_CLIENT_SECRET GOOGLE_OAUTH_REFRESH_TOKEN
uv run python -c "
from gdrive_mcp.auth import get_credentials, _reset_cache, AuthError
_reset_cache()
try:
    get_credentials()
    print('FAIL: should have raised')
except AuthError as e:
    print(f'OK: {e}')
"
```
Expected: prints `OK: Missing required OAuth env vars: ...`

**Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for OAuth + 9-tool expansion"
```

---

## Appendix: Verification checklist

Before declaring done:

- [ ] `uv run pytest -q` — all tests pass
- [ ] `uv run ruff check .` — clean
- [ ] All 9 MCP tools are registered in `server.py`
- [ ] `drive.py` and `test_drive.py` are deleted
- [ ] `storageQuotaExceeded` branch is gone from `upload_file`
- [ ] `CLAUDE.md` reflects OAuth env vars and new tool list
- [ ] Design doc at `docs/plans/2026-04-10-gdrive-mcp-expansion-design.md` matches the implementation
- [ ] Git history shows one commit per task (from auto-commit hook or manual commits)
- [ ] `auth_setup` CLI smoke-test: missing-env error path works (happy path untested since it opens a browser)

## Appendix: What's deliberately NOT in this plan

- Drive push notifications (`files.watch`) — out of scope (stateful, needs webhook infra)
- Cross-paragraph `docx_suggest_edit` — workaround is multiple per-paragraph calls
- Fuzzy / semantic matching in `replace_text` — exact + regex covers known needs
- Structured comment anchors in `create_comment` — current implementation prefixes content with `[re: 'anchor_text']`; full Drive anchor JSON is a v2 improvement
- Pagination in `list_comments` — default page size (20) is sufficient for single-user use
