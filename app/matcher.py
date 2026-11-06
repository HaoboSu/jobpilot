"""Explainable job/resume matching.

Every point of the score is attributed to a named dimension so the user can see
*why* a job was recommended. No dimension uses invented resume facts.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from . import db
from . import skills as sk
from . import resume as rs

DIMENSIONS: list[tuple[str, str, int]] = [
    ("skill_coverage", "技能覆盖", 40),
    ("experience", "经验年限", 15),
    ("title_fit", "职位方向", 15),
    ("domain_fit", "领域相关", 15),
    ("education", "教育背景", 5),
    ("location", "地点与工作方式", 10),
]


def _parse_date(value: str) -> date | None:
    v = (value or "").strip()[:19]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    m = re.search(r"((?:19|20)\d{2})-(\d{1,2})-(\d{1,2})", v)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def _job_skills(job: dict) -> list[str]:
    blob = " ".join(str(job.get(k) or "") for k in ("title", "description", "salary_text"))
    return sk.extract_skills(blob)


def evaluate(job: dict, structured: dict, settings: dict | None = None) -> dict:
    settings = settings or db.get_settings()
    s = rs.normalize_structured(structured)
    jd_text = " ".join(str(job.get(k) or "") for k in ("title", "description"))
    resume_text = rs.resume_plain_text(s)
    resume_skills = rs.canonical_skills(s)
    resume_skill_set = {x.lower() for x in resume_skills}
    jd_skills = _job_skills(job)

    breakdown: dict[str, dict] = {}
    reasons: list[str] = []
    adjustments: list[dict] = []

    # 1. Skill coverage ----------------------------------------------------
    matched = [x for x in jd_skills if x.lower() in resume_skill_set]
    missing = [x for x in jd_skills if x.lower() not in resume_skill_set]
    if jd_skills:
        ratio = len(matched) / len(jd_skills)
        skill_score = 40 * ratio
        skill_detail = f"命中 {len(matched)}/{len(jd_skills)} 项岗位技能"
    else:
        # No recognisable skill tokens in the JD: fall back to a conservative
        # lexical overlap rather than pretending skills matched.
        ratio = sk.overlap_ratio(sk.content_tokens(jd_text), sk.content_tokens(resume_text))
        skill_score = 40 * min(1.0, ratio * 1.5) * 0.7
        skill_detail = "岗位未列出可识别技能，按文本相关度保守估分"
    breakdown["skill_coverage"] = {
        "score": round(skill_score, 1), "max": 40, "detail": skill_detail,
    }
    if matched:
        reasons.append("命中岗位技能：" + "、".join(matched[:8]) + ("等" if len(matched) > 8 else ""))
    if missing:
        reasons.append("岗位提到但简历未体现：" + "、".join(missing[:6]) + ("等" if len(missing) > 6 else ""))

    # 2. Experience years --------------------------------------------------
    years = rs.years_of_experience(s)
    required = sk.extract_required_years(job.get("description") or "")
    if required is None:
        exp_score, exp_detail = 11.0, f"岗位未明确年限要求；简历可核算工作年限 {years} 年"
    elif years >= required:
        exp_score = 15.0
        exp_detail = f"简历工作年限 {years} 年，满足岗位约 {required:g} 年要求"
    else:
        exp_score = 15.0 * (years / max(0.5, required))
        exp_detail = f"简历工作年限 {years} 年，低于岗位约 {required:g} 年要求"
    breakdown["experience"] = {
        "score": round(exp_score, 1), "max": 15, "detail": exp_detail,
    }
    reasons.append(exp_detail)

    # 3. Title fit ----------------------------------------------------------
    resume_titles = " ".join(
        [s["basics"]["headline"]] + [e["title"] for e in s["experiences"]]
    )
    jd_title_tokens = sk.content_tokens(job.get("title") or "")
    title_tokens = sk.content_tokens(resume_titles)
    title_ratio = sk.overlap_ratio(jd_title_tokens, title_tokens)
    title_score = 15 * min(1.0, title_ratio * 1.25)
    breakdown["title_fit"] = {
        "score": round(title_score, 1), "max": 15,
        "detail": f"岗位头衔与简历过往职位的重合度 {title_ratio:.0%}",
    }
    if title_ratio >= 0.5:
        reasons.append("岗位方向与简历过往职位方向相近")

    # 4. Domain relevance ---------------------------------------------------
    jd_tokens = sk.content_tokens(jd_text)
    resume_tokens = sk.content_tokens(resume_text)
    domain_ratio = sk.overlap_ratio(jd_tokens, resume_tokens)
    domain_score = 15 * min(1.0, domain_ratio * 2.4)
    breakdown["domain_fit"] = {
        "score": round(domain_score, 1), "max": 15,
        "detail": f"岗位描述用语在简历中的覆盖率 {domain_ratio:.0%}",
    }

    # 5. Education ----------------------------------------------------------
    edu_text = " ".join(
        " ".join(str(e.get(k) or "") for k in ("school", "degree", "major"))
        for e in s["education"]
    )
    edu_ok = bool(edu_text.strip())
    asks_edu = bool(re.search(r"本科|硕士|学历|degree|bachelor|master", jd_text, re.I))
    if asks_edu and edu_ok:
        edu_score, edu_detail = 5.0, "岗位有学历要求，简历填写了教育背景"
    elif edu_ok:
        edu_score, edu_detail = 4.0, "简历填写了教育背景"
    else:
        edu_score, edu_detail = 1.0, "简历未填写教育背景"
    breakdown["education"] = {"score": edu_score, "max": 5, "detail": edu_detail}

    # 6. Location / work mode ----------------------------------------------
    from . import sources
    resume_loc = s["basics"]["location"]
    wanted_loc = str(settings.get("location") or "").strip()
    job_loc = str(job.get("location") or "")
    _, region_terms = sources.parse_location_queries(wanted_loc)
    region_mode = str(settings.get("region_mode") or "prefer")
    region_ok = sources.region_matches(job_loc, region_terms)
    is_remote = bool(job.get("remote")) or bool(re.search(r"remote|远程", job_loc, re.I))
    resume_hit = bool(resume_loc and resume_loc in job_loc)
    if not region_terms:
        loc_score, loc_detail = (9.0 if resume_hit else 6.0), "未设置首选地区，按简历所在地判断"
    elif region_ok:
        loc_score, loc_detail = 10.0, f"符合首选地区（{wanted_loc[:24]}）"
    elif is_remote:
        loc_score, loc_detail = 8.0, "岗位支持远程，不受地区限制"
    elif not job_loc:
        loc_score, loc_detail = 6.0, "岗位未标明地点"
    elif region_mode == "off":
        loc_score, loc_detail = 5.0, "已关闭地区偏好"
    else:
        loc_score, loc_detail = 0.0, f"岗位地点（{job_loc[:24]}）不在首选地区"
    breakdown["location"] = {"score": loc_score, "max": 10, "detail": loc_detail}
    if loc_score >= 8:
        reasons.append(loc_detail)
    elif loc_score == 0:
        reasons.append("地区不符合首选范围：" + loc_detail)

    total = sum(v["score"] for v in breakdown.values())

    # Adjustments -----------------------------------------------------------
    posted = _parse_date(str(job.get("posted_at") or ""))
    if posted:
        age = (date.today() - posted).days
        if age <= 3:
            adjustments.append({"name": "发布时间新鲜", "delta": 3, "detail": f"{age} 天内发布"})
        elif age > 30:
            adjustments.append({"name": "发布时间较早", "delta": -3, "detail": f"发布于 {age} 天前"})
    required_skills = [str(x).strip() for x in (settings.get("required_skills") or []) if str(x).strip()]
    if required_skills:
        missing_required = [x for x in required_skills
                            if not any(x.lower() in m.lower() or m.lower() in x.lower() for m in matched)]
        if missing_required:
            adjustments.append({
                "name": "必备技能缺失", "delta": -6,
                "detail": "未命中：" + "、".join(missing_required[:4]),
            })
    if settings.get("remote_only") and not is_remote:
        adjustments.append({"name": "非远程岗位", "delta": -8, "detail": "监控条件要求仅远程岗位"})
    if (region_mode == "prefer" and region_terms and not region_ok
            and not is_remote and job_loc):
        adjustments.append({
            "name": "地区不符合首选", "delta": -12,
            "detail": f"{job_loc[:24]} 不在 {wanted_loc[:24]} 范围内",
        })

    for adj in adjustments:
        total += adj["delta"]
    breakdown["adjustments"] = adjustments
    total = max(0.0, min(100.0, total))

    # Hard exclusions -------------------------------------------------------
    excluded = False
    exclude_list = [str(x).strip().lower() for x in (settings.get("exclude_companies") or []) if str(x).strip()]
    company = str(job.get("company") or "").lower()
    if exclude_list and any(x in company for x in exclude_list):
        excluded = True
        total = 0.0
        reasons.append("公司在排除名单中，已过滤")
    if (region_mode == "strict" and region_terms and not region_ok
            and not is_remote and job_loc and not excluded):
        excluded = True
        total = 0.0
        reasons.append(f"岗位地点（{job_loc[:24]}）不在首选地区，严格模式下已过滤")

    breakdown["total"] = round(total, 1)
    return {
        "score": round(total, 1),
        "breakdown": breakdown,
        "matched_skills": matched,
        "missing_skills": missing,
        "reasons": reasons,
        "excluded": excluded,
        "jd_skills": jd_skills,
        "resume_years": years,
        "required_years": required,
    }


def evaluate_all(structured: dict, settings: dict | None = None,
                 jobs: list[dict] | None = None) -> list[dict]:
    """Evaluate a batch of jobs against one resume version."""
    settings = settings or db.get_settings()
    rows = jobs if jobs is not None else db.list_jobs(limit=1000)
    out = []
    for job in rows:
        result = evaluate(job, structured, settings)
        result["job"] = job
        out.append(result)
    out.sort(key=lambda x: -x["score"])
    return out
