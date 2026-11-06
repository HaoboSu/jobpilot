"""Grounded resume tailoring and cover-letter writing.

Hard rule: no experience, skill, company, metric, date or achievement may appear
in a generated document unless it already exists in the resume.

The built-in rule engine achieves this structurally - it only reorders, groups
and quotes existing entries verbatim. Both the tailored resume and the cover
letter are emitted in the language of the job posting (English for European /
English JDs, Chinese for Chinese JDs), configurable via `letter_language`.
An optional LLM path is additionally constrained by a fact ledger; if
validation fails, the app automatically falls back to the rule engine.
"""
from __future__ import annotations

import re
from typing import Any

from . import db
from . import llm
from . import resume as rs
from . import skills as sk

NOTICE = {
    "zh": (
        "生成说明：本版本仅对原始简历中已有的条目做相关性排序、分组与原文引用，"
        "未新增、删改任何经历、技能、数字或成果。原始简历全文已另行留存，可在「原文对照」中核对。"
    ),
    "en": (
        "Note: this version only reorders, groups and quotes entries that already exist "
        "in the original resume. No experience, skill, metric or achievement has been added "
        "or altered. The unedited original is retained separately under the Original tab."
    ),
}

LABELS = {
    "zh": {
        "profile": "个人概述", "skills": "核心技能", "experience": "工作经历",
        "projects": "项目经历", "education": "教育背景",
        "certifications": "证书与认证", "languages": "语言能力",
        "related": "岗位相关", "other": "其他技能", "target": "定制目标岗位",
        "years": "可核算工作年限", "matched": "与本岗位要求直接相关的既有技能",
        "empty_skills": "（原始简历未填写技能条目）", "note": NOTICE["zh"],
    },
    "en": {
        "profile": "Profile", "skills": "Core Skills", "experience": "Professional Experience",
        "projects": "Projects", "education": "Education",
        "certifications": "Certifications", "languages": "Languages",
        "related": "Role-relevant", "other": "Additional skills", "target": "Target role",
        "years": "Verifiable professional experience", "matched": "Existing skills relevant to this role",
        "empty_skills": "(No skills listed in the original resume)", "note": NOTICE["en"],
    },
}


def letter_language(job: dict, setting: str = "auto") -> str:
    """Pick the document language from the job posting unless overridden."""
    if setting in ("zh", "en"):
        return setting
    blob = (job.get("title") or "") + " " + (job.get("description") or "")
    cjk = len(re.findall(r"[\u4e00-\u9fff]", blob))
    latin = len(re.findall(r"[A-Za-z]", blob))
    if cjk == 0 and latin > 0:
        return "en"
    return "en" if latin > cjk * 2 else "zh"


# ------------------------------------------------------------- fact ledger
def build_fact_ledger(structured: dict, job: dict | None = None) -> dict[str, Any]:
    s = rs.normalize_structured(structured)
    raw_text = rs.structured_to_text(s)
    blob = rs.resume_plain_text(s) + "\n" + raw_text
    entities: set[str] = set()
    for e in s["experiences"]:
        entities |= {e["company"], e["title"], e["location"]}
    for p in s["projects"]:
        entities |= {p["name"], p["role"]}
    for e in s["education"]:
        entities |= {e["school"], e["degree"], e["major"]}
    entities |= set(s["certifications"]) | set(s["languages"]) | set(s["skills"])

    numbers = sk.extract_numbers(blob)
    years = rs.years_of_experience(s)
    numbers |= {f"{years:g}", str(years), str(int(years))}
    extra_skills: set[str] = set()
    # Job title / company / location are public facts from the posting, so
    # skill-like words inside them are allowed without implying resume facts.
    if job:
        public_name = " ".join(str(job.get(k) or "") for k in ("title", "company", "location"))
        numbers |= sk.extract_numbers(public_name)
        extra_skills |= set(sk.extract_skills(public_name))
        entities |= {str(job.get("company") or ""), str(job.get("location") or "")}
    entities = {e.strip().lower() for e in entities if e and e.strip()}

    return {
        "raw_text": raw_text,
        "blob": blob,
        "structured": s,
        "skills": set(rs.canonical_skills(s)),
        "extra_skills": extra_skills,
        "numbers": numbers,
        "entities": entities,
        "years": years,
    }


def _compact(text: str) -> str:
    """Keep only letters, digits and CJK characters for verbatim lookup."""
    return "".join(
        ch for ch in (text or "")
        if ch.isalnum() or "\u4e00" <= ch <= "\u9fff"
    )


def validate_text(text: str, ledger: dict) -> dict[str, Any]:
    """Hard fact check: every number and every named skill must be grounded."""
    violations: list[str] = []
    found_numbers = sk.extract_numbers(text)
    bad_numbers = sorted(n for n in found_numbers if n not in ledger["numbers"])
    for n in bad_numbers:
        violations.append(f"出现简历中不存在或与岗位无关的数字「{n}」")

    found_skills = sk.extract_canonical_mentions(text)
    allowed_skills = {
        x.lower() for x in ledger["skills"] | ledger.get("extra_skills", set())
    }
    bad_skills = [x for x in found_skills if x.lower() not in allowed_skills]
    for x in bad_skills:
        violations.append(f"出现简历中未声明的技能「{x}」")

    source = _compact(ledger["blob"] + "\n" + ledger["raw_text"])
    sentences = [x.strip() for x in re.split(r"[\n。；;]", text) if len(x.strip()) > 12]
    untraceable = 0
    for sentence in sentences:
        compact = _compact(sentence)
        if len(compact) >= 12 and compact not in source:
            if sk.extract_numbers(sentence) or sk.extract_canonical_mentions(sentence):
                untraceable += 1

    return {
        "passed": not violations,
        "violations": violations,
        "checked_numbers": len(found_numbers),
        "checked_skills": len(found_skills),
        "untraceable_fact_sentences": untraceable,
        "checked_sentences": len(sentences),
        "rule": "仅允许出现原始简历中已有的数字与规范技能名称；岗位标题/公司名/地点来自公开 JD。带事实信息但无法逐字溯源的句子计入审计统计，供人工复核。",
    }


# -------------------------------------------------------------- relevance
def _relevance(text: str, jd_skills: list[str], jd_tokens: set[str]) -> float:
    low = (text or "").lower()
    hits = sum(1 for s in jd_skills if s.lower() in low)
    skill_part = min(1.0, hits / max(1, len(jd_skills))) if jd_skills else 0.0
    token_part = sk.overlap_ratio(jd_tokens, sk.content_tokens(text))
    return 3.0 * skill_part + 1.0 * token_part


def _rank(items: list[dict], jd_skills: list[str], jd_tokens: set[str],
          text_getter) -> tuple[list[dict], dict]:
    scored = [(_relevance(text_getter(item), jd_skills, jd_tokens), idx, item)
              for idx, item in enumerate(items)]
    scored.sort(key=lambda t: (-t[0], t[1]))
    order_new = [t[1] for t in scored]
    order_old = list(range(len(items)))
    trace = {"original_order": order_old, "new_order": order_new,
             "reordered": order_new != order_old}
    return [t[2] for t in scored], trace


def _evidence(s: dict, jd_skills: list[str], jd_tokens: set[str]):
    """Pick real bullets / project lines, ranked by relevance to this JD."""
    ranked_exp = _rank(
        s["experiences"], jd_skills, jd_tokens,
        lambda e: " ".join([e["company"], e["title"], *e["bullets"]]),
    )[0]
    ranked_proj = _rank(
        s["projects"], jd_skills, jd_tokens,
        lambda p: " ".join([p["name"], p["role"], p["description"], *p["bullets"]]),
    )[0]
    evidence: list[tuple[str, str, str]] = []
    for e in ranked_exp[:2]:
        ranked_b = _rank(
            [{"t": x} for x in e["bullets"]], jd_skills, jd_tokens,
            lambda x: x["t"],
        )[0]
        for x in ranked_b[:2]:
            evidence.append((e["company"], e["title"], x["t"]))
    project = ranked_proj[0] if ranked_proj else None
    project_lines: list[str] = []
    if project:
        project_lines = project["bullets"][:2]
    return evidence, project, project_lines


# ------------------------------------------------------------ tailored resume
def tailored_resume(structured: dict, job: dict, match: dict,
                    ledger: dict | None = None,
                    language: str | None = None) -> tuple[str, dict]:
    ledger = ledger or build_fact_ledger(structured, job)
    language = language or letter_language(job)
    L = LABELS[language]
    s = ledger["structured"]
    b = s["basics"]
    jd_skills = match.get("jd_skills") or []
    jd_tokens = sk.content_tokens(
        (job.get("title") or "") + " " + (job.get("description") or "")
    )
    matched = match.get("matched_skills") or []
    years = ledger["years"]

    lines: list[str] = [f"# {b['name'] or 'Applicant'}"]
    contact = " | ".join(x for x in (
        b["headline"], b["location"], b["phone"], b["email"], *b["links"]
    ) if x)
    if contact:
        lines.append(contact)
    lines += ["", f"> {L['target']}: {job.get('title') or ''} · {job.get('company') or ''}", ""]

    lines.append("## " + L["profile"])
    if s["summary"]:
        lines.append(s["summary"])
    facts = []
    if years:
        facts.append(f"{L['years']}: {years:g} " + ("years" if language == "en" else "年"))
    if matched:
        facts.append(L["matched"] + "：" + "、".join(matched[:8]))
    if facts:
        lines.append("；".join(facts) + ("." if language == "en" else "。"))
    lines.append("")

    lines.append("## " + L["skills"])
    declared = [x for x in s["skills"] if x]
    hit_words = {m.lower() for m in matched}
    highlight, rest = [], []
    for item in declared:
        mapped = sk.extract_skills(item)
        target = (highlight if any(m.lower() in hit_words for m in mapped)
                  or item.lower() in hit_words else rest)
        target.append(item)
    sep = ", " if language == "en" else "、"
    if highlight:
        lines.append(f"**{L['related']}**: " + sep.join(highlight))
    if rest:
        lines.append(f"**{L['other']}**: " + sep.join(rest))
    if not declared:
        lines.append(L["empty_skills"])
    lines.append("")

    ranked_exp, exp_trace = _rank(
        s["experiences"], jd_skills, jd_tokens,
        lambda e: " ".join([e["company"], e["title"], *e["bullets"]]),
    )
    lines.append("## " + L["experience"])
    bullet_traces = []
    for e in ranked_exp:
        head = " | ".join(x for x in (
            e["company"], e["title"], rs._period(e["start"], e["end"]), e["location"]
        ) if x)
        if head:
            lines.append(f"**{head}**")
        ranked_bullets, b_trace = _rank(
            [{"t": x} for x in e["bullets"]], jd_skills, jd_tokens,
            lambda x: x["t"],
        )
        bullet_traces.append(b_trace)
        for x in ranked_bullets:
            lines.append("- " + x["t"])
        lines.append("")

    if s["projects"]:
        ranked_proj, _ = _rank(
            s["projects"], jd_skills, jd_tokens,
            lambda p: " ".join([p["name"], p["role"], p["description"], *p["bullets"]]),
        )
        lines.append("## " + L["projects"])
        for p in ranked_proj:
            head = " | ".join(x for x in (p["name"], p["role"], p["period"]) if x)
            if head:
                lines.append(f"**{head}**")
            if p["description"]:
                lines.append(p["description"])
            for x in p["bullets"]:
                lines.append("- " + x)
            lines.append("")

    if s["education"]:
        lines.append("## " + L["education"])
        for e in s["education"]:
            lines.append(" | ".join(x for x in (
                e["school"], e["degree"], e["major"], rs._period(e["start"], e["end"])
            ) if x))
        lines.append("")
    if s["certifications"]:
        lines += ["## " + L["certifications"], "、".join(s["certifications"]), ""]
    if s["languages"]:
        lines += ["## " + L["languages"], "、".join(s["languages"]), ""]
    for extra in s["extras"]:
        if extra["lines"]:
            lines.append(f"## {extra['heading']}")
            lines += extra["lines"]
            lines.append("")

    lines += ["---", L["note"]]
    content = "\n".join(lines).strip() + "\n"
    trace = {
        "method": "rule", "language": language,
        "operations": [
            "按岗位相关度重排工作经历条目（文字未改动）",
            "按岗位相关度重排每条经历下的要点（文字未改动）",
            "按岗位相关度分组核心技能（未新增技能）",
            "引用原始简历中的个人简介、项目、教育与证书（逐字）",
        ],
        "experience_reordered": exp_trace["reordered"],
        "experience_new_order": exp_trace["new_order"],
        "bullets_reordered": any(t["reordered"] for t in bullet_traces),
        "skills_highlighted": highlight, "skills_untouched": rest,
        "facts_added": [],
    }
    return content, trace


# ------------------------------------------------------------- cover letter
def cover_letter(structured: dict, job: dict, match: dict,
                 ledger: dict | None = None,
                 language: str | None = None) -> tuple[str, dict]:
    ledger = ledger or build_fact_ledger(structured, job)
    language = language or letter_language(job)
    s = ledger["structured"]
    b = s["basics"]
    jd_skills = match.get("jd_skills") or []
    jd_tokens = sk.content_tokens(
        (job.get("title") or "") + " " + (job.get("description") or "")
    )
    matched = match.get("matched_skills") or []
    evidence, project, project_lines = _evidence(s, jd_skills, jd_tokens)
    company = job.get("company") or ("the hiring team" if language == "en" else "贵司")
    title = job.get("title") or ("this position" if language == "en" else "该职位")
    name = b["name"] or ("Applicant" if language == "en" else "（简历未填写姓名）")
    contact = " | ".join(x for x in (
        b["location"], b["phone"], b["email"], *b["links"]
    ) if x)
    quoted = [item for _, _, item in evidence] + project_lines

    if language == "en":
        lines: list[str] = [name]
        if contact:
            lines.append(contact)
        lines += ["", f"Re: {title} — {company}", "", "Dear Hiring Team,", "",
                  f"I am writing to apply for the {title} position at {company}."]
        if s["summary"]:
            lines += ["", s["summary"]]
        if evidence:
            lines += ["", "My most relevant experience includes:"]
            for _, _, item in evidence[:3]:
                lines.append(f"  - {item}")
        if matched:
            lines += ["", "The skills in my resume that overlap with your requirements are "
                      + ", ".join(matched[:10]) + ". These are tools and methods I have used "
                      "in the work and projects described above, not a list of aspirations."]
        if project and project_lines:
            head = " | ".join(x for x in (project["name"], project["role"], project["period"]) if x)
            lines += ["", f"A concrete example of how I work is {head}:"]
            if project["description"]:
                lines.append(f"  {project['description']}")
            for item in project_lines[:2]:
                lines.append(f"  - {item}")
        lines += [
            "",
            "I would welcome the opportunity to discuss how this background fits your team. "
            "Every experience, skill and figure above is taken from my original resume, which "
            "I have attached for your reference; I have not added anything beyond it.",
            "", "Sincerely,", name,
        ]
    else:
        lines = [f"应聘人：{name}"]
        if contact:
            lines.append("联系方式：" + contact)
        lines += ["", f"应聘职位：{title} · {company}", "", f"尊敬的 {company} 招聘团队：", "",
                  f"我应聘贵司发布的「{title}」职位。"]
        if s["summary"]:
            lines += ["", s["summary"]]
        if evidence:
            lines += ["", "与岗位最相关的经历如下："]
            for _, _, item in evidence[:3]:
                lines.append(f"  · {item}")
        if matched:
            lines += ["", "岗位要求中提到的 " + "、".join(matched[:10]) +
                      "，均属于我简历中列明、并在上述工作中实际使用的技能。"]
        if project and project_lines:
            head = " | ".join(x for x in (project["name"], project["role"], project["period"]) if x)
            lines += ["", f"以「{head}」项目为例，可说明我的工作方式："]
            if project["description"]:
                lines.append(f"  {project['description']}")
            for item in project_lines[:2]:
                lines.append(f"  · {item}")
        lines += [
            "",
            "以上经历、技能与数据均可在随附的原始简历中逐条核对，我没有添加简历之外的经历。"
            "期待有机会进一步沟通，感谢您审阅我的申请。",
            "", "此致", "敬礼", "", name,
        ]

    content = "\n".join(lines).strip() + "\n"
    trace = {
        "method": "rule", "language": language,
        "operations": ["按相关度选取真实经历", "逐字引用原始简历要点", "仅列出真实技能交集",
                       "按岗位语言输出：" + ("English" if language == "en" else "中文")],
        "quoted_bullets": quoted,
        "facts_added": [],
        "external_facts_used": [f"岗位名称：{title}", f"公司名称：{company}"],
    }
    return content, trace


# ------------------------------------------------------------- orchestration
def generate_both(structured: dict, job: dict, match: dict,
                  settings: dict | None = None) -> dict[str, dict]:
    settings = settings or db.get_settings()
    ledger = build_fact_ledger(structured, job)
    language = letter_language(job, settings.get("letter_language") or "auto")
    rule_resume, rule_resume_trace = tailored_resume(
        structured, job, match, ledger, language
    )
    rule_letter, rule_letter_trace = cover_letter(
        structured, job, match, ledger, language
    )

    result = {
        "resume": {
            "content": rule_resume, "engine": "rule", "language": language,
            "trace": rule_resume_trace, "validation": validate_text(rule_resume, ledger),
        },
        "cover_letter": {
            "content": rule_letter, "engine": "rule", "language": language,
            "trace": rule_letter_trace, "validation": validate_text(rule_letter, ledger),
        },
    }

    if settings.get("llm_enabled") and settings.get("llm_api_key"):
        try:
            generated = llm.generate(
                settings=settings, structured=ledger["structured"],
                job=job, match=match, ledger=ledger,
                rule_resume=rule_resume, rule_letter=rule_letter,
                language=language,
            )
            for kind, payload in (("resume", generated.get("resume")),
                                  ("cover_letter", generated.get("cover_letter"))):
                if not isinstance(payload, str) or len(payload) < 80:
                    continue
                check = validate_text(payload, ledger)
                if check["passed"]:
                    result[kind] = {
                        "content": payload.strip() + "\n",
                        "engine": "llm", "language": language,
                        "trace": {
                            "method": "llm", "language": language,
                            "operations": ["大模型在事实白名单内润色措辞",
                                           "输出已通过数字／技能事实校验"],
                            "facts_added": [],
                        },
                        "validation": check,
                    }
                else:
                    result[kind]["trace"]["llm_rejected"] = check["violations"]
                    result[kind]["validation"]["fallback_reason"] = (
                        "大模型输出未通过事实校验，已自动回退到规则引擎版本："
                        + "；".join(check["violations"][:3])
                    )
        except Exception as exc:  # noqa: BLE001 - LLM is optional, never fatal
            for kind in ("resume", "cover_letter"):
                result[kind]["validation"]["fallback_reason"] = (
                    f"大模型调用失败，已使用规则引擎版本：{type(exc).__name__}: {exc}"[:300]
                )
    return result
