"""Google Docs v1 operations — append, replace_text, heading detection."""

import asyncio
import re
from typing import Any

from gsuite_mcp.retry import retry_transient

# ---------------------------------------------------------------------------
# Heading detection helpers
# ---------------------------------------------------------------------------

_HEADING_RANKS: dict[str, int] = {
    f"HEADING_{i}": i for i in range(1, 7)
}

_FALLBACK_RANK: int = 7


def _para_text(paragraph: dict) -> str:
    """Extract plain text from a paragraph's elements (concatenated textRuns)."""
    parts: list[str] = []
    for elem in paragraph.get("elements", []):
        tr = elem.get("textRun")
        if tr:
            parts.append(tr.get("content", ""))
    return "".join(parts)


def _find_heading(
    doc: dict,
    section_heading: str,
    matches_out: list | None = None,
) -> dict[str, Any] | None:
    """Locate a section heading inside a Google Docs document structure.

    Pass 1 — formal headings (HEADING_1 through HEADING_6).
    Pass 2 — text fallback (any paragraph whose stripped text matches).

    Returns a dict with ``text``, ``start_index``, ``end_index``,
    ``heading_level``, ``level_rank``, and ``paragraph_index`` when exactly
    one match is found.  Returns ``None`` on zero or multiple matches;
    populates *matches_out* (if provided) when ambiguous.

    When Pass 1 produces multiple formal matches, Pass 2 is not attempted
    and *matches_out* contains only the formal matches.
    """
    content = doc.get("body", {}).get("content", [])
    needle = section_heading.strip().lower()

    def _build_match(block: dict, para_idx: int, named_style: str) -> dict:
        raw_text = _para_text(block["paragraph"])
        return {
            "text": raw_text.strip(),
            "start_index": block["startIndex"],
            "end_index": block["endIndex"],
            "heading_level": named_style,
            "level_rank": _HEADING_RANKS.get(named_style, _FALLBACK_RANK),
            "paragraph_index": para_idx,
        }

    # Pass 1 — formal headings
    formal: list[dict] = []
    for idx, block in enumerate(content):
        para = block.get("paragraph")
        if not para:
            continue
        style = para.get("paragraphStyle", {}).get("namedStyleType", "")
        if style not in _HEADING_RANKS:
            continue
        text = _para_text(para).strip().lower()
        if text == needle:
            formal.append(_build_match(block, idx, style))

    if len(formal) == 1:
        return formal[0]
    if formal:
        if matches_out is not None:
            matches_out.extend(formal)
        return None

    # Pass 2 — text fallback (no formal heading matched)
    fallback: list[dict] = []
    for idx, block in enumerate(content):
        para = block.get("paragraph")
        if not para:
            continue
        style = para.get("paragraphStyle", {}).get("namedStyleType", "")
        text = _para_text(para).strip().lower()
        if text == needle:
            fallback.append(_build_match(block, idx, style))

    if len(fallback) == 1:
        return fallback[0]
    if fallback:
        if matches_out is not None:
            matches_out.extend(fallback)
    return None


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
    await retry_transient(
        lambda: docs_service.documents()
        .batchUpdate(documentId=file_id, body={"requests": requests})
        .execute()
    )
    return {"bytes_appended": len(text.encode("utf-8"))}


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
    resp = await retry_transient(
        lambda: docs_service.documents()
        .batchUpdate(documentId=file_id, body={"requests": requests})
        .execute()
    )
    reply = resp.get("replies", [{}])[0]
    return reply.get("replaceAllText", {}).get("occurrencesChanged", 0)


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

    await retry_transient(
        lambda: docs_service.documents()
        .batchUpdate(documentId=file_id, body={"requests": requests})
        .execute()
    )
    return len(matches)
