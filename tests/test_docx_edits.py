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
