"""JobPilot HTTP server - stdlib only, no framework, no install step."""
from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
import threading
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import (db, docx_export, llm, matcher, monitor, notifier,
               pdf as pdfmod, resume as rs, sources, writer)
from .seed import SAMPLE_RESUME

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
MAX_BODY = 16 * 1024 * 1024


def _masked_settings(settings: dict) -> dict:
    out = dict(settings)
    if out.get("llm_api_key"):
        out["llm_api_key"] = "********"
        out["llm_key_configured"] = True
    else:
        out["llm_key_configured"] = False
    if out.get("smtp_password"):
        out["smtp_password"] = "********"
        out["smtp_password_configured"] = True
    else:
        out["smtp_password_configured"] = False
    return out


def _version_summary(version: dict | None) -> dict | None:
    if not version:
        return None
    s = version["structured"]
    return {
        "id": version["id"], "label": version["label"],
        "created_at": version["created_at"], "source": version["source"],
        "name": s["basics"]["name"], "headline": s["basics"]["headline"],
        "skills_count": len(s["skills"]),
        "experiences_count": len(s["experiences"]),
        "is_sample": bool(s.get("_sample")),
        "raw_length": len(version["raw_text"]),
    }


def _generate_for_recommendation(rec_id: int, force: bool = True) -> dict:
    rec = db.get_recommendation(rec_id)
    if not rec:
        return {"ok": False, "error": "推荐记录不存在"}
    job = db.get_job(rec["job_id"])
    version = db.get_resume_version(rec["resume_version_id"])
    if not job or not version:
        return {"ok": False, "error": "职位或简历版本数据缺失"}
    previous = db.q(
        "SELECT id FROM documents WHERE recommendation_id=? ORDER BY id DESC LIMIT 1", (rec_id,)
    )
    existing = db.q("SELECT id FROM documents WHERE recommendation_id=? LIMIT 1", (rec_id,))
    if existing and not force:
        return {"ok": True, "created": 0, "message": "该职位已有定制文档"}
    db.set_docs_suppressed(rec_id, 0)
    match = {
        "jd_skills": rec["matched_skills"] + rec["missing_skills"],
        "matched_skills": rec["matched_skills"],
        "missing_skills": rec["missing_skills"],
    }
    settings = db.get_settings()
    generated = writer.generate_both(version["structured"], job, match, settings)
    created = 0
    for kind, label in (("resume", "定制简历"), ("cover_letter", "Cover Letter")):
        item = generated[kind]
        validation = dict(item["validation"])
        validation["trace"] = item["trace"]
        if previous:
            validation["previous_document_id"] = previous[0]["id"]
        db.create_document(
            rec_id=rec_id, job_id=job["id"], version_id=version["id"],
            doc_type=kind,
            title=f"{label} · {job.get('title') or ''} · {job.get('company') or ''}",
            content=item["content"], engine=item["engine"],
            original_snapshot=version["raw_text"],
            original_structured=version["structured"], validation=validation,
        )
        created += 1
    return {
        "ok": True, "created": created,
        "message": f"已生成 {created} 份文档（新版本已追加，历史版本保留）",
        "engines": {k: generated[k]["engine"] for k in ("resume", "cover_letter")},
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "JobPilot/1.0"

    def log_message(self, fmt: str, *args) -> None:
        # Keep the console readable: only API calls, not every static asset.
        if "/api/" in (self.path or ""):
            sys.stderr.write("  %s %s\n" % (self.command, self.path))

    # ------------------------------------------------------------- helpers
    def _send(self, status: int, payload: bytes, ctype: str,
              headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _ok(self, data) -> None:
        self._json({"ok": True, "data": data})

    def _fail(self, message: str, status: int = 400) -> None:
        self._json({"ok": False, "error": message}, status)

    def _body(self) -> dict:
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("不支持 Transfer-Encoding")
        length = int(self.headers.get("Content-Length") or 0)
        if length < 0:
            raise ValueError("Content-Length 不能为负数")
        if length == 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("请求体超过 16MB 限制")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON 请求体必须为对象")
        return data

    def _local_request(self, write: bool = False) -> bool:
        """Reject DNS rebinding and browser requests from other origins.

        CLI clients may omit Origin, but writes must use application/json.
        This is a local-browser boundary, not user authentication.
        """
        port = self.server.server_port
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if port == 80:
            allowed.update({"127.0.0.1", "localhost"})
        host = self.headers.get("Host", "").lower()
        origin = self.headers.get("Origin")
        if (host not in allowed
                or (origin is not None and origin != f"http://{host}")
                or self.headers.get("Sec-Fetch-Site") == "cross-site"):
            self._fail("仅允许本机同源请求", 403)
            return False
        if write and self.headers.get_content_type() != "application/json":
            self._fail("请使用 application/json", 415)
            return False
        return True

    def _static(self, rel: str) -> None:
        rel = "index.html" if rel in ("", "/") else rel.lstrip("/")
        root = os.path.realpath(WEB_DIR)
        path = os.path.realpath(os.path.join(root, urllib.parse.unquote(rel)))
        if os.path.commonpath([root, path]) != root or not os.path.isfile(path):
            self._fail("资源不存在", 404)
            return
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(path, "rb") as fh:
            self._send(200, fh.read(), ctype)

    # ---------------------------------------------------------------- verbs
    def do_OPTIONS(self) -> None:
        self._send(204, b"", "text/plain")

    def do_GET(self) -> None:
        if not self._local_request():
            return
        parsed = urllib.parse.urlparse(self.path)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)
        try:
            if not path.startswith("/api/"):
                self._static(path)
                return
            self._route_get(path, query)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._fail(f"{type(exc).__name__}: {exc}", 500)

    def do_POST(self) -> None:
        if not self._local_request(write=True):
            return
        path = urllib.parse.urlparse(self.path).path
        try:
            data = self._body()
            self._route_post(path, data)
        except (ValueError, json.JSONDecodeError) as exc:
            self._fail(f"请求格式错误：{exc}")
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._fail(f"{type(exc).__name__}: {exc}", 500)

    # ------------------------------------------------------------ GET routes
    def _route_get(self, path: str, query: dict) -> None:
        if path == "/api/state":
            overview = db.overview()
            version = db.get_resume_version()
            settings = _masked_settings(db.get_settings())
            region_queries, region_terms = sources.parse_location_queries(
                str(settings.get("location") or "")
            )
            settings["region_queries"] = region_queries
            settings["region_terms_count"] = len(region_terms)
            self._ok({
                "overview": overview,
                "settings": settings,
                "monitor": monitor.monitor.status(),
                "resume": _version_summary(version),
                "recent_runs": db.list_runs(6),
                "top_recommendations": db.list_recommendations(
                    min_score=float(db.get_settings().get("min_score") or 0), limit=5
                ),
                "sources": sources.source_labels(),
            })
            return

        if path == "/api/resume":
            version = db.get_resume_version()
            self._ok({
                "version": version,
                "years": rs.years_of_experience(version["structured"]) if version else 0,
                "canonical_skills": rs.canonical_skills(version["structured"]) if version else [],
            })
            return
        if path == "/api/resume/versions":
            self._ok(db.list_resume_versions())
            return
        m = re.fullmatch(r"/api/resume/versions/(\d+)", path)
        if m:
            version = db.get_resume_version(int(m.group(1)))
            if not version:
                self._fail("版本不存在", 404)
            else:
                self._ok(version)
            return
        m = re.fullmatch(r"/api/versions/(\d+)/download", path)
        if m:
            version = db.get_resume_version(int(m.group(1)))
            if not version:
                self._fail("版本不存在", 404)
                return
            name = f"原始简历_v{version['id']}.txt"
            self._download(version["raw_text"].encode("utf-8"), "text/plain; charset=utf-8", name)
            return

        if path == "/api/settings":
            self._ok(_masked_settings(db.get_settings()))
            return
        if path == "/api/pdf/status":
            self._ok(pdfmod.available_engines())
            return
        if path == "/api/network/detect":
            self._ok(sources.detect_system_proxy(force=True))
            return
        if path == "/api/network/diagnose":
            self._ok(sources.probe_network(db.get_settings()))
            return
        if path == "/api/monitor/runs":
            self._ok(db.list_runs(50))
            return
        if path == "/api/notify/log":
            self._ok(db.list_notifications(50))
            return
        if path == "/api/notify/preview":
            self._ok(notifier.preview_digest(db.get_settings()))
            return
        if path == "/api/notify/eml":
            preview = notifier.preview_digest(db.get_settings())
            payload = notifier.build_eml(
                db.get_settings(), preview["subject"], preview["body"]
            )
            self._download(payload, "message/rfc822", "JobPilot_通知邮件.eml")
            return
        if path == "/api/jobs":
            self._ok(db.list_jobs(int(query.get("limit", ["200"])[0])))
            return
        if path == "/api/recommendations":
            settings = db.get_settings()
            min_score = float(query.get("min_score", ["0"])[0])
            status = query.get("status", ["all"])[0]
            latest = query.get("latest", ["1"])[0] != "0"
            region = query.get("region", ["all"])[0]
            rows = db.list_recommendations(min_score, status, 300, latest)
            _, terms = sources.parse_location_queries(str(settings.get("location") or ""))
            for row in rows:
                row["region_ok"] = sources.region_matches(row.get("location"), terms)
            if region == "preferred" and settings.get("region_mode") != "off" and terms:
                rows = [r for r in rows if r["region_ok"] or r.get("remote")]
            self._ok(rows)
            return
        m = re.fullmatch(r"/api/recommendations/(\d+)", path)
        if m:
            rec = db.get_recommendation(int(m.group(1)))
            if not rec:
                self._fail("推荐记录不存在", 404)
                return
            self._ok({
                "recommendation": rec,
                "documents": db.list_documents(int(m.group(1))),
                "dimension_labels": {k: label for k, label, _ in matcher.DIMENSIONS},
            })
            return
        if path == "/api/documents":
            rec_id = query.get("recommendation_id", [None])[0]
            self._ok(db.list_documents(int(rec_id) if rec_id else None))
            return
        m = re.fullmatch(r"/api/documents/(\d+)", path)
        if m:
            doc = db.get_document(int(m.group(1)))
            if not doc:
                self._fail("文档不存在", 404)
            else:
                self._ok(doc)
            return
        m = re.fullmatch(r"/api/documents/(\d+)/download", path)
        if m:
            doc = db.get_document(int(m.group(1)))
            if not doc:
                self._fail("文档不存在", 404)
                return
            fmt = query.get("format", ["md"])[0]
            self._download_document(doc, fmt)
            return
        self._fail("接口不存在", 404)

    def _download(self, payload: bytes, ctype: str, filename: str) -> None:
        quoted = urllib.parse.quote(filename)
        self._send(200, payload, ctype, {
            "Content-Disposition": f"attachment; filename=\"jobpilot\"; filename*=UTF-8''{quoted}"
        })

    def _download_document(self, doc: dict, fmt: str) -> None:
        base = re.sub(r"[\\/:*?\"<>|·\s]+", "_", doc["title"])[:60] or "document"
        content = doc["content"]
        if fmt == "docx":
            payload = docx_export.build_docx(content, doc["title"])
            self._download(payload, "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                           base + ".docx")
        elif fmt == "txt":
            plain = re.sub(r"[#*>\-]{1,2}\s?", "", content)
            self._download(plain.encode("utf-8"), "text/plain; charset=utf-8", base + ".txt")
        else:
            self._download(content.encode("utf-8"), "text/markdown; charset=utf-8", base + ".md")

    # ----------------------------------------------------------- POST routes
    def _route_post(self, path: str, data: dict) -> None:
        if path == "/api/resume/import":
            filename = data.get("filename") or "简历.txt"
            text, fmt, display = rs.decode_upload(
                filename, data.get("b64"), data.get("text")
            )
            if not text.strip():
                self._fail("文件内容为空")
                return
            structured = (rs.normalize_structured(data["structured"])
                          if isinstance(data.get("structured"), dict)
                          else rs.parse_structured(text))
            label = (data.get("label") or display).strip()
            version_id = db.create_resume_version(
                label=label, raw_text=text, structured=structured,
                source="import", raw_filename=display, raw_format=fmt,
                note="导入的原始全文，永久保留、不可修改",
            )
            self._ok({
                "version_id": version_id, "structured": structured,
                "raw_text": text,
                "message": f"已导入并生成版本 v{version_id}，原始全文已留存",
            })
            return

        if path == "/api/resume/sample":
            structured = rs.normalize_structured(SAMPLE_RESUME)
            raw = rs.structured_to_text(structured)
            version_id = db.create_resume_version(
                label="示例简历（演示数据）", raw_text=raw, structured=structured,
                source="sample", raw_filename="sample_resume.md", raw_format="md",
                note="这是内置演示数据，不是你的真实简历；请导入自己的简历后再用于投递。",
            )
            self._ok({
                "version_id": version_id, "structured": structured,
                "message": f"已载入示例简历 v{version_id}（演示数据，非真实简历）",
            })
            return

        if path == "/api/resume/update":
            if not isinstance(data.get("structured"), dict):
                self._fail("缺少结构化简历内容")
                return
            structured = rs.normalize_structured(data["structured"])
            raw = rs.structured_to_text(structured)
            version_id = db.create_resume_version(
                label=(data.get("label") or "手动编辑的结构化简历").strip(),
                raw_text=raw, structured=structured, source="edit",
                raw_filename=None, raw_format="json",
                note="由用户在结构化编辑器中保存，生成新的不可变版本",
            )
            self._ok({
                "version_id": version_id, "structured": structured,
                "message": f"已保存为新版本 v{version_id}，旧版本仍可查看",
            })
            return

        if path == "/api/settings":
            patch = dict(data or {})
            for secret in ("llm_api_key", "smtp_password"):
                if patch.get(secret) == "********":
                    patch.pop(secret)
            settings = db.save_settings(patch)
            self._ok(_masked_settings(settings))
            return
        if path == "/api/notify/clear-credentials":
            db.save_settings({
                "smtp_password": "", "smtp_last_ok": 0, "smtp_last_message": "",
            })
            self._ok({"message": "已清空本机保存的 SMTP 凭据"})
            return
        if path == "/api/notify/test":
            current = db.get_settings()
            patch = dict(data or {})
            if patch.get("smtp_password") == "********":
                patch.pop("smtp_password")
            merged = {**current, **patch}
            result = notifier.send_email(
                merged,
                "[JobPilot] 邮件通知测试",
                "这是一封来自本机 JobPilot 的测试邮件。\n\n"
                "收到此邮件说明 SMTP 配置正确；今后有岗位达到设定的匹配阈值时，"
                "会自动发送岗位汇总到这个邮箱。\n\n"
                "JobPilot（本地运行）",
            )
            self._ok(result)
            return
        if path == "/api/llm/test":
            settings = dict(data or {})
            if settings.get("llm_api_key") == "********":
                settings["llm_api_key"] = db.get_settings().get("llm_api_key")
            self._ok(llm.test_connection(settings))
            return
        if path == "/api/pdf/install":
            self._ok(pdfmod.install_enhancement())
            return
        if path == "/api/documents/ai-write":
            rec_id = int((data or {}).get("recommendation_id") or 0)
            doc_type = str((data or {}).get("doc_type") or "cover_letter")
            content = str((data or {}).get("content") or "").strip()
            if not rec_id or doc_type not in ("resume", "cover_letter") or len(content) < 80:
                self._fail("需要 recommendation_id、doc_type 与不少于 80 字的正文")
                return
            rec = db.get_recommendation(rec_id)
            if not rec:
                self._fail("推荐记录不存在", 404)
                return
            job = db.get_job(rec["job_id"])
            version = db.get_resume_version(rec["resume_version_id"])
            if not job or not version:
                self._fail("职位或简历版本数据缺失")
                return
            ledger = writer.build_fact_ledger(version["structured"], job)
            validation = writer.validate_text(content, ledger)
            if not validation["passed"]:
                self._ok({
                    "accepted": False, "validation": validation,
                    "message": "内容未通过事实校验，未写入文档库：" + "；".join(validation["violations"][:3]),
                })
                return
            previous = db.q(
                "SELECT id FROM documents WHERE recommendation_id=? AND doc_type=? "
                "ORDER BY id DESC LIMIT 1", (rec_id, doc_type)
            )
            label = "定制简历" if doc_type == "resume" else "Cover Letter"
            language = writer.letter_language(
                job, db.get_settings().get("letter_language") or "auto"
            )
            trace = {
                "method": "workbuddy", "language": language,
                "operations": [
                    "由 WorkBuddy 依据原始简历与岗位 JD 撰写",
                    "仅使用简历中已有的经历、技能与数据",
                    "输出已通过数字／技能事实白名单校验",
                ],
                "facts_added": [],
                "external_facts_used": [
                    f"岗位名称：{job.get('title') or ''}",
                    f"公司名称：{job.get('company') or ''}",
                ],
            }
            validation = dict(validation)
            validation["trace"] = trace
            if previous:
                validation["previous_document_id"] = previous[0]["id"]
            doc_id = db.create_document(
                rec_id=rec_id, job_id=job["id"], version_id=version["id"],
                doc_type=doc_type,
                title=f"WorkBuddy 精写 · {label} · {job.get('title') or ''} · {job.get('company') or ''}",
                content=content + "\n", engine="workbuddy",
                original_snapshot=version["raw_text"],
                original_structured=version["structured"], validation=validation,
            )
            self._ok({
                "accepted": True, "document_id": doc_id, "language": language,
                "validation": validation,
                "message": f"已写入 WorkBuddy 精写版本 #{doc_id}，原有版本保留",
            })
            return
        if path == "/api/network/apply-proxy":
            proxy = str((data or {}).get("proxy") or "").strip()
            if not proxy:
                proxy = sources.detect_system_proxy(force=True).get("recommended") or ""
            if not proxy:
                self._fail("未检测到可用的系统代理，请手动填写 VPN 的本地 HTTP 代理地址")
                return
            db.save_settings({"linkedin_proxy": proxy, "use_linkedin": True})
            self._ok({
                "proxy": sources.mask_proxy(proxy),
                "message": f"已应用代理 {sources.mask_proxy(proxy)}，现在可以运行监控抓取真实职位",
            })
            return

        if path == "/api/monitor/run":
            self._ok(monitor.run_once("manual"))
            return
        if path == "/api/monitor/demo":
            self._ok(monitor.run_once("manual", force_demo=True))
            return
        if path == "/api/monitor/import":
            jobs = sources.parse_manual_payload(str(data.get("text") or ""))
            if not jobs:
                self._fail("没有解析到有效职位内容")
                return
            self._ok(monitor.run_once("manual", jobs_override=jobs))
            return
        if path == "/api/monitor/start":
            self._ok(monitor.monitor.start())
            return
        if path == "/api/monitor/stop":
            self._ok(monitor.monitor.stop())
            return

        m = re.fullmatch(r"/api/recommendations/(\d+)/status", path)
        if m:
            status = str(data.get("status") or "")
            if status not in ("new", "viewed", "applied", "dismissed"):
                self._fail("状态值不合法")
                return
            db.set_recommendation_status(int(m.group(1)), status)
            self._ok({"status": status})
            return
        m = re.fullmatch(r"/api/documents/(\d+)/delete", path)
        if m:
            removed = db.delete_document(int(m.group(1)))
            if not removed:
                self._fail("文档不存在", 404)
                return
            label = "定制简历" if removed["doc_type"] == "resume" else "Cover Letter"
            self._ok({
                "deleted_id": removed["id"],
                "title": removed["title"],
                "message": (
                    f"已删除 {label} #{removed['id']}；原始简历版本完整保留，"
                    "并已阻止该职位在下一轮监控中自动生成新文档"
                ),
            })
            return
        m = re.fullmatch(r"/api/recommendations/(\d+)/generate", path)
        if m:
            self._ok(_generate_for_recommendation(int(m.group(1)), force=True))
            return
        self._fail("接口不存在", 404)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    db.init_db()
    port = int(os.environ.get("JOBPILOT_PORT") or "8765")
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    settings = db.get_settings()
    if settings.get("auto_monitor"):
        monitor.monitor.start()
    url = f"http://127.0.0.1:{port}/"
    print("=" * 62)
    print("  JobPilot · 求职辅助 Agent 应用")
    print(f"  服务地址：{url}")
    print(f"  数据目录：{db.DATA_DIR}")
    print("  关闭服务：在本窗口按 Ctrl+C")
    print("=" * 62)
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
