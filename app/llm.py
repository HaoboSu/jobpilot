"""Optional OpenAI-compatible LLM adapter (stdlib only).

The model is used only to polish wording *inside* the fact boundary. Whatever it
returns is validated by writer.validate_text(); failures automatically fall back
to the deterministic rule engine. The API key is sent to the configured endpoint
as an Authorization header.
"""
from __future__ import annotations

import json
import urllib.request

from . import resume as rs

SYSTEM_PROMPT = """You are a resume editor. You must obey these rules without exception:

1. Use ONLY facts present in the RESUME_FACTS JSON. Never invent, infer, exaggerate
   or add any experience, employer, project, skill, tool, certificate, metric,
   percentage, date, number, achievement or responsibility.
2. Do not add skills that are not listed in the resume, even if the job asks for them.
   Do not claim proficiency the resume does not state.
3. You may only reorder existing items, group them, quote them verbatim, and polish
   connective wording. Quoted bullet points must remain verbatim.
4. Job title and company name may be used because they come from the public posting.
5. Return ONLY a JSON object with exactly two string keys: "resume" and
   "cover_letter". No markdown fences, no commentary, no extra keys.
6. Write in the language requested in the user message (usually the job posting's language).
"""


def _chat(settings: dict, messages: list[dict], timeout: int = 90) -> str:
    base = (settings.get("llm_base_url") or "https://api.openai.com/v1").rstrip("/")
    if base.endswith("/chat/completions"):
        url = base
    else:
        url = base + "/chat/completions"
    payload = {
        "model": settings.get("llm_model") or "gpt-4o-mini",
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 2600,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + (settings.get("llm_api_key") or ""),
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8", errors="replace"))
    return body["choices"][0]["message"]["content"] or ""


def _facts_payload(structured: dict, job: dict, match: dict) -> str:
    return json.dumps({
        "RESUME_FACTS": {
            "basics": structured["basics"],
            "summary": structured["summary"],
            "skills": structured["skills"],
            "experiences": structured["experiences"],
            "projects": structured["projects"],
            "education": structured["education"],
            "certifications": structured["certifications"],
            "languages": structured["languages"],
            "extras": structured["extras"],
        },
        "JOB_POSTING": {
            "title": job.get("title"), "company": job.get("company"),
            "location": job.get("location"),
            "description": (job.get("description") or "")[:6000],
        },
        "MATCHED_REAL_SKILLS": match.get("matched_skills") or [],
        "REFERENCE_RULE_OUTPUT": {
            "resume": "see below", "cover_letter": "see below",
        },
    }, ensure_ascii=False)


def generate(settings: dict, structured: dict, job: dict, match: dict,
             ledger: dict, rule_resume: str, rule_letter: str,
             language: str = "en") -> dict:
    user = (
        _facts_payload(structured, job, match)
        + "\n\nBASELINE_VERSIONS (already fact-safe; you may rephrase connective text "
          "but must not add facts beyond them):\n"
        + json.dumps({"resume": rule_resume[:6000], "cover_letter": rule_letter[:4000]},
                     ensure_ascii=False)
        + f"\n\nWrite both documents in {'English' if language == 'en' else 'Chinese'}. "
          "Rewrite both documents now. Remember: JSON only."
    )
    content = _chat(settings, [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ])
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("LLM did not return a JSON object")
    parsed = json.loads(content[start:end + 1])
    return {
        "resume": str(parsed.get("resume") or ""),
        "cover_letter": str(parsed.get("cover_letter") or ""),
    }


def test_connection(settings: dict) -> dict:
    try:
        text = _chat(settings, [
            {"role": "system", "content": "Reply with exactly: OK"},
            {"role": "user", "content": "Reply with exactly: OK"},
        ], timeout=25)
        return {"ok": True, "message": "连接成功，模型返回：" + text.strip()[:60]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"{type(exc).__name__}: {str(exc)[:200]}"}
