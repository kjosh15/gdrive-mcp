# Design: `replace_section` Tool

## Problem

`replace_text` does exact string matching on Google Docs, which breaks on multi-paragraph content due to invisible formatting characters, whitespace differences, and Google Docs' internal representation. It works for single sentences but not section-level rewrites.

## Solution

A new `replace_section` tool that replaces content by heading/section rather than by exact string match. Uses the Google Docs API's positional operations (`deleteContentRange` + `insertText` + `updateParagraphStyle`) in a single atomic `batchUpdate`.

## Tool Signature

```python
@mcp.tool()
async def replace_section(
    file_id: str,
    section_heading: str,           # e.g. "4. Zanzibar (Proving Repeatability)"
    new_content: str,               # replacement text
    include_heading: bool = False,  # False = preserve heading, True = replace it too
) -> dict[str, Any]
```

## Section Detection

1. Fetch full document via `documents().get()`
2. Walk `body.content` structural elements:
   - **Pass 1 — formal headings:** Match paragraphs where `paragraphStyle.namedStyleType` is `HEADING_1`–`HEADING_6` AND paragraph text matches `section_heading` (stripped, case-insensitive)
   - **Pass 2 — text fallback:** If no formal heading found, match any paragraph whose text matches `section_heading` (stripped, case-insensitive). Treated as level 7 (section ends at next formal heading or end of doc)
3. **Ambiguity check:** If multiple paragraphs match, return error with all matches (text, index, heading level) so caller can provide a more specific string
4. **Section boundary:** Scan forward from matched heading to the next paragraph with heading level <= matched heading's level. If none found, section extends to end of document body.

## Mutation (single `batchUpdate`)

Working in reverse index order (same pattern as `replace_regex`):

1. `delete_start`: heading's `startIndex` if `include_heading=True`, otherwise next element's `startIndex` after heading paragraph
2. `delete_end`: next-heading's `startIndex` (or body end index - 1)
3. Requests:
   - `deleteContentRange` for `[delete_start, delete_end)`
   - `insertText` at `delete_start` with `new_content` (ensure trailing newline)
   - `updateParagraphStyle` on inserted range: `NORMAL_TEXT` for body paragraphs
   - If `include_heading=True`: `updateParagraphStyle` on first inserted paragraph with original heading's `namedStyleType`

## Return Value

Success:
```python
{
    "file_id": str,
    "section_heading": str,
    "heading_level": str,          # e.g. "HEADING_2"
    "characters_deleted": int,
    "characters_inserted": int,
    "include_heading": bool,
    "modified_time": str,
}
```

Errors:
- `NOT_A_GOOGLE_DOC` — file isn't a native Google Doc
- `HEADING_NOT_FOUND` — no heading matched `section_heading`
- `AMBIGUOUS_HEADING` — multiple matches, includes `matches` list for disambiguation
- `EMPTY_SECTION` — heading found but section has no body content to replace (only when `include_heading=False`)

## File Layout

- `src/gsuite_mcp/docs_ops.py` — new `replace_section()` with heading detection + mutation logic
- `src/gsuite_mcp/server.py` — new `@mcp.tool() replace_section` thin wrapper
- `tests/test_docs_ops.py` — tests for heading detection, section boundary, ambiguity, include_heading, text fallback, paragraph style application

## Approach

Single `batchUpdate` (Approach A). Follows the existing pattern in `replace_regex` which already does fetch-walk-delete-insert. No new dependencies, no new scopes, no extra round-trips.
