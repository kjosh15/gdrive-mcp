"""Tests for _find_heading, _find_section_end, and replace_section in docs_ops."""

import pytest
from unittest.mock import MagicMock

from gsuite_mcp.docs_ops import (
    _FALLBACK_RANK,
    _HEADING_RANKS,
    _find_heading,
    _find_section_end,
    _para_text,
    replace_section,
)


def _make_doc(*paragraphs):
    """Build a minimal Google Docs body structure.

    Each paragraph is a tuple: (start, end, text, named_style).
    """
    content = []
    for idx, (start, end, text, named_style) in enumerate(paragraphs):
        content.append({
            "startIndex": start,
            "endIndex": end,
            "paragraph": {
                "paragraphStyle": {"namedStyleType": named_style},
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


# -------------------------------------------------------------------
# _para_text helper
# -------------------------------------------------------------------

def test_para_text_extracts_text():
    para = {
        "elements": [
            {"textRun": {"content": "Hello "}},
            {"textRun": {"content": "World"}},
        ]
    }
    assert _para_text(para) == "Hello World"


def test_para_text_handles_missing_text_run():
    para = {
        "elements": [
            {"inlineObjectElement": {"inlineObjectId": "obj1"}},
            {"textRun": {"content": "text"}},
        ]
    }
    assert _para_text(para) == "text"


# -------------------------------------------------------------------
# _HEADING_RANKS constant
# -------------------------------------------------------------------

def test_heading_ranks_maps_all_levels():
    for level in range(1, 7):
        assert _HEADING_RANKS[f"HEADING_{level}"] == level
    assert _FALLBACK_RANK == 7


# -------------------------------------------------------------------
# _find_heading
# -------------------------------------------------------------------

def test_find_heading_formal_match():
    """Finds a HEADING_1 paragraph by its text."""
    doc = _make_doc(
        (0, 13, "Introduction\n", "HEADING_1"),
        (13, 30, "Some body text.\n", "NORMAL_TEXT"),
    )
    result = _find_heading(doc, "Introduction")
    assert result is not None
    assert result["text"] == "Introduction"
    assert result["heading_level"] == "HEADING_1"
    assert result["level_rank"] == 1
    assert result["start_index"] == 0
    assert result["end_index"] == 13
    assert result["paragraph_index"] == 0


def test_find_heading_case_insensitive():
    """'introduction' matches 'Introduction' (case-insensitive)."""
    doc = _make_doc(
        (0, 13, "Introduction\n", "HEADING_2"),
    )
    result = _find_heading(doc, "introduction")
    assert result is not None
    assert result["text"] == "Introduction"
    assert result["heading_level"] == "HEADING_2"
    assert result["level_rank"] == 2


def test_find_heading_strips_whitespace():
    """'  Introduction  \\n' matches 'Introduction' after stripping."""
    doc = _make_doc(
        (0, 18, "  Introduction  \n", "HEADING_3"),
    )
    result = _find_heading(doc, "Introduction")
    assert result is not None
    assert result["text"] == "Introduction"
    assert result["heading_level"] == "HEADING_3"
    assert result["level_rank"] == 3


def test_find_heading_text_fallback():
    """Matches a NORMAL_TEXT paragraph when no formal heading matches."""
    doc = _make_doc(
        (0, 13, "Introduction\n", "NORMAL_TEXT"),
        (13, 25, "Body text.\n", "NORMAL_TEXT"),
    )
    result = _find_heading(doc, "Introduction")
    assert result is not None
    assert result["text"] == "Introduction"
    assert result["heading_level"] == "NORMAL_TEXT"
    assert result["level_rank"] == _FALLBACK_RANK


def test_find_heading_not_found():
    """Returns None for a heading that does not exist."""
    doc = _make_doc(
        (0, 13, "Introduction\n", "HEADING_1"),
    )
    result = _find_heading(doc, "Conclusion")
    assert result is None


def test_find_heading_ambiguous_returns_none_with_matches():
    """Two 'Summary' headings -> None, and populates matches_out."""
    doc = _make_doc(
        (0, 9, "Summary\n", "HEADING_1"),
        (9, 25, "Some body text.\n", "NORMAL_TEXT"),
        (25, 34, "Summary\n", "HEADING_2"),
    )
    matches_out = []
    result = _find_heading(doc, "Summary", matches_out=matches_out)
    assert result is None
    assert len(matches_out) == 2
    assert matches_out[0]["heading_level"] == "HEADING_1"
    assert matches_out[1]["heading_level"] == "HEADING_2"


def test_find_heading_prefers_formal_over_fallback():
    """When a formal heading and a text fallback both match, only the
    formal heading is considered (pass 1 succeeds, pass 2 is skipped)."""
    doc = _make_doc(
        (0, 13, "Introduction\n", "HEADING_1"),
        (13, 26, "Introduction\n", "NORMAL_TEXT"),
    )
    result = _find_heading(doc, "Introduction")
    assert result is not None
    assert result["heading_level"] == "HEADING_1"
    assert result["level_rank"] == 1


# -------------------------------------------------------------------
# _find_section_end
# -------------------------------------------------------------------

def test_section_end_at_same_level_heading():
    """HEADING_1 section ends at the next HEADING_1."""
    doc = _make_doc(
        (0, 10, "Chapter 1\n", "HEADING_1"),
        (10, 30, "Some body text here.\n", "NORMAL_TEXT"),
        (30, 40, "Chapter 2\n", "HEADING_1"),
        (40, 55, "More body text.\n", "NORMAL_TEXT"),
    )
    heading = _find_heading(doc, "Chapter 1")
    assert heading is not None
    end = _find_section_end(doc, heading)
    # Section ends just before the next HEADING_1 starts at index 30
    assert end == 30


def test_section_end_at_higher_level_heading():
    """HEADING_2 section ends when a HEADING_1 is encountered."""
    doc = _make_doc(
        (0, 10, "Chapter 1\n", "HEADING_1"),
        (10, 22, "Sub-section\n", "HEADING_2"),
        (22, 40, "Sub-section body.\n", "NORMAL_TEXT"),
        (40, 50, "Chapter 2\n", "HEADING_1"),
    )
    heading = _find_heading(doc, "Sub-section")
    assert heading is not None
    end = _find_section_end(doc, heading)
    # HEADING_1 (rank 1) <= HEADING_2 (rank 2), so section ends at index 40
    assert end == 40


def test_section_end_at_document_end():
    """Last section extends to the end of the document."""
    doc = _make_doc(
        (0, 10, "Chapter 1\n", "HEADING_1"),
        (10, 30, "Some body text here.\n", "NORMAL_TEXT"),
        (30, 40, "Chapter 2\n", "HEADING_1"),
        (40, 60, "Final body text....\n", "NORMAL_TEXT"),
    )
    heading = _find_heading(doc, "Chapter 2")
    assert heading is not None
    end = _find_section_end(doc, heading)
    # No heading after Chapter 2, so section extends to end of last block
    assert end == 60


def test_section_end_skips_lower_level_headings():
    """HEADING_1 section spans over HEADING_2 and HEADING_3 subsections."""
    doc = _make_doc(
        (0, 10, "Chapter 1\n", "HEADING_1"),
        (10, 25, "Intro paragraph.\n", "NORMAL_TEXT"),
        (25, 40, "Sub-section A\n", "HEADING_2"),
        (40, 55, "Sub-section body\n", "NORMAL_TEXT"),
        (55, 72, "Sub-sub-section\n", "HEADING_3"),
        (72, 90, "Sub-sub body text.\n", "NORMAL_TEXT"),
        (90, 100, "Chapter 2\n", "HEADING_1"),
    )
    heading = _find_heading(doc, "Chapter 1")
    assert heading is not None
    end = _find_section_end(doc, heading)
    # HEADING_2 (rank 2) and HEADING_3 (rank 3) are lower-level (higher rank)
    # than HEADING_1 (rank 1), so they are skipped. Section ends at Chapter 2.
    assert end == 90


def test_section_end_fallback_heading_stops_at_any_formal():
    """A fallback heading (rank 7) section ends at any formal heading."""
    doc = _make_doc(
        (0, 15, "My Bold Title\n", "NORMAL_TEXT"),
        (15, 30, "Some body text.\n", "NORMAL_TEXT"),
        (30, 45, "A Real Heading\n", "HEADING_3"),
        (45, 60, "More body text.\n", "NORMAL_TEXT"),
    )
    heading = _find_heading(doc, "My Bold Title")
    assert heading is not None
    assert heading["level_rank"] == _FALLBACK_RANK
    end = _find_section_end(doc, heading)
    # Any formal heading (HEADING_3, rank 3) terminates a fallback section (rank 7)
    assert end == 30
