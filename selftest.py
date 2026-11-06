"""End-to-end self check.

Run:  python selftest.py

It uses a throw-away SQLite database and an ephemeral local HTTP port, so it
never touches the data created by the real application.
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import tempfile
import threading
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer

# Point persistence at a temporary location BEFORE any module opens a connection.
_TMP = tempfile.mkdtemp(prefix="jobpilot-selftest-")
from app import db  # noqa: E402

db.DATA_DIR = _TMP
db.DB_PATH = os.path.join(_TMP, "selftest.db")

from app import docx_export, matcher, monitor, resume as rs, sources, writer  # noqa: E402
from app.seed import SAMPLE_RESUME  # noqa: E402
from app.server import Handler  # noqa: E402

PASS, FAIL = [], []

# ---------------------------------------------------------------- PDF fixtures
def _pdf_bytes(objects: list[tuple[int, bytes]], root: int = 1) -> bytes:
    """Build a small but structurally valid PDF with a real xref table."""
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for num, body in objects:
        offsets[num] = len(out)
        out += f"{num} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_pos = len(out)
    maxnum = max(offsets)
    out += f"xref\n0 {maxnum + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for i in range(1, maxnum + 1):
        out += f"{offsets.get(i, 0):010d} 00000 n \n".encode("ascii")
    out += (f"trailer\n<< /Size {maxnum + 1} /Root {root} 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n").encode("ascii")
    return bytes(out)


def _stream(payload: bytes, extra: bytes = b"", compress: bool = False) -> bytes:
    import zlib
    data = zlib.compress(payload) if compress else payload
    head = b"<< /Length " + str(len(data)).encode("ascii")
    if compress:
        head += b" /Filter /FlateDecode"
    if extra:
        head += b" " + extra
    return head + b" >>\nstream\n" + data + b"\nendstream"


def _make_simple_pdf() -> bytes:
    content = (b"BT\n/F1 12 Tf\n72 720 Td\n(Zhang Ming) Tj\n"
               b"0 -18 Td\n(Core Skills: Python, SQL, Tableau) Tj\n"
               b"0 -18 Td\n(Work Experience: Data Analyst) Tj\nET\n")
    return _pdf_bytes([
        (1, b"<< /Type /Catalog /Pages 2 0 R >>"),
        (2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
        (3, b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents [5 0 R] >>"),
        (4, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
            b"/Encoding /WinAnsiEncoding >>"),
        (5, _stream(content)),
    ])


def _make_cjk_pdf() -> bytes:
    """Type0 / Identity-H font with a ToUnicode CMap + Flate-compressed content."""
    content = (b"BT\n/F1 12 Tf\n72 740 Td\n<004100420043> Tj\n"
               b"0 -20 Td\n<00440045> Tj\nET\n")
    cmap = b"""/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def
/CMapName /Adobe-Identity-UCS def
/CMapType 2 def
1 begincodespacerange
<0000> <FFFF>
endcodespacerange
5 beginbfchar
<0041> <5F20>
<0042> <660E>
<0043> <6570>
<0044> <636E>
<0045> <5206>
endbfchar
endcmap
CMapName currentdict /CMap defineresource pop
end
end
"""
    return _pdf_bytes([
        (1, b"<< /Type /Catalog /Pages 2 0 R >>"),
        (2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
        (3, b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"),
        (4, _stream(content, compress=True)),
        (5, b"<< /Type /Font /Subtype /Type0 /BaseFont /NotoSansCJK "
            b"/Encoding /Identity-H /DescendantFonts [6 0 R] /ToUnicode 7 0 R >>"),
        (6, b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /NotoSansCJK "
            b"/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> >>"),
        (7, _stream(cmap)),
    ])


def _make_objstm_pdf() -> bytes:
    """PDF 1.5 style: catalog/pages/fonts live inside a compressed object stream."""
    import zlib
    content = (b"BT\n/F1 12 Tf\n72 740 Td\n<004100420043> Tj\n"
               b"0 -20 Td\n<00440045> Tj\nET\n")
    cmap = b"""begincmap
1 begincodespacerange
<0000> <FFFF>
endcodespacerange
5 beginbfchar
<0041> <5F20>
<0042> <660E>
<0043> <6570>
<0044> <636E>
<0045> <5206>
endbfchar
endcmap
"""
    packed = [
        (1, b"<< /Type /Catalog /Pages 2 0 R >>"),
        (2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
        (5, b"<< /Type /Font /Subtype /Type0 /BaseFont /NotoSansCJK "
            b"/Encoding /Identity-H /DescendantFonts [6 0 R] /ToUnicode 7 0 R >>"),
        (6, b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /NotoSansCJK >>"),
    ]
    cursor, labels = 0, []
    for num, body in packed:
        labels.append(f"{num} {cursor}")
        cursor += len(body) + 1
    header = (" ".join(labels) + "\n").encode("ascii")
    first = len(header)
    objstm_data = header + b" ".join(body for _, body in packed)
    objstm = (b"<< /Type /ObjStm /N " + str(len(packed)).encode("ascii")
              + b" /First " + str(first).encode("ascii")
              + b" /Filter /FlateDecode /Length "
              + str(len(zlib.compress(objstm_data))).encode("ascii")
              + b" >>\nstream\n" + zlib.compress(objstm_data) + b"\nendstream")
    return _pdf_bytes([
        (3, b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"),
        (4, _stream(content, compress=True)),
        (7, _stream(cmap)),
        (8, objstm),
    ], root=1)


def _image_only_pdf() -> bytes:
    """A page with no content stream - like a scanned/image-only resume."""
    return _pdf_bytes([
        (1, b"<< /Type /Catalog /Pages 2 0 R >>"),
        (2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
        (3, b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>"),
    ])


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    print(("  PASS  " if condition else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))
    if not condition and detail:
        print("         " + detail[:300])


def http(base: str, path: str, method: str = "GET", payload: dict | None = None):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        base + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        ctype = resp.headers.get("Content-Type", "")
    if "json" in ctype:
        return json.loads(raw), raw, ctype
    return None, raw, ctype


def main() -> int:
    # Redirected output on English Windows may otherwise default to cp1252.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    db.init_db()
    print("\n[1] 简历结构化与版本留存")
    structured = rs.normalize_structured(SAMPLE_RESUME)
    raw = rs.structured_to_text(structured)
    v1 = db.create_resume_version("示例简历", raw, structured, "sample", note="自检版本")
    years = rs.years_of_experience(structured)
    skills = rs.canonical_skills(structured)
    check("示例简历解析出技能与经历", len(skills) >= 15 and len(structured["experiences"]) == 3,
          f"skills={len(skills)}, years={years}")
    check("工作年限由真实日期核算", 5 <= years <= 8, f"years={years}")
    v2 = db.create_resume_version("更新版", raw + "\n（更新标记）", structured, "edit")
    old = db.get_resume_version(v1)
    check("旧版本原文不可变且可读取", old and "（更新标记）" not in old["raw_text"] and v2 > v1)

    docx_bytes = docx_export.build_docx(raw, "简历")
    text, fmt, _ = rs.decode_upload(
        "my_resume.docx", base64.b64encode(docx_bytes).decode("ascii"), None
    )
    check("DOCX 文件导入解析", fmt == "docx" and "核心技能" in text and "云栖零售" in text)

    free_form = """张明
000-00000000 | zhang@example.com | 上海

个人简介
多年数据分析经验。

核心技能
Python、SQL、Tableau、Excel

工作经历
某某科技 | 数据分析师 | 2022-03 - 至今
- 使用 SQL 完成日报取数
- 使用 Tableau 搭建看板

教育背景
某大学 | 本科 | 统计学 | 2018-09 - 2022-06
"""
    parsed = rs.parse_structured(free_form)
    check("自由格式简历启发式解析",
          len(parsed["skills"]) >= 3 and len(parsed["experiences"]) >= 1
          and parsed["basics"]["name"] == "张明" and bool(parsed["basics"]["phone"]),
          f"skills={parsed['skills'][:4]}, exp={len(parsed['experiences'])}")
    from app import pdf as pdfmod
    status = pdfmod.available_engines()
    check("内置 PDF 解析引擎始终可用", status["builtin"] is True, status["message"][:60])

    print("\n[1b] PDF 简历上传解析")
    pdf_en = _make_simple_pdf()
    text_en, fmt_en, _ = rs.decode_upload(
        "resume_en.pdf", base64.b64encode(pdf_en).decode("ascii"), None
    )
    check("英文 PDF（未压缩内容流）解析",
          fmt_en == "pdf" and "Zhang Ming" in text_en and "Python" in text_en
          and "Tableau" in text_en, repr(text_en[:110]))
    pdf_cn = _make_cjk_pdf()
    text_cn, fmt_cn, _ = rs.decode_upload(
        "resume_cn.pdf", base64.b64encode(pdf_cn).decode("ascii"), None
    )
    check("中文 PDF（Flate 压缩 + ToUnicode CMap）解析",
          fmt_cn == "pdf" and "张明" in text_cn and "据分" in text_cn,
          repr(text_cn[:80]))
    pdf_os = _make_objstm_pdf()
    text_os, _, _ = rs.decode_upload(
        "resume_objstm.pdf", base64.b64encode(pdf_os).decode("ascii"), None
    )
    check("PDF 1.5 对象流（ObjStm + Flate）解析",
          "张明" in text_os and "据分" in text_os, repr(text_os[:80]))
    try:
        rs.decode_upload("broken.pdf", base64.b64encode(b"%PDF-1.4 garbage").decode("ascii"), None)
        check("损坏 PDF 给出可读错误", False, "未报错")
    except ValueError as exc:
        check("损坏 PDF 给出可读错误", "PDF" in str(exc), str(exc)[:90])
    try:
        rs.decode_upload("scan.pdf", base64.b64encode(_image_only_pdf()).decode("ascii"), None)
        check("无文字层 PDF 给出 OCR 提示", False, "未报错")
    except ValueError as exc:
        check("无文字层 PDF 给出 OCR 提示", "OCR" in str(exc), str(exc)[:90])

    detect = sources.detect_system_proxy(force=True)
    check("系统代理检测（注册表 / 环境变量）可用",
          "recommended" in detect and "note" in detect and "registry_proxy" in detect,
          detect["note"][:70])
    check("有效代理解析不抛错", isinstance(sources.effective_proxy({}), str))
    slug_card = ('<li><div class="base-search-card"><a class="base-card__full-link" '
                 'href="https://www.linkedin.com/jobs/view/senior-data-analyst-at-acme-4485915276">'
                 '</a><h3 class="base-search-card__title">Senior Data Analyst</h3>'
                 '<h4 class="base-search-card__subtitle">Acme</h4>'
                 '<span class="job-search-card__location">Shanghai</span></div></li>')
    parsed = sources.parse_search_cards(slug_card)
    check("领英 slug 格式链接可解析职位 ID",
          bool(parsed) and parsed[0]["job_id"] == "4485915276",
          parsed[0]["job_id"] if parsed else "no card")

    print("\n[1c] 地区解析与偏好")
    queries, terms = sources.parse_location_queries("德国，法国，荷兰")
    check("中文多地点转为领英可识别查询词",
          "Germany" in queries and "France" in queries and "Netherlands" in queries,
          str(queries))
    check("欧洲各国/城市地点可识别为匹配",
          sources.region_matches("德国 柏林 Berlin", terms)
          and sources.region_matches("法国 巴黎 Paris", terms)
          and sources.region_matches("荷兰 Amsterdam", terms))
    check("美国职位被判定为地区不符",
          not sources.region_matches("美国 德州 休斯顿", terms)
          and not sources.region_matches("迈阿密-劳德代尔堡地区", terms))

    from app import notifier
    qq_settings = {"smtp_provider": "auto", "smtp_host": "smtp.office365.com",
                   "smtp_port": 587, "smtp_use_tls": True,
                   "smtp_user": "example@qq.com"}
    host, port, tls, provider, note = notifier.resolve_smtp(
        qq_settings, "example@qq.com"
    )
    check("QQ 发件账号自动纠正 SMTP 主机与端口",
          host == "smtp.qq.com" and port == 465 and provider == "qq"
          and "office365" in note, note[:90])
    check("服务商凭据提示可用", "授权码" in notifier.SMTP_PRESETS["qq"]["hint"])
    custom = {"smtp_provider": "custom", "smtp_host": "smtp.example.com",
              "smtp_port": 2525, "smtp_use_tls": True, "smtp_user": "a@qq.com"}
    c_host, c_port, _, c_provider, _ = notifier.resolve_smtp(custom, "a@qq.com")
    check("自定义服务商保留手动填写的主机",
          c_host == "smtp.example.com" and c_port == 2525 and c_provider == "")

    print("\n[2] 匹配度评估")
    jobs = []
    for job in sources.demo_jobs():
        clean = {k: v for k, v in job.items() if not k.startswith("_")}
        jid, _ = db.upsert_job(clean)
        jobs.append(db.get_job(jid))
    results = []
    for job in jobs:
        result = matcher.evaluate(job, structured, db.get_settings())
        result["job"] = job
        results.append(result)
    results.sort(key=lambda x: -x["score"])
    top = results[0]
    check("评分落在 0-100 且带分解", all(0 <= r["score"] <= 100 and r["breakdown"]["total"] == r["score"]
                                     for r in results))
    check("高分岗位命中真实技能", len(top["matched_skills"]) >= 5,
          f"top={top['score']} {top['matched_skills'][:6]}")
    check("低相关岗位评分明显更低", results[0]["score"] > results[-1]["score"] + 20,
          f"{results[0]['score']} vs {results[-1]['score']}")

    eu_job = dict(jobs[0]); eu_job["location"] = "德国 柏林"
    us_job = dict(jobs[0]); us_job["location"] = "美国 德州 休斯顿"
    region_settings = dict(db.get_settings())
    region_settings["location"] = "德国，法国，荷兰"
    region_settings["region_mode"] = "prefer"
    score_eu = matcher.evaluate(eu_job, structured, region_settings)["score"]
    score_us = matcher.evaluate(us_job, structured, region_settings)["score"]
    check("地区偏好使欧洲职位评分显著高于美国职位", score_eu > score_us + 15,
          f"{score_eu} vs {score_us}")
    region_settings["region_mode"] = "strict"
    check("严格模式排除非首选地区职位",
          matcher.evaluate(us_job, structured, region_settings)["excluded"] is True)

    print("\n[3] 零虚构改写与事实校验")
    best_job = top["job"]
    generated = writer.generate_both(structured, best_job, top, db.get_settings())
    for kind in ("resume", "cover_letter"):
        g = generated[kind]
        check(f"{kind} 通过事实校验", g["validation"]["passed"],
              "; ".join(g["validation"].get("violations", [])))
        check(f"{kind} 未新增事实", g["trace"]["facts_added"] == [])
    check("定制简历引用了原文要点",
          any(b in generated["resume"]["content"] for b in structured["experiences"][0]["bullets"]))
    check("Cover Letter 逐字引用真实 bullet",
          all(b in raw for b in generated["cover_letter"]["trace"]["quoted_bullets"]))
    ledger = writer.build_fact_ledger(structured, best_job)
    fabricated = writer.validate_text(
        "我曾主导 99 个 Kubernetes 集群迁移，并使用 Rust 将效率提升 300%，获得公司年度金奖。",
        ledger,
    )
    check("校验层能拦截虚构内容", not fabricated["passed"] and len(fabricated["violations"]) >= 2,
          "; ".join(fabricated["violations"]))
    en_job = dict(best_job)
    en_job["title"] = "Senior C++ Platform Engineer"
    en_job["description"] = "We need C++, Linux, Docker and Python experience for platform engineering."
    check("英文 JD 自动选择英文 / 中文 JD 自动选择中文",
          writer.letter_language(en_job, "auto") == "en"
          and writer.letter_language(best_job, "auto") == "zh")
    gen_en = writer.generate_both(structured, en_job, top, db.get_settings())
    check("英文定制简历通过事实校验", gen_en["resume"]["validation"]["passed"],
          "; ".join(gen_en["resume"]["validation"].get("violations", [])))
    check("英文 Cover Letter 通过事实校验",
          gen_en["cover_letter"]["validation"]["passed"]
          and "Dear Hiring Team" in gen_en["cover_letter"]["content"],
          "; ".join(gen_en["cover_letter"]["validation"].get("violations", [])))

    print("\n[4] 文档导出")
    docx = docx_export.build_docx(generated["resume"]["content"], "test")
    with zipfile.ZipFile(io.BytesIO(docx)) as zf:
        names = zf.namelist()
        xml = zf.read("word/document.xml").decode("utf-8")
    check("DOCX 结构有效且含简历文本",
          "word/document.xml" in names and "工作经历" in xml and "<w:document" in xml)

    print("\n[5] 监控流水线与 HTTP API")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        data, _, _ = http(base, "/api/state")
        check("GET /api/state", data["ok"] is True)
        data, _, _ = http(base, "/api/resume/sample", "POST", {})
        check("POST /api/resume/sample", data["ok"] and data["data"]["version_id"] > 0)
        data, _, _ = http(base, "/api/monitor/demo", "POST", {})
        run = data["data"]
        check("POST /api/monitor/demo 全链路", run["ok"] and run["jobs_fetched"] >= 8
              and run["documents_new"] > 0,
              json.dumps({k: run.get(k) for k in ("jobs_fetched", "above_threshold", "documents_new")},
                         ensure_ascii=False))
        data, _, _ = http(base, "/api/recommendations?min_score=0")
        recs = data["data"]
        check("推荐结果留存且可查询", len(recs) >= 8 and all("breakdown" in r for r in recs),
              f"count={len(recs)}")
        data, _, _ = http(base, "/api/recommendations?region=preferred&min_score=0")
        check("推荐列表支持地区筛选", data["ok"] and isinstance(data["data"], list),
              f"count={len(data['data'])}")
        data, _, _ = http(base, "/api/documents")
        docs = data["data"]
        check("自动生成定制文档", len(docs) >= 2, f"count={len(docs)}")
        doc, _, _ = http(base, f"/api/documents/{docs[0]['id']}")
        item = doc["data"]
        check("文档保存改写前原始全文快照", len(item["original_snapshot"]) > 200
              and item["validation"]["passed"])
        _, blob, ctype = http(base, f"/api/documents/{docs[0]['id']}/download?format=docx")
        check("DOCX 下载端点可用", blob[:2] == b"PK" and "wordprocessingml" in ctype)
        _, blob, _ = http(base, f"/api/documents/{docs[0]['id']}/download?format=md")
        check("Markdown 下载端点可用", len(blob) > 200)
        data, _, _ = http(base, "/api/resume/versions")
        check("版本历史完整保留", len(data["data"]) >= 3)
        data, _, _ = http(base, "/api/settings", "POST", {"min_score": 65})
        check("监控配置可保存", data["data"]["min_score"] == 65)
        data, _, _ = http(base, "/api/monitor/import", "POST", {
            "text": json.dumps({"title": "数据分析师（自检导入）", "company": "自检公司",
                                "location": "上海",
                                "description": "需要 SQL、Python、Tableau 与 3 年数据分析经验。"},
                               ensure_ascii=False)})
        check("手动导入 JD 并评估", data["data"]["ok"] and data["data"]["jobs_fetched"] >= 1)
        data, _, _ = http(base, "/api/monitor/runs")
        check("运行日志已记录", len(data["data"]) >= 2)
        data, _, _ = http(base, "/api/resume/import", "POST", {
            "filename": "upload.docx",
            "b64": base64.b64encode(docx_bytes).decode("ascii"),
        })
        check("POST /api/resume/import 文件上传",
              data["ok"] and data["data"]["version_id"] > 0)
        data, _, _ = http(base, "/api/pdf/status")
        check("GET /api/pdf/status 返回解析引擎状态",
              data["ok"] and data["data"]["builtin"] is True)
        data, _, _ = http(base, "/api/network/detect")
        check("GET /api/network/detect 返回系统代理检测",
              data["ok"] and "recommended" in data["data"])
        data, _, _ = http(base, "/api/resume/import", "POST", {
            "filename": "pdf_resume.pdf",
            "b64": base64.b64encode(pdf_en).decode("ascii"),
        })
        check("POST /api/resume/import 上传 PDF 简历",
              data["ok"] and "Zhang Ming" in data["data"]["raw_text"]
              and data["data"]["version_id"] > 0)
        data, _, _ = http(base, "/api/documents")
        cl_doc = next((d for d in data["data"] if d["doc_type"] == "cover_letter"), None)
        if cl_doc:
            doc, _, _ = http(base, f"/api/documents/{cl_doc['id']}")
            content = doc["data"]["content"]
            data, _, _ = http(base, "/api/documents/ai-write", "POST", {
                "recommendation_id": cl_doc["recommendation_id"],
                "doc_type": "cover_letter", "content": content,
            })
            check("POST /api/documents/ai-write 精写版本入库",
                  data["ok"] and data["data"]["accepted"])
            data, _, _ = http(base, "/api/documents/ai-write", "POST", {
                "recommendation_id": cl_doc["recommendation_id"],
                "doc_type": "cover_letter",
                "content": content + "\nI led 99 Kubernetes clusters and improved throughput by 300%.",
            })
            check("ai-write 拦截虚构内容",
                  data["ok"] and data["data"]["accepted"] is False)
        data, _, _ = http(base, "/api/notify/log")
        check("GET /api/notify/log 通知记录可查询",
              data["ok"] and isinstance(data["data"], list))
        data, _, _ = http(base, "/api/notify/preview")
        check("GET /api/notify/preview 生成邮件草稿",
              data["ok"] and "subject" in data["data"] and len(data["data"]["body"]) > 40)
        _, eml, eml_type = http(base, "/api/notify/eml")
        check("GET /api/notify/eml 生成免密码邮件草稿",
              b"Subject:" in eml and b"To:" in eml and "rfc822" in eml_type)
        data, _, _ = http(base, "/api/settings")
        check("通知阈值与示例收件邮箱已预置",
              data["data"]["notify_threshold"] == 80
              and "@" in data["data"]["notify_email"])
        data, _, _ = http(base, "/api/notify/test", "POST", {})
        check("未配置 SMTP 时给出明确提示",
              data["ok"] and data["data"]["ok"] is False
              and "应用专用密码" in data["data"]["message"])
        data, _, _ = http(base, "/api/notify/clear-credentials", "POST", {})
        check("POST /api/notify/clear-credentials 清空本机凭据", data["ok"])
        data, _, _ = http(base, "/api/documents")
        target = data["data"][0]
        rec_id = target["recommendation_id"]
        data, _, _ = http(base, f"/api/documents/{target['id']}/delete", "POST", {})
        check("POST /api/documents/{id}/delete 删除单份文档",
              data["ok"] and data["data"]["deleted_id"] == target["id"])
        data, _, _ = http(base, "/api/documents")
        check("删除后文档不再出现在列表",
              all(d["id"] != target["id"] for d in data["data"]))
        row = db.q1("SELECT docs_suppressed FROM recommendations WHERE id=?", (rec_id,))
        check("删除后抑制该职位自动生成新文档", bool(row and row["docs_suppressed"]))
        check("删除文档不影响不可变的原始简历版本", len(db.list_resume_versions()) >= 3)
        data, _, _ = http(base, f"/api/recommendations/{rec_id}/generate", "POST", {})
        row = db.q1("SELECT docs_suppressed FROM recommendations WHERE id=?", (rec_id,))
        check("手动重制材料可解除抑制", data["ok"] and row["docs_suppressed"] == 0)
        _, page, ctype = http(base, "/")
        check("前端页面与静态资源可访问", b"JobPilot" in page and "html" in ctype)
    finally:
        httpd.shutdown()
        httpd.server_close()

    print("\n" + "=" * 60)
    print(f"  自检结果：{len(PASS)} 项通过，{len(FAIL)} 项失败")
    if FAIL:
        print("  失败项：" + "、".join(FAIL))
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
