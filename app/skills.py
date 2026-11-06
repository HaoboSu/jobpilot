"""Skill vocabulary and lightweight NLP helpers (pure stdlib).

Canonical skill names are used everywhere so a resume skill and a JD skill can
be compared without inventing anything. Only skills present in this dictionary
(or explicitly declared by the user in structured resume data) can be matched.
"""
from __future__ import annotations

import re

# canonical -> aliases (lower-case). Aliases are matched as substrings with
# word boundaries for ASCII and plain substring for CJK.
SKILL_ALIASES: dict[str, list[str]] = {
    "Python": ["python", "py3", "python3"],
    "SQL": ["sql", "结构化查询语言", "t-sql", "pl/sql"],
    "R": [" r语言", "r language", " r programming"],
    "Excel": ["excel", "电子表格", "vlookup", "数据透视表", "pivot table"],
    "VBA": ["vba"],
    "SPSS": ["spss"],
    "SAS": ["sas"],
    "Java": ["java"],
    "Scala": ["scala"],
    "Go": ["golang", "go语言"],
    "JavaScript": ["javascript", "js", "es6", "typescript", "ts"],
    "C++": ["c++", "cpp"],
    "C#": ["c#", "csharp", ".net", "dotnet"],
    "HTML/CSS": ["html", "css", "前端三件套"],
    "React": ["react", "reactjs", "react.js"],
    "Vue": ["vue", "vuejs", "vue.js"],
    "Node.js": ["node.js", "nodejs", "express"],
    "Django": ["django"],
    "Flask": ["flask"],
    "FastAPI": ["fastapi"],
    "Spring": ["spring boot", "springboot", "spring framework"],
    "MySQL": ["mysql"],
    "PostgreSQL": ["postgresql", "postgres"],
    "Oracle": ["oracle"],
    "MongoDB": ["mongodb", "mongo"],
    "Redis": ["redis"],
    "SQLite": ["sqlite"],
    "ClickHouse": ["clickhouse"],
    "Hive": ["hive"],
    "Spark": ["spark", "pyspark"],
    "Hadoop": ["hadoop"],
    "Flink": ["flink"],
    "Kafka": ["kafka"],
    "Airflow": ["airflow"],
    "DBT": ["dbt"],
    "ETL": ["etl", "数据抽取", "数据清洗", "数据仓库", "data warehouse"],
    "数据仓库": ["数据仓库", "data warehouse", "数仓", "维度建模", "星型模型"],
    "数据建模": ["数据建模", "data modeling", "dimensional modeling"],
    "Tableau": ["tableau"],
    "Power BI": ["power bi", "powerbi"],
    "FineBI": ["finebi", "帆软", "fine report", "finereport"],
    "Looker": ["looker"],
    "Metabase": ["metabase"],
    "ECharts": ["echarts"],
    "数据可视化": ["数据可视化", "data visualization", "可视化看板", "bi看板", "dashboard"],
    "BI": ["bi", "商业智能", "business intelligence"],
    "报表开发": ["报表", "reporting", "定期报告", "周报", "月报"],
    "数据分析": ["数据分析", "data analysis", "业务分析", "经营分析", "data analytics"],
    "数据统计": ["统计分析", "描述性统计", "statistics", "假设检验", "回归分析", "ab测试", "a/b test", "ab test"],
    "机器学习": ["机器学习", "machine learning", "ml模型", "特征工程"],
    "深度学习": ["深度学习", "deep learning", "神经网络"],
    "NLP": ["nlp", "自然语言处理", "文本挖掘"],
    "推荐系统": ["推荐系统", "recommendation system", "推荐算法"],
    "计算机视觉": ["计算机视觉", "computer vision", "图像识别", "cv"],
    "LLM": ["llm", "大模型", "大语言模型", "langchain", "prompt", "rag", "生成式ai"],
    "Pandas": ["pandas"],
    "NumPy": ["numpy"],
    "Scikit-learn": ["scikit-learn", "sklearn"],
    "PyTorch": ["pytorch"],
    "TensorFlow": ["tensorflow"],
    "Docker": ["docker", "容器化"],
    "Kubernetes": ["kubernetes", "k8s"],
    "Git": ["git", "github", "gitlab", "版本控制"],
    "Linux": ["linux", "shell", "bash", "unix"],
    "AWS": ["aws", "amazon web services", "s3", "redshift"],
    "Azure": ["azure"],
    "GCP": ["gcp", "google cloud", "bigquery"],
    "阿里云": ["阿里云", "aliyun", "maxcompute", "dataworks"],
    "腾讯云": ["腾讯云", "tencent cloud"],
    "CI/CD": ["ci/cd", "cicd", "jenkins", "github actions", "持续集成"],
    "微服务": ["微服务", "microservice"],
    "REST API": ["rest api", "restful", "api设计", "web api"],
    "GraphQL": ["graphql"],
    "单元测试": ["单元测试", "unit test", "pytest", "测试驱动"],
    "敏捷开发": ["敏捷", "agile", "scrum", "kanban", "迭代开发"],
    "项目管理": ["项目管理", "project management", "pmp", "项目交付", "里程碑"],
    "需求分析": ["需求分析", "requirements", "需求调研", "prd", "业务梳理"],
    "产品设计": ["产品设计", "product design", "交互设计", "原型", "axure", "figma", "sketch"],
    "用户研究": ["用户研究", "user research", "用户访谈", "可用性测试", "用户画像"],
    "竞品分析": ["竞品分析", "competitive analysis", "市场调研"],
    "增长运营": ["用户增长", "增长运营", "growth", "增长黑客", "用户留存", "留存分析"],
    "活动策划": ["活动策划", "活动运营", "营销活动", "campaign"],
    "内容运营": ["内容运营", "content marketing", "文案", "新媒体", "公众号"],
    "SEO/SEM": ["seo", "sem", "搜索优化", "信息流投放", "广告投放"],
    "CRM": ["crm", "salesforce", "客户关系管理"],
    "电商运营": ["电商运营", "ecommerce", "淘宝", "天猫", "京东", "店铺运营", "gmv"],
    "财务分析": ["财务分析", "financial analysis", "预算", "成本核算", "财务报表", "budget"],
    "投资分析": ["投资分析", "估值", "valuation", "尽职调查", "行业研究", "研报"],
    "风险管理": ["风险管理", "风控", "risk management", "合规", "内控"],
    "供应链": ["供应链", "supply chain", "库存管理", "采购", "物流"],
    "人力资源": ["人力资源", "hrbp", "绩效管理", "薪酬管理", "组织发展"],
    "团队协作": ["团队协作", "跨部门", "沟通协调", "stakeholder", "跨团队"],
    "英语": ["英语", "english", "cet-6", "雅思", "ielts", "托福", "toefl"],
    "文档写作": ["文档写作", "技术文档", "documentation", "报告撰写", "方案撰写", "文档撰写"],
    "数据治理": ["数据治理", "数据质量", "metadata", "元数据", "数据标准"],
    "信息安全": ["信息安全", "数据隐私", "gdpr", "安全合规"],
}

# sorted longest-first so "power bi" is not swallowed by "bi"
_SKILL_TERMS: list[tuple[str, str]] = []
for _canon, _aliases in SKILL_ALIASES.items():
    for _alias in _aliases:
        _SKILL_TERMS.append((_alias.strip().lower(), _canon))
_SKILL_TERMS.sort(key=lambda p: -len(p[0]))

_CJK = re.compile(r"[\u4e00-\u9fff]")
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?%?")
_YEAR_RE = re.compile(
    r"(?:(\d+)\s*[-~–至到]\s*(\d+)\s*年|(\d+)\s*年\s*(?:以上|\+|\+年)?|"
    r"(\d+)\s*\+?\s*years?|at\s+least\s+(\d+)\s*years?|(\d+)\s*\+?\s*yrs)",
    re.I,
)


def extract_skills(text: str) -> list[str]:
    """Return canonical skills mentioned in text, in first-appearance order."""
    if not text:
        return []
    low = text.lower()
    found: list[str] = []
    seen: set[str] = set()
    for alias, canon in _SKILL_TERMS:
        if canon in seen:
            continue
        if _CJK.search(alias):
            hit = alias in low
        else:
            # word-boundary aware for latin terms
            pattern = r"(?<![a-z0-9+#.])" + re.escape(alias) + r"(?![a-z0-9+#])"
            hit = re.search(pattern, low) is not None
        if hit:
            seen.add(canon)
            found.append(canon)
    return found


def extract_canonical_mentions(text: str) -> list[str]:
    """Find canonical skill NAMES mentioned in text (not loose aliases).

    Used by the fact checker: generated documents must only name skills that
    are declared in the resume. Matching canonical names (rather than every
    alias) avoids false positives from ordinary words such as '招聘团队'.
    """
    low = (text or "").lower()
    found: list[str] = []
    for name in SKILL_ALIASES:
        target = name.lower()
        if _CJK.search(target):
            hit = target in low
        else:
            pattern = r"(?<![a-z0-9+#.])" + re.escape(target) + r"(?![a-z0-9+#])"
            hit = re.search(pattern, low) is not None
        if hit:
            found.append(name)
    return found


def skill_positions(text: str, skills: list[str]) -> dict[str, int]:
    low = (text or "").lower()
    pos: dict[str, int] = {}
    for skill in skills:
        best = len(low) + 1
        for alias in SKILL_ALIASES.get(skill, [skill.lower()]):
            idx = low.find(alias.strip().lower())
            if 0 <= idx < best:
                best = idx
        pos[skill] = best
    return pos


def extract_required_years(text: str) -> float | None:
    """Parse an experience requirement from a JD, e.g. '3-5年' / '5+ years'."""
    if not text:
        return None
    m = _YEAR_RE.search(text)
    if not m:
        return None
    groups = m.groups()
    if groups[0] and groups[1]:
        return (int(groups[0]) + int(groups[1])) / 2
    for g in groups[2:]:
        if g:
            return float(g)
    return None


def extract_numbers(text: str) -> set[str]:
    return set(_NUMBER_RE.findall(text or ""))


def tokenize(text: str) -> list[str]:
    """Very small tokenizer for similarity: latin words + CJK bigrams."""
    low = (text or "").lower()
    words = re.findall(r"[a-z][a-z0-9+#.\-]{1,}", low)
    cjk = re.findall(r"[\u4e00-\u9fff]{2,}", low)
    grams: list[str] = []
    for chunk in cjk:
        for i in range(len(chunk) - 1):
            grams.append(chunk[i:i + 2])
    return words + grams


STOPWORDS = {
    "的", "和", "与", "及", "或", "在", "有", "是", "对", "为", "以", "等", "中",
    "我们", "你", "岗位", "职位", "工作", "任职", "要求", "职责", "优先", "熟悉",
    "熟练", "掌握", "具备", "负责", "相关", "以上", "经验", "能力", "以及", "能够",
    "the", "and", "or", "with", "for", "of", "to", "in", "a", "an", "our", "you",
    "we", "are", "is", "will", "have", "has", "years", "year", "experience",
    "role", "team", "work", "working", "skills", "required", "preferred", "about",
    "job", "position", "strong", "good", "plus", "etc", "including", "using",
}


def content_tokens(text: str) -> set[str]:
    return {t for t in tokenize(text) if t not in STOPWORDS and len(t) > 1}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def overlap_ratio(needles: set[str], haystack: set[str]) -> float:
    """How much of `needles` appears in `haystack`."""
    if not needles:
        return 0.0
    return len(needles & haystack) / len(needles)
