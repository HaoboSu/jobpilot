"""PDF text extraction using only the Python standard library.

Covers the formats produced by Word / WPS / LaTeX / browsers when exporting a
resume: classic and xref-stream PDFs, object streams, Flate/LZW/ASCII85/
ASCIIHex/RunLength filters, PNG predictors, page trees, text operators
(Tj / TJ / ' / "), and Type0 / TrueType / Type1 fonts with ToUnicode CMaps
(which is how CJK text is stored). Image-only scans have no text layer and are
reported honestly instead of returning garbage.

If a full-featured library (pypdf / PyMuPDF / pdfminer) is installed it is used
first; this module is the always-available fallback that keeps the app
zero-dependency and portable.
"""
from __future__ import annotations

import base64
import importlib
import importlib.util
import re
import subprocess
import sys
import zlib
from typing import Any

_WS = b"\x00\t\n\x0c\r "
_DELIM = b"()<>[]{}/%"
_OBJ_RE = re.compile(rb"(?<![0-9])(\d+)\s+(\d+)\s+obj\b")
_HEX_CLEAN = re.compile(rb"\s+")


class PDFError(Exception):
    """Raised when a PDF cannot be read or has no extractable text layer."""


class Name(str):
    """PDF name object (/F1, /Type, ...)."""


class Keyword(str):
    """PDF operator or bare keyword (Tj, BT, ...)."""


class Ref:
    __slots__ = ("num", "gen")

    def __init__(self, num: int, gen: int) -> None:
        self.num, self.gen = num, gen

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Ref({self.num},{self.gen})"


# ------------------------------------------------------------------- lexing
class Lexer:
    def __init__(self, data: bytes, pos: int = 0) -> None:
        self.b, self.i, self.n = data, pos, len(data)

    def _skip(self) -> None:
        while self.i < self.n:
            c = self.b[self.i]
            if c in _WS:
                self.i += 1
            elif c == 0x25:  # '%' comment
                while self.i < self.n and self.b[self.i] not in b"\r\n":
                    self.i += 1
            else:
                break

    def next(self) -> Any:
        self._skip()
        if self.i >= self.n:
            return None
        c = self.b[self.i]

        if c == 0x2F:  # /Name
            self.i += 1
            start = self.i
            while self.i < self.n and self.b[self.i] not in _WS + _DELIM:
                self.i += 1
            raw = self.b[start:self.i]
            return Name(_unescape_name(raw))

        if c == 0x28:  # (literal string)
            self.i += 1
            depth, out = 1, bytearray()
            while self.i < self.n and depth:
                ch = self.b[self.i]
                self.i += 1
                if ch == 0x5C:  # backslash escape
                    if self.i >= self.n:
                        break
                    e = self.b[self.i]
                    self.i += 1
                    mapping = {0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12,
                               0x28: 40, 0x29: 41, 0x5C: 92}
                    if e in mapping:
                        out.append(mapping[e])
                    elif 0x30 <= e <= 0x37:  # octal
                        digits = bytearray([e])
                        for _ in range(2):
                            if self.i < self.n and 0x30 <= self.b[self.i] <= 0x37:
                                digits.append(self.b[self.i])
                                self.i += 1
                        out.append(int(bytes(digits), 8) & 0xFF)
                    elif e in (10, 13):
                        if e == 13 and self.i < self.n and self.b[self.i] == 10:
                            self.i += 1
                    else:
                        out.append(e)
                elif ch == 0x28:
                    depth += 1
                    out.append(ch)
                elif ch == 0x29:
                    depth -= 1
                    if depth:
                        out.append(ch)
                else:
                    out.append(ch)
            return bytes(out)

        if c == 0x3C:  # <hex> or <<dict>>
            if self.b[self.i:self.i + 2] == b"<<":
                self.i += 2
                return "<<"
            self.i += 1
            start = self.i
            while self.i < self.n and self.b[self.i] != 0x3E:
                self.i += 1
            raw = _HEX_CLEAN.sub(b"", self.b[start:self.i])
            self.i = min(self.n, self.i + 1)
            if len(raw) % 2:
                raw += b"0"
            try:
                return bytes.fromhex(raw.decode("ascii"))
            except ValueError:
                return b""

        if self.b[self.i:self.i + 2] == b">>":
            self.i += 2
            return ">>"
        if c in (0x5B, 0x5D):  # [ ]
            self.i += 1
            return chr(c)

        start = self.i
        while self.i < self.n and self.b[self.i] not in _WS + _DELIM:
            self.i += 1
        token = self.b[start:self.i] or self.b[self.i - 1:self.i]
        if self.i == start:
            self.i += 1
        try:
            text = token.decode("latin-1")
        except UnicodeDecodeError:  # pragma: no cover
            return Keyword("")
        if re.fullmatch(r"[+-]?\d+", text):
            return int(text)
        if re.fullmatch(r"[+-]?(?:\d*\.\d+|\d+\.)", text):
            return float(text)
        if text == "true":
            return True
        if text == "false":
            return False
        if text == "null":
            return None
        return Keyword(text)

    def value(self) -> Any:
        """Parse one PDF object, resolving `N G R` indirect references."""
        first = self.next()
        if isinstance(first, int) and not isinstance(first, bool) and first >= 0:
            save = self.i
            second = self.next()
            if isinstance(second, int) and not isinstance(second, bool) and second >= 0:
                third = self.next()
                if isinstance(third, Keyword) and str(third) == "R":
                    return Ref(first, second)
            self.i = save
        if first == "<<":
            out: dict[str, Any] = {}
            while True:
                key = self.next()
                if key is None or key == ">>":
                    break
                if isinstance(key, Name):
                    out[str(key)] = self.value()
                elif key == "[":
                    self.i -= 1
                    self.value()
            return out
        if first == "[":
            items: list[Any] = []
            while True:
                save = self.i
                nxt = self.next()
                if nxt is None or nxt == "]":
                    break
                self.i = save
                items.append(self.value())
            return items
        return first


def _unescape_name(raw: bytes) -> str:
    out = bytearray()
    i = 0
    while i < len(raw):
        if raw[i] == 0x23 and i + 2 < len(raw):  # #xx
            try:
                out.append(int(raw[i + 1:i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        out.append(raw[i])
        i += 1
    return bytes(out).decode("latin-1")


# ------------------------------------------------------------ stream filters
def _flate(data: bytes) -> bytes:
    try:
        return zlib.decompress(data)
    except zlib.error:
        pass
    for trim in (1, 2, 3, 4):
        try:
            return zlib.decompressobj().decompress(data[:-trim] if trim else data)
        except zlib.error:
            continue
    try:
        return zlib.decompress(data, -15)
    except zlib.error:
        raise PDFError("PDF 内容流使用了无法解压的压缩数据")


def _lzw(data: bytes) -> bytes:
    bitbuf = int.from_bytes(data, "big")
    total = len(data) * 8
    pos = 0
    table: list[bytes] = [bytes([i]) for i in range(256)] + [b"", b""]
    width, prev, out = 9, None, bytearray()
    while pos + width <= total:
        shift = total - pos - width
        code = (bitbuf >> shift) & ((1 << width) - 1)
        pos += width
        if code == 256:
            table = [bytes([i]) for i in range(256)] + [b"", b""]
            width, prev = 9, None
            continue
        if code == 257:
            break
        if prev is None:
            entry = table[code] if code < len(table) else b""
        else:
            if code < len(table):
                entry = table[code]
            elif code == len(table):
                entry = prev + prev[:1]
            else:
                break
            table.append(prev + entry[:1])
            if len(table) >= (1 << width) and width < 12:
                width += 1
        out += entry
        prev = entry
    return bytes(out)


def _ascii85(data: bytes) -> bytes:
    cleaned = re.sub(rb"\s+", b"", data)
    if cleaned.endswith(b"~>"):
        cleaned = cleaned[:-2]
    if cleaned.startswith(b"<~"):
        cleaned = cleaned[2:]
    if cleaned.endswith(b"z"):
        cleaned = cleaned[:-1] + b"!!!!!"
    try:
        return base64.a85decode(cleaned, adobe=False)
    except (ValueError, TypeError):
        return b""


def _asciihex(data: bytes) -> bytes:
    raw = _HEX_CLEAN.sub(b"", data.split(b">")[0])
    if len(raw) % 2:
        raw += b"0"
    try:
        return bytes.fromhex(raw.decode("ascii"))
    except ValueError:
        return b""


def _runlength(data: bytes) -> bytes:
    out, i = bytearray(), 0
    while i < len(data):
        n = data[i]
        i += 1
        if n == 128:
            break
        if n < 128:
            out += data[i:i + n + 1]
            i += n + 1
        elif i < len(data):
            out += bytes([data[i]]) * (257 - n)
            i += 1
    return bytes(out)


def _predictor(data: bytes, params: dict) -> bytes:
    try:
        predictor = int(params.get("Predictor", 1))
        colors = int(params.get("Colors", 1))
        bpc = int(params.get("BitsPerComponent", 8))
        columns = int(params.get("Columns", 1))
    except (TypeError, ValueError):
        return data
    if predictor <= 1:
        return data
    row_len = (colors * bpc * columns + 7) // 8
    bpp = max(1, (colors * bpc + 7) // 8)
    out, prev, i = bytearray(), bytearray(row_len), 0
    while i + row_len + 1 <= len(data):
        ft = data[i]
        row = bytearray(data[i + 1:i + 1 + row_len])
        i += row_len + 1
        for x in range(row_len):
            a = row[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if ft == 1:
                row[x] = (row[x] + a) & 0xFF
            elif ft == 2:
                row[x] = (row[x] + b) & 0xFF
            elif ft == 3:
                row[x] = (row[x] + ((a + b) >> 1)) & 0xFF
            elif ft == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                row[x] = (row[x] + pred) & 0xFF
        out += row
        prev = row
    return bytes(out)


# -------------------------------------------------------------- object model
def _resolve(value: Any, objs: dict, depth: int = 0) -> Any:
    if isinstance(value, Ref) and depth < 12:
        obj = objs.get(value.num)
        return _resolve(obj.get("value"), objs, depth + 1) if obj else None
    if isinstance(value, list):
        return [_resolve(v, objs, depth + 1) for v in value]
    return value


def _obj(objs: dict, value: Any) -> dict | None:
    if isinstance(value, Ref):
        return objs.get(value.num)
    if isinstance(value, dict):
        return {"dict": value, "value": value, "stream": None}
    return None


def _parse_objects(data: bytes) -> dict:
    matches = list(_OBJ_RE.finditer(data))
    parts: dict[int, tuple[bytes, int | None, bytes]] = {}
    for idx, m in enumerate(matches):
        num = int(m.group(1))
        start = m.end()
        limit = matches[idx + 1].start() if idx + 1 < len(matches) else len(data)
        segment = data[start:limit]
        end = segment.rfind(b"endobj")
        body = segment[:end] if end >= 0 else segment
        si = body.find(b"stream")
        if si >= 0:
            head = body[:si]
            p = si + 6
            if body[p:p + 2] == b"\r\n":
                p += 2
            elif body[p:p + 1] in (b"\r", b"\n"):
                p += 1
            parts[num] = (head, p, body)
        else:
            parts[num] = (body, None, body)

    objs: dict[int, dict] = {}
    for num, (head, _, _) in parts.items():
        value = Lexer(head).value()
        objs[num] = {
            "dict": value if isinstance(value, dict) else {},
            "value": value,
            "stream": None,
        }

    for num, (head, stream_pos, body) in parts.items():
        if stream_pos is None:
            continue
        length = _resolve(objs[num]["dict"].get("Length"), objs)
        if isinstance(length, (int, float)) and 0 <= int(length) <= len(body) - stream_pos:
            raw = body[stream_pos:stream_pos + int(length)]
        else:
            stop = body.find(b"endstream", stream_pos)
            raw = body[stream_pos:stop if stop >= 0 else len(body)]
            raw = raw.rstrip(b"\r\n")
        filters = _resolve(objs[num]["dict"].get("Filter"), objs)
        parms = _resolve(objs[num]["dict"].get("DecodeParms")
                         or objs[num]["dict"].get("DP"), objs)
        if not filters:
            objs[num]["stream"] = raw
            continue
        if isinstance(filters, (Name, str)):
            filters = [filters]
        if not isinstance(filters, list):
            filters = []
        if not isinstance(parms, list):
            parms = [parms] * len(filters)
        decoded = raw
        for i, f in enumerate(filters):
            name = str(f)
            param = parms[i] if i < len(parms) and isinstance(parms[i], dict) else {}
            if name in ("FlateDecode", "Fl"):
                decoded = _flate(decoded)
            elif name in ("LZWDecode", "LZW"):
                decoded = _lzw(decoded)
            elif name in ("ASCII85Decode", "A85"):
                decoded = _ascii85(decoded)
            elif name in ("ASCIIHexDecode", "AHx"):
                decoded = _asciihex(decoded)
            elif name in ("RunLengthDecode", "RL"):
                decoded = _runlength(decoded)
            elif name in ("DCTDecode", "JPXDecode", "CCITTFaxDecode", "JBIG2Decode"):
                decoded = b""  # image stream, no text
                break
            if param.get("Predictor"):
                decoded = _predictor(decoded, param)
        objs[num]["stream"] = decoded
    return objs


def _expand_object_streams(objs: dict) -> None:
    for num in sorted(list(objs)):
        obj = objs[num]
        if str(obj["dict"].get("Type")) != "ObjStm" or not obj.get("stream"):
            continue
        data = obj["stream"]
        try:
            n = int(_resolve(obj["dict"].get("N"), objs) or 0)
            first = int(_resolve(obj["dict"].get("First"), objs) or 0)
        except (TypeError, ValueError):
            continue
        header = data[:first].split()
        pairs = []
        for i in range(n):
            if 2 * i + 1 >= len(header):
                break
            try:
                pairs.append((int(header[2 * i]), int(header[2 * i + 1])))
            except ValueError:
                break
        for i, (child_num, offset) in enumerate(pairs):
            seg_start = first + offset
            seg_end = first + pairs[i + 1][1] if i + 1 < len(pairs) else len(data)
            value = Lexer(data[seg_start:seg_end]).value()
            objs[child_num] = {
                "dict": value if isinstance(value, dict) else {},
                "value": value,
                "stream": None,
            }


def _pages(objs: dict) -> list[dict]:
    catalog = next((o for o in objs.values()
                    if str(o["dict"].get("Type")) == "Catalog"), None)
    start = catalog["dict"].get("Pages") if catalog else None  # keep the Ref
    pages: list[dict] = []
    seen: set[int] = set()

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 40:
            return
        key = node.num if isinstance(node, Ref) else id(node)
        if key in seen:
            return
        seen.add(key)
        obj = _obj(objs, node)
        if not obj:
            return
        d = obj["dict"]
        if str(d.get("Type")) == "Page" or ("Contents" in d and "Kids" not in d):
            pages.append(obj)
            return
        kids = d.get("Kids")
        if isinstance(kids, Ref):  # Kids array stored as an indirect object
            holder = objs.get(kids.num)
            kids = holder["value"] if holder else None
        if isinstance(kids, list):
            for kid in kids:
                walk(kid, depth + 1)

    if start is not None:
        walk(start)
    if not pages:
        for num in sorted(objs):
            if str(objs[num]["dict"].get("Type")) == "Page":
                pages.append(objs[num])
    return pages


# ------------------------------------------------------------------- CMaps
def _utf16(raw: bytes) -> str:
    if not raw:
        return ""
    if len(raw) == 1:
        return chr(raw[0])
    try:
        return raw.decode("utf-16-be")
    except UnicodeDecodeError:
        return ""


def _hex_bytes(raw: bytes) -> bytes:
    cleaned = _HEX_CLEAN.sub(b"", raw)
    if len(cleaned) % 2:
        cleaned += b"0"
    try:
        return bytes.fromhex(cleaned.decode("ascii"))
    except ValueError:
        return b""


def _parse_cmap(data: bytes) -> tuple[dict[int, str], int]:
    text = data or b""
    cmap: dict[int, str] = {}
    lengths: list[int] = []
    for block in re.findall(rb"beginbfchar(.*?)endbfchar", text, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f\s]+)>\s*<([0-9A-Fa-f\s]+)>", block):
            s, d = _hex_bytes(src), _hex_bytes(dst)
            if s:
                cmap[int.from_bytes(s, "big")] = _utf16(d)
                lengths.append(len(s))
    for block in re.findall(rb"beginbfrange(.*?)endbfrange", text, re.S):
        for lo, hi, dst in re.findall(
            rb"<([0-9A-Fa-f\s]+)>\s*<([0-9A-Fa-f\s]+)>\s*<([0-9A-Fa-f\s]+)>", block
        ):
            a, b, base = _hex_bytes(lo), _hex_bytes(hi), _hex_bytes(dst)
            if not a or not b:
                continue
            start, end = int.from_bytes(a, "big"), int.from_bytes(b, "big")
            if end - start > 65535:
                continue
            prefix = base[:-2] if len(base) > 2 else b""
            tail = int.from_bytes(base[-2:], "big") if len(base) >= 2 else 0
            for k in range(start, end + 1):
                value = min(0xFFFF, tail + k - start)
                if len(base) >= 4 and prefix:
                    cmap[k] = _utf16(prefix + value.to_bytes(2, "big"))
                else:
                    cmap[k] = _utf16(value.to_bytes(2, "big"))
            lengths.append(len(a))
        for lo, hi, arr in re.findall(
            rb"<([0-9A-Fa-f\s]+)>\s*<([0-9A-Fa-f\s]+)>\s*\[(.*?)\]", block, re.S
        ):
            a, b = _hex_bytes(lo), _hex_bytes(hi)
            if not a:
                continue
            start = int.from_bytes(a, "big")
            for i, item in enumerate(re.findall(rb"<([0-9A-Fa-f\s]+)>", arr)):
                cmap[start + i] = _utf16(_hex_bytes(item))
            lengths.append(len(a))
    for block in re.findall(rb"begincodespacerange(.*?)endcodespacerange", text, re.S):
        for src, _ in re.findall(rb"<([0-9A-Fa-f\s]+)>\s*<([0-9A-Fa-f\s]+)>", block):
            s = _hex_bytes(src)
            if s:
                lengths.append(len(s))
    return cmap, max(lengths) if lengths else 2


def _fonts(page: dict, objs: dict) -> dict[str, dict]:
    resources = _resolve(page.get("Resources"), objs)
    if not isinstance(resources, dict):
        resources = {}
    entries = _resolve(resources.get("Font"), objs)
    if not isinstance(entries, dict):
        entries = {}
    out: dict[str, dict] = {}
    for key, ref in entries.items():
        obj = _obj(objs, ref)
        if not obj:
            continue
        d = obj["dict"]
        cmap: dict[int, str] = {}
        # ToUnicode points at a stream object: dereference it, do NOT resolve
        # it to a dictionary, or the decoded stream bytes would be lost.
        tu = _obj(objs, d.get("ToUnicode"))
        default_len = 2 if str(d.get("Subtype")) == "Type0" else 1
        if tu and tu.get("stream"):
            cmap, cmap_len = _parse_cmap(tu["stream"])
            code_len = cmap_len or default_len
        else:
            code_len = default_len
        out[str(key)] = {"cmap": cmap, "code_len": max(1, min(4, code_len))}
    return out


# ------------------------------------------------------------ text decoding
_PDFDOC_HIGH = {
    0x80: "\u20ac", 0x82: "\u201a", 0x83: "\u0192", 0x84: "\u201e",
    0x85: "\u2026", 0x86: "\u2020", 0x87: "\u2021", 0x88: "\u02c6",
    0x89: "\u2030", 0x8A: "\u0160", 0x8B: "\u2039", 0x8C: "\u0152",
    0x8E: "\u017d", 0x91: "\u2018", 0x92: "\u2019", 0x93: "\u201c",
    0x94: "\u201d", 0x95: "\u2022", 0x96: "\u2013", 0x97: "\u2014",
    0x98: "\u02dc", 0x99: "\u2122", 0x9A: "\u0161", 0x9B: "\u203a",
    0x9C: "\u0153", 0x9E: "\u017e", 0x9F: "\u0178",
}


def _decode_simple(raw: bytes) -> str:
    weird = sum(1 for x in raw if 0x80 <= x < 0xA0)
    if weird >= 3:
        try:
            return raw.decode("gb18030")
        except UnicodeDecodeError:
            pass
    return "".join(_PDFDOC_HIGH.get(x, chr(x)) for x in raw)


def _decode_string(raw: bytes, font: dict | None) -> str:
    font = font or {"cmap": {}, "code_len": 1}
    cmap: dict[int, str] = font["cmap"]
    n: int = font["code_len"]
    if cmap:
        out, i = [], 0
        while i < len(raw):
            chunk = raw[i:i + n]
            code = int.from_bytes(chunk, "big") if chunk else 0
            if code in cmap:
                out.append(cmap[code])
                i += n
                continue
            one = raw[i]
            if one in cmap:
                out.append(cmap[one])
                i += 1
                continue
            if n >= 2 and len(chunk) == 2:
                val = int.from_bytes(chunk, "big")
                if 0x20 <= val <= 0x7E or 0x4E00 <= val <= 0x9FFF or val > 0x2FFF:
                    out.append(chr(val))
                else:
                    try:
                        out.append(chunk.decode("gb18030"))
                    except UnicodeDecodeError:
                        out.append("")
            else:
                out.append(_decode_simple(bytes([one])))
            i += n
        return "".join(out)
    if n >= 2:
        out, i = [], 0
        while i + 1 < len(raw):
            val = int.from_bytes(raw[i:i + 2], "big")
            out.append(chr(val) if 0x20 <= val < 0xFFFD else "")
            i += 2
        text = "".join(out)
        if text.strip():
            return text
        try:
            return raw.decode("gb18030")
        except UnicodeDecodeError:
            return ""
    return _decode_simple(raw)


def _content_text(buf: bytes, fonts: dict[str, dict]) -> str:
    lx = Lexer(buf)
    operands: list[Any] = []
    rows: list[list] = []
    cur_y, last_y, leading = 0.0, None, 0.0
    cur_font: dict | None = None

    def emit(text: str) -> None:
        nonlocal last_y
        if not text:
            return
        if last_y is None or abs(cur_y - last_y) > 1.2:
            rows.append([cur_y, text])
            last_y = cur_y
        else:
            rows[-1][1] += text

    while True:
        token = lx.next()
        if token is None:
            break
        if not isinstance(token, Keyword):
            operands.append(token)
            continue
        op = str(token)
        try:
            if op == "Tf":
                for item in reversed(operands):
                    if isinstance(item, Name):
                        cur_font = fonts.get(str(item))
                        break
            elif op in ("Td", "TD", "Tm"):
                nums = [x for x in operands if isinstance(x, (int, float))
                        and not isinstance(x, bool)]
                if op == "Tm" and len(nums) >= 6:
                    cur_y = float(nums[-1])
                elif len(nums) >= 2:
                    cur_y += float(nums[-1])
                    if op == "TD":
                        leading = -float(nums[-1])
            elif op == "TL":
                nums = [x for x in operands if isinstance(x, (int, float))
                        and not isinstance(x, bool)]
                if nums:
                    leading = float(nums[-1])
            elif op == "T*" or op == "'":
                cur_y -= leading or 12.0
                if op == "'":
                    for item in reversed(operands):
                        if isinstance(item, bytes):
                            emit(_decode_string(item, cur_font))
                            break
            elif op == '"':
                cur_y -= leading or 12.0
                for item in reversed(operands):
                    if isinstance(item, bytes):
                        emit(_decode_string(item, cur_font))
                        break
            elif op == "Tj":
                for item in reversed(operands):
                    if isinstance(item, bytes):
                        emit(_decode_string(item, cur_font))
                        break
            elif op == "TJ":
                for item in operands:
                    if isinstance(item, list):
                        for part in item:
                            if isinstance(part, bytes):
                                emit(_decode_string(part, cur_font))
                            elif isinstance(part, (int, float)) and part < -120:
                                emit(" ")
                        break
        except (ValueError, TypeError, IndexError):
            pass
        operands = []

    lines = []
    for _, text in rows:
        clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text).rstrip()
        if clean.strip():
            lines.append(clean)
    return "\n".join(lines)


def _contents(page: dict, objs: dict) -> bytes:
    value = page.get("Contents")
    items: list[Any]
    if isinstance(value, Ref):
        holder = objs.get(value.num)
        raw = holder["value"] if holder else None
        items = raw if isinstance(raw, list) else [value]
    elif isinstance(value, list):
        items = value
    else:
        items = [value]
    chunks = []
    for item in items:
        obj = _obj(objs, item)
        if obj and obj.get("stream"):
            chunks.append(obj["stream"])
    return b"\n".join(chunks)


# ------------------------------------------------------------------ public
def extract_text(data: bytes) -> str:
    """Extract a plain-text representation of a PDF. Stdlib only."""
    if not data or b"%PDF" not in data[:1024]:
        raise PDFError("文件不是有效的 PDF（缺少 %PDF 文件头）")
    objs = _parse_objects(data)
    _expand_object_streams(objs)
    pages = _pages(objs)
    if not pages:
        raise PDFError("未能定位 PDF 页面结构，文件可能已损坏或经过加密")
    chunks = []
    for page in pages:
        buf = _contents(page["dict"], objs)
        if buf:
            text = _content_text(buf, _fonts(page["dict"], objs))
            if text.strip():
                chunks.append(text)
    result = "\n\n".join(chunks).strip()
    # Count letters/digits/CJK characters only - short Chinese resumes are
    # perfectly valid, while control-character garbage is not text.
    meaningful = len(re.findall(r"[\w\u4e00-\u9fff]", result))
    if meaningful < 3:
        raise PDFError(
            "该 PDF 没有可提取的文字层（很可能是扫描图片或纯图片简历）。"
            "请在原编辑器中另存为 .docx / .txt，或直接粘贴简历文字；图片型 PDF 需要 OCR 才能识别。"
        )
    return result


def available_engines() -> dict:
    """Report which PDF backends are available in this Python environment."""
    importlib.invalidate_caches()
    enhanced = []
    for name in ("pypdf", "PyPDF2", "fitz", "pdfminer"):
        if importlib.util.find_spec(name) is not None:
            enhanced.append(name)
    engine = enhanced[0] if enhanced else "builtin"
    return {
        "builtin": True,
        "enhanced": enhanced,
        "engine": engine,
        "message": ("已启用增强解析：{}；复杂排版与嵌入字体的兼容性更好".format(engine)
                    if enhanced else
                    "已启用内置 PDF 解析（零依赖）；可一键安装 pypdf 增强兼容性"),
    }


def install_enhancement() -> dict:
    """Install pypdf on explicit user action; never called automatically."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "pypdf",
             "--disable-pip-version-check", "-q"],
            capture_output=True, text=True, timeout=150,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "message": f"安装未能完成：{exc}. 可手动执行 pip install pypdf"}
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-300:]
        return {"ok": False,
                "message": "自动安装失败，可手动执行 pip install pypdf 后重启应用。" + detail}
    importlib.invalidate_caches()
    return {"ok": True, "message": "pypdf 安装成功，之后导入 PDF 将优先使用增强解析",
            "engines": available_engines()}
