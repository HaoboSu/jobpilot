"""Minimal DOCX writer using only zipfile + stdlib (no python-docx needed)."""
from __future__ import annotations

import io
import re
import zipfile
from xml.sax.saxutils import escape

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""


def _run(text: str, bold: bool = False, size: int | None = None,
         italic: bool = False) -> str:
    props = ['<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei"/>']
    if bold:
        props.append("<w:b/>")
    if italic:
        props.append("<w:i/>")
    if size:
        props.append(f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>')
    return (
        "<w:r><w:rPr>" + "".join(props) + "</w:rPr>"
        f'<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'
    )


def _paragraph(line: str) -> str:
    raw = line.rstrip()
    size = None
    bold_all = False
    italic = False
    indent = 0
    if raw.startswith("# "):
        raw, size, bold_all = raw[2:], 34, True
    elif raw.startswith("## "):
        raw, size, bold_all = raw[3:], 26, True
    elif raw.startswith("> "):
        raw, italic = raw[2:], True
    elif raw.strip() == "---":
        return "<w:p><w:r><w:t></w:t></w:r></w:p>"
    elif re.match(r"^\s*[-·•*]\s+", raw):
        raw = re.sub(r"^(\s*)[-·•*]\s+", r"\1• ", raw, count=1)
        indent = 240
    if not raw:
        return "<w:p><w:r><w:t></w:t></w:r></w:p>"
    ppr = f'<w:pPr><w:spacing w:after="80"/>'
    if indent:
        ppr += f'<w:ind w:left="{indent}"/>'
    ppr += "</w:pPr>"
    runs: list[str] = []
    for part in re.split(r"(\*\*[^*]+\*\*)", raw):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            runs.append(_run(part[2:-2], bold=True, size=size, italic=italic))
        else:
            runs.append(_run(part, bold=bold_all, size=size, italic=italic))
    return "<w:p>" + ppr + "".join(runs) + "</w:p>"


def build_docx(content: str, title: str = "") -> bytes:
    lines = content.splitlines()
    body = "".join(_paragraph(ln) for ln in lines)
    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}"><w:body>{body}'
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/>'
        '</w:sectPr></w:body></w:document>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr("_rels/.rels", RELS)
        zf.writestr("word/document.xml", document)
    return buf.getvalue()
