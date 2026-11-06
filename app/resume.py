"""Resume import, parsing, normalisation and fact helpers.

Zero third-party dependencies: .txt/.md/.docx/.json use stdlib parsers and PDF
uses the built-in app.pdf extractor. If pypdf / PyPDF2 / PyMuPDF / pdfminer is
installed it is preferred for PDF, with the built-in extractor as fallback.
"""
from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import re
import zipfile
from datetime import datetime
from xml.etree import ElementTree as ET

from . import skills as sk

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

MAX_BYTES = 8 * 1024 * 1024

SECTION_RULES: list[tuple[str, list[str]]] = [
    ("basics", ["基本信息", "个人资料", "联系方式", "个人信息", "contact", "personal information"]),
    ("summary", ["个人简介", "自我评价", "求职意向", "职业概述", "个人概述", "简介",
                 "summary", "profile", "objective", "about me"]),
    ("skills", ["核心技能", "专业技能", "技能清单", "技能特长", "技术栈", "技能",
                "skills", "technical skills", "core competencies"]),
    ("experience", ["工作经历", "工作经验", "职业经历", "实习经历", "工作履历", "从业经历",
                    "work experience", "employment history", "experience", "professional experience"]),
    ("projects", ["项目经历", "项目经验", "个人项目", "代表项目", "projects", "project experience"]),
    ("education", ["教育背景", "教育经历", "学习经历", "学历", "education", "academic background"]),
    ("certifications", ["证书", "资格证书", "认证", "certifications", "certificates", "licenses"]),
    ("languages", ["语言能力", "语言", "languages"]),
    ("extras", ["其他", "附加信息", "兴趣爱好", "others", "additional", "interests"]),
]

_HEADER_RE = re.compile(r"^[#*=\-—\s]*")
_BULLET_RE = re.compile(r"^\s*(?:[-•·*◦▪]|\d+[.、)]|[（(]\d+[)）])\s*")
_DATE_RE = re.compile(
    r"(?:19|20)\d{2}\s*[.\-/年]\s*\d{1,2}\s*月?\s*(?:[-~–—至到]+\s*"
    r"(?:(?:19|20)\d{2}\s*[.\-/年]\s*\d{1,2}\s*月?|至今|现在|present|now|current))?",
    re.I,
)
_DATE_SIMPLE_RE = re.compile(r"(?:19|20)\d{2}\s*[.\-/年]\s*\d{1,2}\s*月?|(?:19|20)\d{2}\s*年?", re.I)
_SPLIT_RE = re.compile(r"[,，、;；/|｜]+|\s{2,}")


# --------------------------------------------------------------- file decode
def decode_upload(filename: str, b64: str | None, text: str | None) -> tuple[str, str, str]:
    """Return (text, format, display_filename)."""
    filename = os.path.basename(filename or "resume.txt")
    ext = os.path.splitext(filename)[1].lower()
    if text is not None:
        return text.strip(), (ext.lstrip(".") or "txt"), filename
    if not b64:
        raise ValueError("没有收到文件内容")
    raw = base64.b64decode(b64)
    if len(raw) > MAX_BYTES:
        raise ValueError("文件超过 8MB 限制")
    if ext in (".txt", ".md", ".markdown", ".csv", ".json"):
        return _decode_bytes(raw), ext.lstrip("."), filename
    if ext == ".docx":
        return _docx_text(raw), "docx", filename
    if ext == ".pdf":
        return _pdf_text(raw), "pdf", filename
    if ext in (".doc", ".wps"):
        raise ValueError("旧版 .doc/.wps 暂不支持，请另存为 .docx 或 .txt 后再导入")
    # unknown extension: try plain text
    return _decode_bytes(raw), ext.lstrip(".") or "txt", filename


def _decode_bytes(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk", "big5", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _docx_text(raw: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        xml = zf.read("word/document.xml")
    root = ET.fromstring(xml)
    lines: list[str] = []
    for para in root.iter(W_NS + "p"):
        parts: list[str] = []
        for node in para.iter():
            if node.tag == W_NS + "t" and node.text:
                parts.append(node.text)
            elif node.tag == W_NS + "tab":
                parts.append("\t")
            elif node.tag in (W_NS + "br", W_NS + "cr"):
                parts.append("\n")
        text = "".join(parts).strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def _library_pdf_text(name: str, raw: bytes) -> str:
    """Use a full PDF library when one happens to be installed."""
    if name in ("pypdf", "PyPDF2"):
        module = __import__(name)
        reader = module.PdfReader(io.BytesIO(raw))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    if name == "fitz":
        fitz = __import__("fitz")
        with fitz.open(stream=raw, filetype="pdf") as doc:
            return "\n".join(page.get_text() for page in doc)
    from pdfminer.high_level import extract_text  # type: ignore
    return extract_text(io.BytesIO(raw))


def _pdf_text(raw: bytes) -> str:
    """Extract text from an uploaded PDF.

    Priority: installed PDF libraries -> built-in stdlib extractor. Reject
    obvious decoding artifacts before accepting a library result. Users still
    need to review extracted text for missing characters and layout issues.
    """
    from . import pdf as pdfmod

    if b"%PDF" not in raw[:1024]:
        raise ValueError("文件不是有效的 PDF（未找到 %PDF 文件头），请确认上传的是 PDF 简历")
    notes: list[str] = []
    for name in ("pypdf", "PyPDF2", "fitz", "pdfminer"):
        try:
            text = _library_pdf_text(name, raw)
        except ImportError:
            continue
        except Exception as exc:  # noqa: BLE001 - fall through to builtin
            notes.append(f"{name} 解析失败（{type(exc).__name__}）")
            continue
        garbled = re.search(r"[\x00-\x08\x0b\x0e-\x1f\ufffd]|\(cid:\d+\)", text)
        if not garbled and len(re.sub(r"\s", "", text)) >= 8:
            return text.strip()
        notes.append(f"{name} 文字不足或含解码异常，继续尝试其他解析器")
    try:
        return pdfmod.extract_text(raw)
    except pdfmod.PDFError as exc:
        detail = ("增强解析器状态：" + "；".join(notes)) if notes else ""
        raise ValueError(
            f"{exc}"
            + (f"（{detail}）" if detail else "")
            + " 你也可以点「粘贴文字导入」，或把简历另存为 .docx / .txt 后再上传。"
        ) from exc


# ------------------------------------------------------------- normalisation
def empty_structured() -> dict:
    return {
        "basics": {"name": "", "email": "", "phone": "", "location": "",
                   "headline": "", "links": []},
        "summary": "",
        "skills": [],
        "experiences": [],
        "projects": [],
        "education": [],
        "certifications": [],
        "languages": [],
        "extras": [],
    }


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [x.strip() for x in re.split(r"[,，、;；\n]+", value) if x.strip()]
    return [value]


def normalize_structured(obj: dict) -> dict:
    """Coerce arbitrary user-edited JSON into the canonical shape."""
    obj = obj or {}
    out = empty_structured()
    basics = obj.get("basics") if isinstance(obj.get("basics"), dict) else {}
    for key in out["basics"]:
        val = basics.get(key, obj.get(key, ""))
        if key == "links":
            out["basics"][key] = [str(x) for x in _as_list(val)]
        else:
            out["basics"][key] = str(val or "").strip()
    out["summary"] = str(obj.get("summary") or "").strip()
    out["skills"] = [str(x).strip() for x in _as_list(obj.get("skills")) if str(x).strip()]

    for row in _as_list(obj.get("experiences")):
        if isinstance(row, dict):
            out["experiences"].append({
                "company": str(row.get("company") or "").strip(),
                "title": str(row.get("title") or "").strip(),
                "start": str(row.get("start") or "").strip(),
                "end": str(row.get("end") or "").strip(),
                "location": str(row.get("location") or "").strip(),
                "bullets": [str(x).strip() for x in _as_list(row.get("bullets")) if str(x).strip()],
            })
        else:
            out["experiences"].append({
                "company": "", "title": str(row).strip(), "start": "", "end": "",
                "location": "", "bullets": [],
            })
    for row in _as_list(obj.get("projects")):
        if isinstance(row, dict):
            out["projects"].append({
                "name": str(row.get("name") or "").strip(),
                "role": str(row.get("role") or "").strip(),
                "period": str(row.get("period") or "").strip(),
                "description": str(row.get("description") or "").strip(),
                "bullets": [str(x).strip() for x in _as_list(row.get("bullets")) if str(x).strip()],
            })
        else:
            out["projects"].append({"name": str(row).strip(), "role": "", "period": "",
                                   "description": "", "bullets": []})
    for row in _as_list(obj.get("education")):
        if isinstance(row, dict):
            out["education"].append({
                "school": str(row.get("school") or "").strip(),
                "degree": str(row.get("degree") or "").strip(),
                "major": str(row.get("major") or "").strip(),
                "start": str(row.get("start") or "").strip(),
                "end": str(row.get("end") or "").strip(),
            })
        else:
            out["education"].append({"school": str(row).strip(), "degree": "",
                                     "major": "", "start": "", "end": ""})
    out["certifications"] = [str(x).strip() for x in _as_list(obj.get("certifications")) if str(x).strip()]
    out["languages"] = [str(x).strip() for x in _as_list(obj.get("languages")) if str(x).strip()]
    for row in _as_list(obj.get("extras")):
        if isinstance(row, dict):
            out["extras"].append({
                "heading": str(row.get("heading") or "其他").strip(),
                "lines": [str(x).strip() for x in _as_list(row.get("lines")) if str(x).strip()],
            })
        else:
            out["extras"].append({"heading": "其他", "lines": [str(row).strip()]})
    if obj.get("_sample"):
        out["_sample"] = True
    return out


# ----------------------------------------------------------- text -> structure
def parse_structured(text: str) -> dict:
    """Heuristic section parser for free-form resumes."""
    text = (text or "").strip()
    if not text:
        return empty_structured()
    # Structured JSON input is the most accurate path.
    if text.lstrip().startswith("{"):
        try:
            data = json.loads(text)
            if isinstance(data, dict) and any(
                k in data for k in ("skills", "experiences", "basics", "education")
            ):
                return normalize_structured(data)
        except (ValueError, TypeError):
            pass

    lines = [ln.rstrip() for ln in text.splitlines()]
    sections: dict[str, list[str]] = {}
    preamble: list[str] = []
    current = "preamble"
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        cleaned = _HEADER_RE.sub("", line).strip().strip("：: ").strip()
        matched = None
        if len(cleaned) <= 24 and not _BULLET_RE.match(line):
            low = cleaned.lower()
            for name, keys in SECTION_RULES:
                if any(low == k or (len(k) <= 8 and low.startswith(k)) for k in keys):
                    matched = name
                    break
        if matched:
            current = matched
            sections.setdefault(current, [])
        else:
            if current == "preamble":
                preamble.append(line)
            else:
                sections.setdefault(current, []).append(line)

    out = empty_structured()
    full = text
    email = re.search(r"[\w.+-]+@[\w.-]+\.\w+", full)
    phone = re.search(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)|(?<!\d)\d{3,4}-\d{7,8}(?!\d)", full)
    if email:
        out["basics"]["email"] = email.group(0)
    if phone:
        out["basics"]["phone"] = phone.group(0)
    urls = re.findall(r"https?://[^\s,，;；|｜]+", full)
    out["basics"]["links"] = urls[:5]
    for line in preamble[:3]:
        candidate = _BULLET_RE.sub("", line).strip()
        if (candidate and not re.search(r"@|https?://|\d{3,4}-|1[3-9]\d{9}", candidate)
                and len(candidate) <= 20
                and not any(k in candidate.lower() for k in ("简历", "resume", "curriculum"))):
            out["basics"]["name"] = candidate
            break
    for key in ("上海", "北京", "深圳", "广州", "杭州", "成都", "南京", "武汉", "苏州"):
        if key in full[:400]:
            out["basics"]["location"] = key
            break

    # summary
    out["summary"] = " ".join(_BULLET_RE.sub("", x).strip() for x in sections.get("summary", [])).strip()

    # skills: split on common separators, keep the original wording
    skill_items: list[str] = []
    for line in sections.get("skills", []):
        body = _BULLET_RE.sub("", line).strip()
        body = re.sub(r"^[^\s:：]{1,8}[:：]\s*", "", body)
        for part in _SPLIT_RE.split(body):
            part = part.strip(" ·-•\t")
            if part and len(part) <= 40:
                skill_items.append(part)
    seen: set[str] = set()
    out["skills"] = [x for x in skill_items if not (x.lower() in seen or seen.add(x.lower()))]

    out["experiences"] = _parse_entries(sections.get("experience", []), "experience")
    out["projects"] = _parse_entries(sections.get("projects", []), "projects")
    out["education"] = _parse_entries(sections.get("education", []), "education")
    out["certifications"] = [_BULLET_RE.sub("", x).strip() for x in sections.get("certifications", [])]
    out["languages"] = [_BULLET_RE.sub("", x).strip() for x in sections.get("languages", [])]
    for sec in ("basics", "extras"):
        leftover = [_BULLET_RE.sub("", x).strip() for x in sections.get(sec, [])]
        leftover = [x for x in leftover if x]
        if sec == "extras" and leftover:
            out["extras"].append({"heading": "其他", "lines": leftover})
    return out


def _parse_entries(lines: list[str], kind: str) -> list[dict]:
    entries: list[dict] = []
    current: dict | None = None
    for raw in lines:
        line = raw.strip()
        is_bullet = bool(_BULLET_RE.match(line))
        body = _BULLET_RE.sub("", line).strip()
        if not body:
            continue
        if is_bullet:
            if current is None:
                current = {"_header": "", "bullets": []}
                entries.append(current)
            current["bullets"].append(body)
            continue
        # A new header line starts a new entry
        dates = _DATE_RE.findall(line) or _DATE_SIMPLE_RE.findall(line)
        rest = line
        for d in dates:
            rest = rest.replace(d, " ")
        rest = re.sub(r"\s{2,}", "|", rest.strip())
        parts = [p.strip() for p in re.split(r"[|｜·]+", rest) if p.strip()]
        if len(parts) == 1 and re.search(r"\s+", parts[0]):
            parts = [p.strip() for p in re.split(r"\s+", parts[0], maxsplit=1) if p.strip()]
        current = {"_header": line, "bullets": []}
        entries.append(current)
        if kind == "experience":
            current["company"] = parts[0] if parts else ""
            current["title"] = parts[1] if len(parts) > 1 else ""
            current["location"] = parts[2] if len(parts) > 2 else ""
            ds = _split_dates(dates)
            current["start"], current["end"] = ds
        elif kind == "projects":
            current["name"] = parts[0] if parts else ""
            current["role"] = parts[1] if len(parts) > 1 else ""
            current["period"] = dates[0] if dates else ""
            current["description"] = ""
        else:
            current["school"] = parts[0] if parts else ""
            current["degree"] = parts[1] if len(parts) > 1 else ""
            current["major"] = parts[2] if len(parts) > 2 else ""
            ds = _split_dates(dates)
            current["start"], current["end"] = ds
    result: list[dict] = []
    for e in entries:
        e.pop("_header", None)
        if kind == "experience":
            result.append({
                "company": e.get("company", ""), "title": e.get("title", ""),
                "start": e.get("start", ""), "end": e.get("end", ""),
                "location": e.get("location", ""), "bullets": e.get("bullets", []),
            })
        elif kind == "projects":
            result.append({
                "name": e.get("name", ""), "role": e.get("role", ""),
                "period": e.get("period", ""),
                "description": e.get("description", ""), "bullets": e.get("bullets", []),
            })
        else:
            result.append({
                "school": e.get("school", ""), "degree": e.get("degree", ""),
                "major": e.get("major", ""), "start": e.get("start", ""),
                "end": e.get("end", ""),
            })
    return result


def _split_dates(dates: list[str]) -> tuple[str, str]:
    if not dates:
        return "", ""
    blob = dates[0]
    bits = re.split(r"[-~–—至到]+", blob)
    if len(bits) >= 2:
        return bits[0].strip(), bits[1].strip()
    return blob.strip(), ""


# ------------------------------------------------------- structure -> text
def structured_to_text(s: dict) -> str:
    """Deterministic plain-text rendering. This is the canonical original."""
    s = normalize_structured(s)
    b = s["basics"]
    lines: list[str] = []
    if b["name"]:
        lines.append(b["name"])
    contact = " | ".join(x for x in (
        b["headline"], b["location"], b["phone"], b["email"], *b["links"]
    ) if x)
    if contact:
        lines.append(contact)
    lines.append("")
    if s["summary"]:
        lines += ["个人简介", s["summary"], ""]
    if s["skills"]:
        lines += ["核心技能", "、".join(s["skills"]), ""]
    if s["experiences"]:
        lines.append("工作经历")
        for e in s["experiences"]:
            head = " | ".join(x for x in (e["company"], e["title"],
                                          _period(e["start"], e["end"]),
                                          e["location"]) if x)
            if head:
                lines.append(head)
            for bullet in e["bullets"]:
                lines.append("- " + bullet)
        lines.append("")
    if s["projects"]:
        lines.append("项目经历")
        for p in s["projects"]:
            head = " | ".join(x for x in (p["name"], p["role"], p["period"]) if x)
            if head:
                lines.append(head)
            if p["description"]:
                lines.append(p["description"])
            for bullet in p["bullets"]:
                lines.append("- " + bullet)
        lines.append("")
    if s["education"]:
        lines.append("教育背景")
        for e in s["education"]:
            lines.append(" | ".join(x for x in (
                e["school"], e["degree"], e["major"], _period(e["start"], e["end"])
            ) if x))
        lines.append("")
    if s["certifications"]:
        lines += ["证书与认证", "、".join(s["certifications"]), ""]
    if s["languages"]:
        lines += ["语言能力", "、".join(s["languages"]), ""]
    for extra in s["extras"]:
        if extra["lines"]:
            lines += [extra["heading"] or "其他"] + extra["lines"] + [""]
    return "\n".join(lines).strip() + "\n"


def _period(start: str, end: str) -> str:
    if start and end:
        return f"{start} - {end}"
    return start or end or ""


def resume_plain_text(s: dict) -> str:
    """All facts as one searchable blob (no headers duplicated)."""
    s = normalize_structured(s)
    parts: list[str] = [s["summary"], " ".join(s["skills"])]
    for e in s["experiences"]:
        parts += [e["company"], e["title"], e["location"], " ".join(e["bullets"])]
    for p in s["projects"]:
        parts += [p["name"], p["role"], p["description"], " ".join(p["bullets"])]
    for e in s["education"]:
        parts += [e["school"], e["degree"], e["major"]]
    parts += s["certifications"] + s["languages"]
    for x in s["extras"]:
        parts += x["lines"]
    parts += [s["basics"]["headline"], s["basics"]["location"]]
    return " ".join(p for p in parts if p)


def canonical_skills(s: dict) -> list[str]:
    """Canonical skill names grounded in the resume's real content."""
    blob = resume_plain_text(s)
    found = sk.extract_skills(blob)
    # Declared skills not in the dictionary are still real facts: keep their
    # literal wording as a canonical token so they can be matched by name.
    for declared in s.get("skills", []):
        d = str(declared).strip()
        if not d:
            continue
        mapped = sk.extract_skills(d)
        for m in mapped:
            if m not in found:
                found.append(m)
        if not mapped and len(d) <= 24 and d not in found:
            found.append(d)
    return found


# ------------------------------------------------------------ experience years
def _parse_ym(value: str) -> tuple[int, int] | None:
    v = str(value or "").strip().lower()
    if not v:
        return None
    m = re.search(r"((?:19|20)\d{2})\s*[.\-/年]\s*(\d{1,2})", v)
    if m:
        return int(m.group(1)), min(12, max(1, int(m.group(2))))
    m = re.search(r"((?:19|20)\d{2})", v)
    if m:
        return int(m.group(1)), 1
    return None


def years_of_experience(s: dict) -> float:
    s = normalize_structured(s)
    today = datetime.now()
    cur = today.year * 12 + today.month
    spans: list[tuple[int, int]] = []
    for e in s["experiences"]:
        title = str(e["title"] or "").lower()
        if "实习" in title or "intern" in title:
            continue  # internships are not counted as full-time experience
        start = _parse_ym(e["start"])
        if not start:
            continue
        a = start[0] * 12 + start[1]
        end_raw = str(e["end"] or "").strip().lower()
        if end_raw and not any(k in end_raw for k in ("至今", "现在", "present", "now", "current", "今")):
            end = _parse_ym(end_raw)
            b = (end[0] * 12 + end[1]) if end else a
        else:
            b = cur
        if b < a:
            a, b = b, a
        spans.append((a, max(a + 1, b)))
    if not spans:
        return 0.0
    spans.sort()
    merged: list[list[int]] = [list(spans[0])]
    for a, b in spans[1:]:
        if a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    months = sum(b - a for a, b in merged)
    return round(months / 12, 1)
