# `replace_section` Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `replace_section` tool that replaces content in a Google Doc by heading/section instead of exact string match.

**Architecture:** Fetch full document structure, walk paragraphs to find heading by style then text fallback, compute section boundaries, execute atomic `batchUpdate` with `deleteContentRange` + `insertText` + `updateParagraphStyle`.

**Tech Stack:** Google Docs API v1, Python 3.12, pytest, unittest.mock

---

### Task 1: Core heading detection — `_find_heading`

**Files:**
- Modify: `src/gsuite_mcp/docs_ops.py` (add helper function)
- Create: `tests/test_replace_section.py`

**Step 1: Write the failing test for formal heading match**

In `tests/test_replace_section.py`:

```python
"""Tests for replace_section functionality."""

import pytest

from gsuite_mcp.docs_ops import _find_heading


def _make_doc(*paragraphs):
    """Build a minimal Google Docs body structure.

    Each paragraph is a tuple: (start, end, text, named_style).
    named_style is e.g. "HEADING_1", "HEADING_2", or "NORMAL_TEXT".
    """
    content = []
    for start, end, text, style in paragraphs:
        content.append({
            "startIndex": start,
            "endIndex": end,
            "paragraph": {
                "paragraphStyle": {"namedStyleType": style},
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


def test_find_heading_formal_match():
    doc = _make_doc(
        (1, 20, "Introduction\n", "HEADING_1"),
        (20, 80, "Some body text here.\n", "NORMAL_TEXT"),
        (80, 110, "Background\n", "HEADING_1"),
        (110, 160, "More body text.\n", "NORMAL_TEXT"),
    )
    result = _find_heading(doc, "Introduction")
    assert result["text"] == "Introduction\n"
    assert result["start_index"] == 1
    assert result["end_index"] == 20
    assert result["heading_level"] == "HEADING_1"
    assert result["level_rank"] == 1


def test_find_heading_case_insensitive():
    doc = _make_doc(
        (1, 20, "Introduction\n", "HEADING_1"),
    )
    result = _find_heading(doc, "introduction")
    assert result["text"] == "Introduction\n"


def test_find_heading_strips_whitespace():
    doc = _make_doc(
        (1, 25, "  Introduction  \n", "HEADING_1"),
    )
    result = _find_heading(doc, "Introduction")
    assert result is not None


def test_find_heading_text_fallback():
    """When no formal heading matches, fall back to text match on any paragraph."""
    doc = _make_doc(
        (1, 20, "Introduction\n", "NORMAL_TEXT"),  # not a formal heading
        (20, 80, "Body text.\n", "NORMAL_TEXT"),
    )
    result = _find_heading(doc, "Introduction")
    assert result is not None
    assert result["heading_level"] == "NORMAL_TEXT"
    assert result["level_rank"] == 7  # fallback level


def test_find_heading_not_found():
    doc = _make_doc(
        (1, 20, "Introduction\n", "HEADING_1"),
    )
    result = _find_heading(doc, "Nonexistent Section")
    assert result is None


def test_find_heading_ambiguous_returns_none_with_matches():
    """Multiple matches should return None but populate a list if we pass a collector."""
    doc = _make_doc(
        (1, 15, "Summary\n", "HEADING_2"),
        (15, 50, "Body.\n", "NORMAL_TEXT"),
        (50, 65, "Summary\n", "HEADING_2"),
        (65, 100, "More body.\n", "NORMAL_TEXT"),
    )
    matches = []
    result = _find_heading(doc, "Summary", matches_out=matches)
    assert result is None
    assert len(matches) == 2
    assert matches[0]["start_index"] == 1
    assert matches[1]["start_index"] == 50
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_replace_section.py -v`
Expected: FAIL — `_find_heading` does not exist yet

**Step 3: Implement `_find_heading`**

Add to `src/gsuite_mcp/docs_ops.py`:

```python
# Heading level ranks — lower number = higher level
_HEADING_RANKS: dict[str, int] = {
    "HEADING_1": 1, "HEADING_2": 2, "HEADING_3": 3,
    "HEADING_4": 4, "HEADING_5": 5, "HEADING_6": 6,
}
_FALLBACK_RANK = 7  # for text-matched non-heading paragraphs


def _para_text(paragraph: dict) -> str:
    """Extract plain text from a paragraph's elements."""
    return "".join(
        elem.get("textRun", {}).get("content", "")
        for elem in paragraph.get("elements", [])
    )


def _find_heading(
    doc: dict,
    section_heading: str,
    matches_out: list | None = None,
) -> dict | None:
    """Find a heading in a Google Doc by style then text fallback.

    Returns a dict with keys: text, start_index, end_index, heading_level,
    level_rank, paragraph_index. Returns None if not found or ambiguous
    (populates matches_out with all matches if ambiguous).
    """
    needle = section_heading.strip().lower()

    def _build_match(block: dict, style: str, rank: int, idx: int) -> dict:
        para = block["paragraph"]
        return {
            "text": _para_text(para),
            "start_index": block["startIndex"],
            "end_index": block["endIndex"],
            "heading_level": style,
            "level_rank": rank,
            "paragraph_index": idx,
        }

    # Pass 1: formal headings
    formal_matches = []
    for i, block in enumerate(doc.get("body", {}).get("content", [])):
        para = block.get("paragraph")
        if not para:
            continue
        style = para.get("paragraphStyle", {}).get("namedStyleType", "NORMAL_TEXT")
        rank = _HEADING_RANKS.get(style)
        if rank is None:
            continue
        text = _para_text(para).strip().lower()
        if text == needle:
            formal_matches.append(_build_match(block, style, rank, i))

    if len(formal_matches) == 1:
        return formal_matches[0]
    if len(formal_matches) > 1:
        if matches_out is not None:
            matches_out.extend(formal_matches)
        return None

    # Pass 2: text fallback on any paragraph
    text_matches = []
    for i, block in enumerate(doc.get("body", {}).get("content", [])):
        para = block.get("paragraph")
        if not para:
            continue
        text = _para_text(para).strip().lower()
        style = para.get("paragraphStyle", {}).get("namedStyleType", "NORMAL_TEXT")
        if text == needle:
            text_matches.append(_build_match(block, style, _FALLBACK_RANK, i))

    if len(text_matches) == 1:
        return text_matches[0]
    if len(text_matches) > 1:
        if matches_out is not None:
            matches_out.extend(text_matches)
        return None

    return None
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_replace_section.py -v`
Expected: All PASS

**Step 5: Commit**

Autocommit handles this.

---

### Task 2: Section boundary detection — `_find_section_end`

**Files:**
- Modify: `src/gsuite_mcp/docs_ops.py` (add helper function)
- Modify: `tests/test_replace_section.py` (add tests)

**Step 1: Write the failing tests**

Append to `tests/test_replace_section.py`:

```python
from gsuite_mcp.docs_ops import _find_section_end


def test_section_end_at_same_level_heading():
    doc = _make_doc(
        (1, 20, "Chapter 1\n", "HEADING_1"),
        (20, 80, "Body text.\n", "NORMAL_TEXT"),
        (80, 100, "Chapter 2\n", "HEADING_1"),
        (100, 150, "More text.\n", "NORMAL_TEXT"),
    )
    heading = {"paragraph_index": 0, "level_rank": 1}
    end = _find_section_end(doc, heading)
    assert end == 80  # start of Chapter 2


def test_section_end_at_higher_level_heading():
    doc = _make_doc(
        (1, 20, "Section 1.1\n", "HEADING_2"),
        (20, 80, "Body.\n", "NORMAL_TEXT"),
        (80, 100, "Chapter 2\n", "HEADING_1"),  # higher level stops section
    )
    heading = {"paragraph_index": 0, "level_rank": 2}
    end = _find_section_end(doc, heading)
    assert end == 80


def test_section_end_at_document_end():
    doc = _make_doc(
        (1, 20, "Last Section\n", "HEADING_1"),
        (20, 80, "Body text.\n", "NORMAL_TEXT"),
    )
    heading = {"paragraph_index": 0, "level_rank": 1}
    end = _find_section_end(doc, heading)
    assert end == 80  # end of document body


def test_section_end_skips_lower_level_headings():
    doc = _make_doc(
        (1, 20, "Chapter 1\n", "HEADING_1"),
        (20, 50, "Section 1.1\n", "HEADING_2"),  # lower level, part of section
        (50, 80, "Body.\n", "NORMAL_TEXT"),
        (80, 100, "Chapter 2\n", "HEADING_1"),
    )
    heading = {"paragraph_index": 0, "level_rank": 1}
    end = _find_section_end(doc, heading)
    assert end == 80  # skips HEADING_2, stops at HEADING_1


def test_section_end_fallback_heading_stops_at_any_formal():
    """A text-fallback heading (rank 7) ends at the next formal heading."""
    doc = _make_doc(
        (1, 20, "My Section\n", "NORMAL_TEXT"),  # fallback match
        (20, 80, "Body.\n", "NORMAL_TEXT"),
        (80, 100, "Next Heading\n", "HEADING_2"),
    )
    heading = {"paragraph_index": 0, "level_rank": 7}
    end = _find_section_end(doc, heading)
    assert end == 80
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_replace_section.py::test_section_end_at_same_level_heading -v`
Expected: FAIL — `_find_section_end` does not exist

**Step 3: Implement `_find_section_end`**

Add to `src/gsuite_mcp/docs_ops.py`:

```python
def _find_section_end(doc: dict, heading: dict) -> int:
    """Find the end index of a section (start of next same-or-higher-level heading).

    Returns the startIndex of the next heading at the same or higher level,
    or the endIndex of the last content block if the section extends to the end.
    """
    content = doc.get("body", {}).get("content", [])
    heading_idx = heading["paragraph_index"]
    level_rank = heading["level_rank"]

    last_end = 1
    for block in content:
        last_end = max(last_end, block.get("endIndex", 1))

    for block in content[heading_idx + 1:]:
        para = block.get("paragraph")
        if not para:
            continue
        style = para.get("paragraphStyle", {}).get("namedStyleType", "NORMAL_TEXT")
        rank = _HEADING_RANKS.get(style)
        if rank is not None and rank <= level_rank:
            return block["startIndex"]

    return last_end
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_replace_section.py -v`
Expected: All PASS

**Step 5: Commit**

Autocommit handles this.

---

### Task 3: Core `replace_section` function in `docs_ops.py`

**Files:**
- Modify: `src/gsuite_mcp/docs_ops.py` (add `replace_section` async function)
- Modify: `tests/test_replace_section.py` (add integration-style tests with mocked API)

**Step 1: Write the failing tests**

Append to `tests/test_replace_section.py`:

```python
from unittest.mock import MagicMock, AsyncMock

from gsuite_mcp.docs_ops import replace_section


def _mock_docs_service(doc: dict, batch_response: dict | None = None):
    """Create a mock docs service that returns the given doc structure."""
    svc = MagicMock()
    svc.documents().get.return_value.execute.return_value = doc
    if batch_response is None:
        batch_response = {"replies": []}
    svc.documents().batchUpdate.return_value.execute.return_value = batch_response
    return svc


@pytest.mark.asyncio
async def test_replace_section_basic():
    """Replace body of a section, preserving the heading."""
    doc = _make_doc(
        (1, 20, "Introduction\n", "HEADING_1"),
        (20, 60, "Old body text here.\n", "NORMAL_TEXT"),
        (60, 80, "Conclusion\n", "HEADING_1"),
        (80, 120, "Final words.\n", "NORMAL_TEXT"),
    )
    svc = _mock_docs_service(doc)
    result = await replace_section(svc, "file1", "Introduction", "New body text.\n")

    assert result["section_heading"] == "Introduction\n"
    assert result["heading_level"] == "HEADING_1"
    assert result["characters_deleted"] == 40  # 60 - 20
    assert result["characters_inserted"] == len("New body text.\n")
    assert result["include_heading"] is False

    # Verify batchUpdate was called with delete + insert + style
    requests = svc.documents().batchUpdate.call_args.kwargs["body"]["requests"]
    kinds = [list(r.keys())[0] for r in requests]
    assert "deleteContentRange" in kinds
    assert "insertText" in kinds
    assert "updateParagraphStyle" in kinds

    # Verify delete range
    delete_req = [r for r in requests if "deleteContentRange" in r][0]
    rng = delete_req["deleteContentRange"]["range"]
    assert rng["startIndex"] == 20
    assert rng["endIndex"] == 60


@pytest.mark.asyncio
async def test_replace_section_include_heading():
    """Replace heading + body when include_heading=True."""
    doc = _make_doc(
        (1, 20, "Introduction\n", "HEADING_1"),
        (20, 60, "Old body.\n", "NORMAL_TEXT"),
        (60, 80, "Conclusion\n", "HEADING_1"),
    )
    svc = _mock_docs_service(doc)
    result = await replace_section(
        svc, "file1", "Introduction", "New Intro\nNew body.\n",
        include_heading=True,
    )

    assert result["include_heading"] is True
    assert result["characters_deleted"] == 59  # 60 - 1

    requests = svc.documents().batchUpdate.call_args.kwargs["body"]["requests"]
    delete_req = [r for r in requests if "deleteContentRange" in r][0]
    assert delete_req["deleteContentRange"]["range"]["startIndex"] == 1


@pytest.mark.asyncio
async def test_replace_section_heading_not_found():
    doc = _make_doc(
        (1, 20, "Introduction\n", "HEADING_1"),
    )
    svc = _mock_docs_service(doc)
    result = await replace_section(svc, "file1", "Nonexistent", "text")

    assert result["error"] == "HEADING_NOT_FOUND"
    assert result["retryable"] is False


@pytest.mark.asyncio
async def test_replace_section_ambiguous():
    doc = _make_doc(
        (1, 15, "Summary\n", "HEADING_2"),
        (15, 50, "Body.\n", "NORMAL_TEXT"),
        (50, 65, "Summary\n", "HEADING_2"),
    )
    svc = _mock_docs_service(doc)
    result = await replace_section(svc, "file1", "Summary", "text")

    assert result["error"] == "AMBIGUOUS_HEADING"
    assert len(result["matches"]) == 2


@pytest.mark.asyncio
async def test_replace_section_last_section_extends_to_end():
    """Section at end of doc should extend to the document end."""
    doc = _make_doc(
        (1, 20, "Only Section\n", "HEADING_1"),
        (20, 80, "Body text.\n", "NORMAL_TEXT"),
    )
    svc = _mock_docs_service(doc)
    result = await replace_section(svc, "file1", "Only Section", "Replaced.\n")

    assert result["characters_deleted"] == 60  # 80 - 20

    requests = svc.documents().batchUpdate.call_args.kwargs["body"]["requests"]
    delete_req = [r for r in requests if "deleteContentRange" in r][0]
    assert delete_req["deleteContentRange"]["range"]["endIndex"] == 80


@pytest.mark.asyncio
async def test_replace_section_applies_normal_text_style():
    """Inserted body text should get NORMAL_TEXT paragraph style."""
    doc = _make_doc(
        (1, 20, "Heading\n", "HEADING_1"),
        (20, 60, "Old body.\n", "NORMAL_TEXT"),
        (60, 80, "Next\n", "HEADING_1"),
    )
    svc = _mock_docs_service(doc)
    await replace_section(svc, "file1", "Heading", "New body.\n")

    requests = svc.documents().batchUpdate.call_args.kwargs["body"]["requests"]
    style_reqs = [r for r in requests if "updateParagraphStyle" in r]
    assert len(style_reqs) >= 1
    style = style_reqs[0]["updateParagraphStyle"]
    assert style["paragraphStyle"]["namedStyleType"] == "NORMAL_TEXT"


@pytest.mark.asyncio
async def test_replace_section_include_heading_applies_heading_style():
    """When include_heading=True, first paragraph gets the original heading style."""
    doc = _make_doc(
        (1, 20, "My H2\n", "HEADING_2"),
        (20, 60, "Body.\n", "NORMAL_TEXT"),
        (60, 80, "Next\n", "HEADING_1"),
    )
    svc = _mock_docs_service(doc)
    await replace_section(
        svc, "file1", "My H2", "New H2\nNew body.\n", include_heading=True
    )

    requests = svc.documents().batchUpdate.call_args.kwargs["body"]["requests"]
    style_reqs = [r for r in requests if "updateParagraphStyle" in r]
    # First style request should apply the original heading style
    heading_style = [
        r for r in style_reqs
        if r["updateParagraphStyle"]["paragraphStyle"]["namedStyleType"] == "HEADING_2"
    ]
    assert len(heading_style) == 1


@pytest.mark.asyncio
async def test_replace_section_empty_section_body():
    """Heading immediately followed by next heading — empty section body."""
    doc = _make_doc(
        (1, 20, "Empty\n", "HEADING_1"),
        (20, 40, "Next\n", "HEADING_1"),
    )
    svc = _mock_docs_service(doc)
    result = await replace_section(svc, "file1", "Empty", "New content.\n")

    assert result["error"] == "EMPTY_SECTION"


@pytest.mark.asyncio
async def test_replace_section_ensures_trailing_newline():
    """new_content without trailing newline should get one added."""
    doc = _make_doc(
        (1, 20, "Heading\n", "HEADING_1"),
        (20, 60, "Old.\n", "NORMAL_TEXT"),
        (60, 80, "Next\n", "HEADING_1"),
    )
    svc = _mock_docs_service(doc)
    await replace_section(svc, "file1", "Heading", "No trailing newline")

    requests = svc.documents().batchUpdate.call_args.kwargs["body"]["requests"]
    insert_req = [r for r in requests if "insertText" in r][0]
    assert insert_req["insertText"]["text"].endswith("\n")
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_replace_section.py::test_replace_section_basic -v`
Expected: FAIL — `replace_section` does not exist in `docs_ops`

**Step 3: Implement `replace_section`**

Add to `src/gsuite_mcp/docs_ops.py`:

```python
async def replace_section(
    docs_service,
    file_id: str,
    section_heading: str,
    new_content: str,
    include_heading: bool = False,
) -> dict[str, Any]:
    """Replace a section in a Google Doc identified by its heading text.

    Finds the heading, determines section boundaries (to the next
    same-or-higher-level heading), then does an atomic deleteContentRange +
    insertText + updateParagraphStyle via batchUpdate.
    """
    doc = await asyncio.to_thread(
        lambda: docs_service.documents()
        .get(documentId=file_id)
        .execute()
    )

    # Find the heading
    matches: list[dict] = []
    heading = _find_heading(doc, section_heading, matches_out=matches)

    if heading is None and matches:
        return {
            "error": "AMBIGUOUS_HEADING",
            "retryable": False,
            "message": (
                f"Found {len(matches)} paragraphs matching "
                f"'{section_heading}'. Provide a more specific heading."
            ),
            "matches": [
                {
                    "text": m["text"].strip(),
                    "start_index": m["start_index"],
                    "heading_level": m["heading_level"],
                }
                for m in matches
            ],
        }

    if heading is None:
        return {
            "error": "HEADING_NOT_FOUND",
            "retryable": False,
            "message": f"No heading matching '{section_heading}' found in document.",
        }

    # Find section boundaries
    section_end = _find_section_end(doc, heading)

    if include_heading:
        delete_start = heading["start_index"]
    else:
        delete_start = heading["end_index"]

    delete_end = section_end

    if delete_start >= delete_end:
        if not include_heading:
            return {
                "error": "EMPTY_SECTION",
                "retryable": False,
                "message": (
                    f"Section '{section_heading}' has no body content. "
                    f"Use include_heading=True to replace the heading itself."
                ),
            }

    # Ensure trailing newline
    if not new_content.endswith("\n"):
        new_content += "\n"

    chars_deleted = delete_end - delete_start
    chars_inserted = len(new_content)

    # Build requests
    requests: list[dict] = []

    # 1. Delete existing section content
    requests.append({
        "deleteContentRange": {
            "range": {"startIndex": delete_start, "endIndex": delete_end}
        }
    })

    # 2. Insert new content
    requests.append({
        "insertText": {
            "location": {"index": delete_start},
            "text": new_content,
        }
    })

    # 3. Apply paragraph styles to inserted text
    insert_end = delete_start + chars_inserted

    if include_heading and heading["heading_level"] != "NORMAL_TEXT":
        # Find end of first paragraph (first newline)
        first_nl = new_content.find("\n")
        first_para_end = delete_start + first_nl + 1

        # Apply heading style to first paragraph
        requests.append({
            "updateParagraphStyle": {
                "range": {"startIndex": delete_start, "endIndex": first_para_end},
                "paragraphStyle": {"namedStyleType": heading["heading_level"]},
                "fields": "namedStyleType",
            }
        })

        # Apply NORMAL_TEXT to remaining paragraphs (if any)
        if first_para_end < insert_end:
            requests.append({
                "updateParagraphStyle": {
                    "range": {"startIndex": first_para_end, "endIndex": insert_end},
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                    "fields": "namedStyleType",
                }
            })
    else:
        # All inserted text gets NORMAL_TEXT
        requests.append({
            "updateParagraphStyle": {
                "range": {"startIndex": delete_start, "endIndex": insert_end},
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "fields": "namedStyleType",
            }
        })

    await retry_transient(
        lambda: docs_service.documents()
        .batchUpdate(documentId=file_id, body={"requests": requests})
        .execute()
    )

    return {
        "file_id": file_id,
        "section_heading": heading["text"],
        "heading_level": heading["heading_level"],
        "characters_deleted": chars_deleted,
        "characters_inserted": chars_inserted,
        "include_heading": include_heading,
    }
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_replace_section.py -v`
Expected: All PASS

**Step 5: Commit**

Autocommit handles this.

---

### Task 4: Server tool wrapper in `server.py`

**Files:**
- Modify: `src/gsuite_mcp/server.py` (add `@mcp.tool() replace_section`)
- Modify: `tests/test_replace_section.py` (add server-level tests)

**Step 1: Write the failing tests**

Append to `tests/test_replace_section.py`:

```python
from unittest.mock import patch


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
async def test_server_replace_section_not_a_google_doc(mock_services):
    """Server wrapper should reject non-Google-Doc files."""
    drive = mock_services["drive"]
    drive.files().get.return_value.execute.return_value = {
        "name": "file.docx",
        "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "modifiedTime": "2026-05-11T00:00:00Z",
    }

    from gsuite_mcp.server import replace_section as server_replace_section
    result = await server_replace_section(
        file_id="d1", section_heading="Intro", new_content="text"
    )
    assert result["error"] == "NOT_A_GOOGLE_DOC"


@pytest.mark.asyncio
async def test_server_replace_section_success(mock_services):
    """Server wrapper should pass through to docs_ops and return modified_time."""
    drive = mock_services["drive"]
    docs = mock_services["docs"]
    drive.files().get.return_value.execute.return_value = {
        "name": "doc", "mimeType": "application/vnd.google-apps.document",
        "modifiedTime": "2026-05-11T00:00:00Z",
    }
    docs.documents().get.return_value.execute.return_value = _make_doc(
        (1, 20, "Heading\n", "HEADING_1"),
        (20, 60, "Old body.\n", "NORMAL_TEXT"),
        (60, 80, "Next\n", "HEADING_1"),
    )
    docs.documents().batchUpdate.return_value.execute.return_value = {"replies": []}

    from gsuite_mcp.server import replace_section as server_replace_section
    result = await server_replace_section(
        file_id="d1", section_heading="Heading", new_content="New body.\n"
    )
    assert "error" not in result
    assert result["file_id"] == "d1"
    assert "modified_time" in result


@pytest.mark.asyncio
async def test_server_replace_section_catches_http_error(mock_services):
    """Server wrapper should catch HttpError and return structured error."""
    drive = mock_services["drive"]
    docs = mock_services["docs"]
    drive.files().get.return_value.execute.return_value = {
        "name": "doc", "mimeType": "application/vnd.google-apps.document",
        "modifiedTime": "2026-05-11T00:00:00Z",
    }
    docs.documents().get.return_value.execute.return_value = _make_doc(
        (1, 20, "Heading\n", "HEADING_1"),
        (20, 60, "Body.\n", "NORMAL_TEXT"),
    )
    from googleapiclient.errors import HttpError
    resp = MagicMock()
    resp.status = 500
    docs.documents().batchUpdate.return_value.execute.side_effect = HttpError(resp, b"err")

    from gsuite_mcp.server import replace_section as server_replace_section
    result = await server_replace_section(
        file_id="d1", section_heading="Heading", new_content="New.\n"
    )
    assert result["error"] == "GOOGLE_API_ERROR"
    assert result["retryable"] is True
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_replace_section.py::test_server_replace_section_not_a_google_doc -v`
Expected: FAIL — `replace_section` not in `server.py`

**Step 3: Implement server wrapper**

Add to `src/gsuite_mcp/server.py` (after the existing `replace_text` tool):

```python
@mcp.tool()
async def replace_section(
    file_id: str,
    section_heading: str,
    new_content: str,
    include_heading: bool = False,
) -> dict[str, Any]:
    """Replace content in a Google Doc by heading/section.

    Finds a heading by text match (formal heading styles first, then any
    paragraph as fallback), determines the section boundary (to the next
    same-or-higher-level heading), and replaces the content atomically.

    Args:
        file_id: Google Drive file ID of a native Google Doc.
        section_heading: Text of the heading to find (case-insensitive, stripped).
        new_content: Replacement text for the section body (or heading+body
            if include_heading=True).
        include_heading: If True, also replace the heading paragraph itself.
            Default False (preserve heading, replace only body).
    """
    drive = auth.get_drive_service()
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
                f"replace_section only works on Google Docs. This file is "
                f"{meta.get('mimeType')}."
            ),
        }

    docs = auth.get_docs_service()
    try:
        result = await docs_ops.replace_section(
            docs, file_id, section_heading, new_content, include_heading
        )
        if "error" in result:
            return result
        # Fetch updated modifiedTime
        meta2 = await asyncio.to_thread(
            lambda: drive.files()
            .get(fileId=file_id, fields="modifiedTime")
            .execute()
        )
        result["modified_time"] = meta2.get("modifiedTime", "")
        return result
    except HttpError as exc:
        status = exc.resp.status if exc.resp else 0
        return {
            "error": "GOOGLE_API_ERROR",
            "retryable": status in TRANSIENT_CODES,
            "http_status": status,
            "message": (
                f"Google Docs API error (HTTP {status}) after retries: {exc}"
            ),
        }
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_replace_section.py -v`
Expected: All PASS

**Step 5: Run the full test suite**

Run: `uv run pytest -q`
Expected: All tests pass (73 existing + new tests)

**Step 6: Commit**

Autocommit handles this.

---

### Task 5: Update CLAUDE.md and lint

**Files:**
- Modify: `CLAUDE.md` (add tool #13 to Tools list, update test count)

**Step 1: Update CLAUDE.md**

Add to the Tools list:
```
13. `replace_section` — replace content by heading/section in Google Docs (formal heading styles + text fallback)
```

Update Project Structure to mention the new function in `docs_ops.py`.

**Step 2: Run linter**

Run: `uv run ruff check .`
Expected: No errors

**Step 3: Run full test suite**

Run: `uv run pytest -q`
Expected: All pass

**Step 4: Commit**

Autocommit handles this.
