"""Email notifications when a job reaches the configured match threshold.

Uses only the standard library (smtplib / email). The SMTP host/port are
resolved from a provider preset (or from the sender address) so changing the
sending mailbox cannot silently leave a stale Outlook host behind. Credentials
stay in the local SQLite database and are never logged.
"""
from __future__ import annotations

import json
import re
import smtplib
import ssl
from email.message import EmailMessage

from . import db

OUTLOOK_HELP = (
    "个人 @outlook.com / @hotmail.com 邮箱自 2024-09 起已对第三方客户端禁用密码式 "
    "SMTP 基本验证（应用专用密码通常也无效，会报 535 5.7.139）。"
    "推荐：① 用「下载 .eml 草稿」或「用邮件客户端打开」零配置发送；"
    "② 或改用 QQ / 163 / Gmail 邮箱的 SMTP 授权码作为发件通道。"
)

SMTP_PRESETS = {
    "qq": {
        "host": "smtp.qq.com", "port": 465, "tls": False,
        "hint": "QQ 邮箱需用 16 位「授权码」：QQ 邮箱网页版 → 设置 → 账户 → "
                "开启 POP3/IMAP/SMTP 服务 → 生成授权码。不是 QQ 登录密码。",
    },
    "163": {
        "host": "smtp.163.com", "port": 465, "tls": False,
        "hint": "163 / 126 邮箱需在 设置 → POP3/SMTP/IMAP 开启服务并生成「授权码」。",
    },
    "gmail": {
        "host": "smtp.gmail.com", "port": 587, "tls": True,
        "hint": "Gmail 需开启两步验证后生成 16 位「应用专用密码」"
                "（myaccount.google.com → 安全性 → 应用密码）。",
    },
    "outlook": {
        "host": "smtp.office365.com", "port": 587, "tls": True,
        "hint": "个人 @outlook.com / @hotmail.com 已禁用密码式 SMTP（535 5.7.139），"
                "此通道通常不可用，建议改用「预览并发送」零配置方式。",
    },
}

_DOMAIN_PROVIDER = {
    "qq.com": "qq", "foxmail.com": "qq",
    "163.com": "163", "126.com": "163", "yeah.net": "163",
    "gmail.com": "gmail", "googlemail.com": "gmail",
    "outlook.com": "outlook", "hotmail.com": "outlook",
    "live.com": "outlook", "msn.com": "outlook",
}


def detect_provider(user: str) -> str:
    domain = user.rsplit("@", 1)[-1].lower() if "@" in user else ""
    return _DOMAIN_PROVIDER.get(domain, "")


def resolve_smtp(settings: dict, user: str) -> tuple[str, int, bool, str, str]:
    """Return (host, port, tls, provider, note); persist corrected presets."""
    chosen = str(settings.get("smtp_provider") or "auto").strip().lower()
    provider = "" if chosen == "custom" else (
        detect_provider(user) if chosen == "auto" else chosen
    )
    preset = SMTP_PRESETS.get(provider)
    if preset:
        host, port, tls = preset["host"], preset["port"], preset["tls"]
        old = str(settings.get("smtp_host") or "").strip()
        db.save_settings({"smtp_host": host, "smtp_port": port, "smtp_use_tls": tls})
        note = f"已套用 {provider} 预设 {host}:{port}"
        if old and old != host:
            note += f"（原为 {old}）"
        return host, port, tls, provider, note + "。"
    host = str(settings.get("smtp_host") or "smtp.office365.com").strip()
    try:
        port = int(settings.get("smtp_port") or 587)
    except (TypeError, ValueError):
        port = 587
    return host, port, bool(settings.get("smtp_use_tls", True)), "", ""


def send_email(settings: dict, subject: str, body: str,
               to: str | None = None) -> dict:
    address = (to or settings.get("notify_email") or "").strip()
    password = str(settings.get("smtp_password") or "").strip()
    if not address:
        return {"ok": False, "message": "未配置收件邮箱"}
    if not password:
        return {"ok": False, "message": "未配置 SMTP 密码 / 授权码。" + OUTLOOK_HELP}
    user = str(settings.get("smtp_user") or address).strip()
    host, port, tls, provider, note = resolve_smtp(settings, user)
    hint = SMTP_PRESETS.get(provider, {}).get("hint", "")

    format_note = ""
    if provider == "qq":
        compact = re.sub(r"\s+", "", password)
        if len(compact) != 16 or not compact.isalnum():
            format_note = ("当前凭据不像 QQ 邮箱的 16 位授权码，请确认用的是"
                           "「授权码」而不是 QQ 登录密码。")

    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = address
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=25,
                                  context=ssl.create_default_context()) as smtp:
                smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=25) as smtp:
                smtp.ehlo()
                if tls:
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                smtp.login(user, password)
                smtp.send_message(msg)
        message = (note + " " if note else "") + f"邮件已发送至 {address}"
        db.save_settings({"smtp_last_ok": 1, "smtp_last_message": message[:300]})
        return {"ok": True, "message": message, "provider": provider, "host": host}
    except Exception as exc:  # noqa: BLE001
        detail = str(exc)
        if ("5.7.139" in detail or "SmtpClientAuthentication" in detail
                or "basic authentication is disabled" in detail.lower()):
            message = (
                "微软已禁用该邮箱的 SMTP 基本验证（535 5.7.139）。个人 Outlook 邮箱"
                "无法自行开启，应用专用密码也无效。请改用「预览并发送 / 下载 .eml 草稿」，"
                "或把发件服务商换成 QQ / 163 / Gmail。"
            )
        elif provider in ("qq", "163") and (
                "SMTPServerDisconnected" in type(exc).__name__
                or "Connection unexpectedly closed" in detail):
            message = (
                f"{provider} 邮箱服务器在认证阶段断开了连接，通常是凭据不对或 SMTP 服务未开启。"
                + (format_note + " " if format_note else "")
                + SMTP_PRESETS[provider]["hint"]
            )
        elif ("5.7.3" in detail and ("OUTLOOK" in detail.upper()
                                     or "office365" in host.lower())) and provider != "outlook":
            message = ("发件账号与 SMTP 服务器不匹配：正在用非微软账号登录微软服务器。"
                       "请把「发件服务商」选为对应邮箱（如 QQ 邮箱），或自定义主机为 smtp.qq.com、端口 465。")
        elif provider in ("qq", "163", "gmail") and (
                "535" in detail or "Authentication" in detail or "authentication" in detail):
            message = (f"{provider} 邮箱认证失败（535）。" + (format_note or hint)
                       + " 原始错误：" + detail[:110])
        else:
            message = f"SMTP 发送失败：{type(exc).__name__}: {detail[:200]}"
        if format_note and format_note not in message:
            message = format_note + " " + message
        if note:
            message = note + " " + message
        db.save_settings({"smtp_last_ok": 0, "smtp_last_message": message[:300]})
        return {"ok": False, "message": message, "provider": provider, "host": host}


def build_eml(settings: dict, subject: str, body: str,
              to: str | None = None) -> bytes:
    """A ready-to-send .eml draft - no SMTP credentials needed.

    Double-clicking the file opens the user's default mail client with the
    recipient, subject and body pre-filled; one click on Send delivers it.
    """
    address = (to or settings.get("notify_email") or "").strip()
    msg = EmailMessage()
    msg["From"] = str(settings.get("smtp_user") or address).strip()
    msg["To"] = address
    msg["Subject"] = subject
    msg.set_content(body)
    return msg.as_bytes()


def _digest(rows: list[dict], threshold: float) -> tuple[str, str]:
    subject = f"[JobPilot] {len(rows)} 个岗位匹配度达到 {threshold:g} 分"
    lines = [
        f"JobPilot 检测到 {len(rows)} 个匹配度 ≥ {threshold:g} 分的岗位：",
        "",
    ]
    for r in rows:
        lines.append(f"● {r['score']:.1f} 分  {r['title']}")
        lines.append(f"  公司：{r['company'] or '未提供'}")
        lines.append(f"  地点：{r['location'] or '未标明'}")
        try:
            skills = json.loads(r.get("matched_skills_json") or "[]")
        except (ValueError, TypeError):
            skills = []
        if skills:
            lines.append("  命中技能：" + "、".join(skills[:8]))
        if r.get("url"):
            lines.append(f"  职位链接：{r['url']}")
        lines.append("")
    lines += [
        "以上结果基于当前简历版本评估，完整评分依据、定制简历与 Cover Letter",
        "请在 JobPilot 工作台查看：http://127.0.0.1:8765/#jobs",
        "",
        "（本邮件由本机 JobPilot 自动发送；改写内容均来自你的原始简历，未虚构经历或技能。）",
    ]
    return subject, "\n".join(lines)


def preview_digest(settings: dict | None = None) -> dict:
    """Show exactly what would be emailed, without sending anything."""
    settings = settings or db.get_settings()
    try:
        threshold = float(settings.get("notify_threshold") or 80)
    except (TypeError, ValueError):
        threshold = 80.0
    rows = db.q(
        "SELECT r.id AS rec_id, r.score, r.job_id, r.matched_skills_json, "
        "j.title, j.company, j.location, j.url "
        "FROM recommendations r JOIN jobs j ON j.id=r.job_id "
        "WHERE r.resume_version_id=(SELECT MAX(id) FROM resume_versions) "
        "AND r.score>=? AND r.status!='dismissed' ORDER BY r.score DESC",
        (threshold,),
    )
    if rows:
        subject, body = _digest(rows[:10], threshold)
        note = f"当前有 {len(rows)} 个 ≥{threshold:g} 分岗位，以下为将发送的邮件内容。"
    else:
        top = db.q1(
            "SELECT MAX(score) AS s FROM recommendations "
            "WHERE resume_version_id=(SELECT MAX(id) FROM resume_versions)"
        ) or {}
        subject = f"[JobPilot] 高分职位监控状态（阈值 {threshold:g} 分）"
        body = (
            f"当前简历版本暂无匹配度 ≥ {threshold:g} 分的岗位。\n"
            f"当前最高匹配度：{float(top.get('s') or 0):.1f} 分。\n\n"
            "一旦有岗位达到阈值，JobPilot 会自动把岗位汇总发送到本邮箱；"
            "同一岗位同一分数不会重复发送。\n\n"
            "工作台：http://127.0.0.1:8765/#jobs\n"
            "（本邮件预览由本机 JobPilot 生成）"
        )
        note = "暂无达标岗位，以下为状态示例邮件。"
    return {"subject": subject, "body": body, "note": note,
            "qualified": len(rows), "threshold": threshold}


def notify_high_matches(settings: dict | None = None) -> dict:
    """Send one digest per run for newly qualified jobs; never send duplicates."""
    settings = settings or db.get_settings()
    if not settings.get("notify_enabled"):
        return {"ok": True, "sent": 0, "message": "邮件通知已关闭"}
    address = (settings.get("notify_email") or "").strip()
    if not address:
        return {"ok": False, "sent": 0, "message": "未配置收件邮箱"}
    try:
        threshold = float(settings.get("notify_threshold") or 80)
    except (TypeError, ValueError):
        threshold = 80.0
    rows = db.q(
        "SELECT r.id AS rec_id, r.score, r.job_id, r.matched_skills_json, "
        "j.title, j.company, j.location, j.url "
        "FROM recommendations r JOIN jobs j ON j.id=r.job_id "
        "WHERE r.resume_version_id=(SELECT MAX(id) FROM resume_versions) "
        "AND r.score>=? AND r.status!='dismissed' ORDER BY r.score DESC",
        (threshold,),
    )
    fresh = [r for r in rows
             if not db.notification_exists(r["rec_id"], r["score"], address)]
    if not fresh:
        return {"ok": True, "sent": 0, "message": f"暂无新增 ≥{threshold:g} 分岗位，未重复发送"}
    subject, body = _digest(fresh, threshold)
    result = send_email(settings, subject, body)
    if result["ok"]:
        for r in fresh:
            db.record_notification(
                r["rec_id"], r["job_id"], r["score"], address,
                subject, "sent", result["message"],
            )
    # Failures are deliberately NOT recorded, so the next run can retry once
    # the user has configured the SMTP app password.
    return {**result, "sent": len(fresh) if result["ok"] else 0}
