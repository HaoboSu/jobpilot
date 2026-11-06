"""Continuous monitoring pipeline and background scheduler."""
from __future__ import annotations

import threading
import traceback
from datetime import datetime, timedelta

from . import db, matcher, notifier, sources, writer

_lock = threading.Lock()


def _generate_documents(job: dict, rec_id: int, version: dict,
                        match: dict, settings: dict) -> int:
    """Auto-tailor both documents for a recommendation that has none yet."""
    existing = db.q("SELECT id FROM documents WHERE recommendation_id=? LIMIT 1", (rec_id,))
    if existing:
        return 0
    suppressed = db.q1(
        "SELECT docs_suppressed FROM recommendations WHERE id=?", (rec_id,)
    )
    if suppressed and suppressed.get("docs_suppressed"):
        return 0  # user deleted these documents on purpose
    structured = version["structured"]
    generated = writer.generate_both(structured, job, match, settings)
    original = db.get_resume_version(version["id"])
    snapshot = original["raw_text"] if original else ""
    count = 0
    for kind, label in (("resume", "定制简历"), ("cover_letter", "Cover Letter")):
        item = generated[kind]
        validation = dict(item["validation"])
        validation["trace"] = item["trace"]
        db.create_document(
            rec_id=rec_id, job_id=job["id"], version_id=version["id"],
            doc_type=kind,
            title=f"{label} · {job.get('title') or ''} · {job.get('company') or ''}",
            content=item["content"], engine=item["engine"],
            original_snapshot=snapshot, original_structured=structured,
            validation=validation,
        )
        count += 1
    return count


def run_once(trigger: str = "manual", force_demo: bool = False,
             jobs_override: list[dict] | None = None) -> dict:
    """One complete monitoring cycle. Safe to call from HTTP or scheduler."""
    with _lock:
        settings = db.get_settings()
        version = db.get_resume_version()
        if not version:
            return {"ok": False, "message": "请先导入或载入一份简历，再开始监控。"}
        run_id = db.start_run(trigger, "linkedin" if settings.get("use_linkedin") else "manual")
        notes: list[str] = []
        jobs: list[dict] = []
        try:
            if jobs_override is not None:
                jobs = jobs_override
                notes.append(f"手动导入 {len(jobs)} 个职位")
            elif force_demo:
                jobs = sources.demo_jobs()
                notes.append("按要求载入演示职位数据（明确标注，非真实招聘信息）")
            else:
                if settings.get("use_linkedin"):
                    real, fetch_notes = sources.fetch_linkedin(settings)
                    jobs.extend(real)
                    notes.extend(fetch_notes)
                if not jobs and settings.get("demo_fallback"):
                    jobs = sources.demo_jobs()
                    notes.append(
                        "未取得领英真实职位（可能是网络限制或接口不可用），已降级载入演示职位数据；"
                        "演示结果仅用于体验流程，不代表真实招聘信息。"
                    )
                elif not jobs:
                    notes.append("未取得任何职位，且已关闭演示数据降级。")

            # Persist jobs (original rows are immutable, no deletion).
            jobs_new = 0
            stored: list[dict] = []
            for job in jobs:
                payload = {k: v for k, v in job.items() if not k.startswith("_")}
                job_id, is_new = db.upsert_job(payload)
                if is_new:
                    jobs_new += 1
                full = db.get_job(job_id)
                if full:
                    stored.append(full)

            # Evaluate every known job against the current resume version so
            # historical results are retained per version, not overwritten.
            all_jobs = db.list_jobs(limit=1000)
            threshold = float(settings.get("min_score") or 0)
            recs_new = 0
            docs_new = 0
            above = 0
            for job in all_jobs:
                match = matcher.evaluate(job, version["structured"], settings)
                rec_id, is_new = db.upsert_recommendation(
                    job_id=job["id"], resume_version_id=version["id"],
                    score=match["score"], breakdown=match["breakdown"],
                    matched=match["matched_skills"], missing=match["missing_skills"],
                    reasons=match["reasons"],
                )
                if is_new:
                    recs_new += 1
                if match["score"] >= threshold and not match["excluded"]:
                    above += 1
                    if settings.get("auto_generate_docs"):
                        docs_new += _generate_documents(
                            job, rec_id, version, match, settings
                        )

            message = "；".join(notes) if notes else "监控完成"
            try:
                notify = notifier.notify_high_matches(settings)
                message += f"；邮件通知：{notify['message']}"
            except Exception as exc:  # noqa: BLE001 - notification must not break monitoring
                notify = {"ok": False, "sent": 0, "message": f"通知模块异常：{exc}"}
                message += f"；邮件通知异常：{type(exc).__name__}"
            db.finish_run(run_id, "success", message, len(stored), jobs_new, recs_new)
            return {
                "ok": True, "run_id": run_id,
                "jobs_fetched": len(stored), "jobs_new": jobs_new,
                "recommendations_new": recs_new, "above_threshold": above,
                "documents_new": docs_new, "notes": notes,
                "notification": notify,
                "message": message,
            }
        except Exception as exc:  # noqa: BLE001
            db.finish_run(run_id, "error", f"{type(exc).__name__}: {exc}", 0, 0, 0)
            traceback.print_exc()
            return {"ok": False, "message": f"监控运行失败：{type(exc).__name__}: {exc}"}


class Monitor:
    """Simple interval scheduler; settings changes are picked up each cycle."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.next_run_at: str | None = None
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> dict:
        if self.running:
            return {"ok": True, "message": "自动监控已在运行"}
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="jobpilot-monitor",
                                        daemon=True)
        self._thread.start()
        return {"ok": True, "message": "自动监控已启动"}

    def stop(self) -> dict:
        self._stop.set()
        self.next_run_at = None
        return {"ok": True, "message": "自动监控已停止"}

    def _loop(self) -> None:
        # Run one cycle immediately on start, then on the configured interval.
        while not self._stop.is_set():
            try:
                settings = db.get_settings()
                result = run_once("auto")
                if not result.get("ok"):
                    self.last_error = result.get("message")
                minutes = max(5, int(settings.get("interval_minutes") or 60))
                self.next_run_at = (
                    datetime.now().astimezone() + timedelta(minutes=minutes)
                ).isoformat(timespec="seconds")
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"{type(exc).__name__}: {exc}"
            minutes = max(5, int(db.get_settings().get("interval_minutes") or 60))
            if self._stop.wait(minutes * 60):
                break
        self.next_run_at = None

    def status(self) -> dict:
        return {
            "running": self.running,
            "next_run_at": self.next_run_at,
            "last_error": self.last_error,
        }


monitor = Monitor()
