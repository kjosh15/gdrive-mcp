# Design: gdoc_template_populate + gdoc_suggest_edit

**Date:** 2026-05-05

## Context

Two new tools for gsuite-mcp:
1. Populate Google Doc templates from .docx or native Doc sources
2. Apply tracked-change suggestions to native Google Docs (via .docx export/re-upload)

### API Constraint

The Google Docs API cannot create suggestions programmatically ([docs](https://developers.google.com/workspace/docs/api/how-tos/suggestions), [feature request #287903901](https://issuetracker.google.com/issues/287903901)). Tracked changes are only possible via OOXML manipulation on .docx files. The `gdoc_suggest_edit` tool works around this by exporting → editing → re-uploading as .docx.

## Tool 1: gdoc_template_populate

**Inputs:** `template_file_id`, `parent_folder_id`, `new_title`, `replacements: dict[str, str]`

**Flow:**
1. `drive.files().copy()` with `mimeType: 'application/vnd.google-apps.document'`, `parents: [parent_folder_id]`, `name: new_title` — copies AND converts .docx to native Doc in one call
2. `docs.documents().batchUpdate()` with one `replaceAllText` request per placeholder
3. Return `{file_id, web_view_link, replacements_made: {placeholder: count}}`

## Tool 2: gdoc_suggest_edit

**Inputs:** `file_id`, `find_text`, `replace_text`, `author` (default "Claude")

**Flow:**
1. Verify file is a native Google Doc (mimeType check)
2. Export as .docx via `drive.files().export(mimeType=DOCX_MIME)`
3. Apply `docx_edits.insert_tracked_change()` on exported bytes
4. Upload modified .docx as a **new file** (name = `{original_name} (with suggestions).docx`)
5. Return `{file_id: new_file_id, web_view_link, original_file_id, note}`

**Key decision:** New .docx sibling file, not overwrite. Overwriting the original would lose native Doc format and tracked changes wouldn't survive conversion back.

## New Module

`src/gsuite_mcp/gdoc_ops.py` — async functions following existing `*_ops.py` pattern.

## Tests

- **Unit tests** (mocked): `tests/test_gdoc_template_populate.py`, `tests/test_gdoc_suggest_edit.py`
- **Integration tests** (`@pytest.mark.integration`): Real BTR_Template (file ID `1Q1avdQF1gP2hWtIV3VOQg9Exd7zQtu_s`), PDF export visual diff

## Integration Test: BTR_Template

Placeholders to replace:
- `NAME OF CLIENT` → test client name
- Date placeholder → current date
- `This Is A Template` → test title
- Lorem Ipsum body → test body content

Verify formatting preservation by exporting both source and result as PDF.
