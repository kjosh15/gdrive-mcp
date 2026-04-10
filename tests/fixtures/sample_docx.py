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
