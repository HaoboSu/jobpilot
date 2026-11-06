"""Demonstration data.

IMPORTANT: everything here is explicitly labelled as SAMPLE data in the UI.
It is only used so a first-time user can see the whole pipeline working before
they import their own resume. It is never presented as the user's real history.
"""
from __future__ import annotations

SAMPLE_RESUME: dict = {
    "basics": {
        "name": "林知远（示例人物）",
        "email": "sample.person@example.com",
        "phone": "138-0000-0000",
        "location": "上海",
        "headline": "数据分析师｜零售与电商数据方向",
        "links": ["https://www.linkedin.com/in/sample-person"],
    },
    "summary": (
        "多年零售与电商行业数据分析经验，负责经营指标体系搭建、用户行为分析与"
        "自动化报表交付，习惯以数据结论推动业务决策。"
    ),
    "skills": [
        "Python", "SQL", "Pandas", "MySQL", "ClickHouse", "ETL",
        "数据仓库", "数据建模", "Tableau", "Power BI", "FineBI", "数据可视化",
        "BI", "数据分析", "数据统计", "AB测试", "报表开发", "Excel",
        "Airflow", "Linux", "Git", "需求分析", "团队协作", "文档写作", "英语",
    ],
    "experiences": [
        {
            "company": "云栖零售科技（示例公司）",
            "title": "高级数据分析师",
            "start": "2022-04",
            "end": "至今",
            "location": "上海",
            "bullets": [
                "搭建门店经营指标体系，覆盖 12 个核心指标，支撑区域周度经营复盘。",
                "使用 Python 与 SQL 完成会员分群分析，输出分层运营建议并落地到 3 个区域试点。",
                "基于 ClickHouse 重构日报取数逻辑，报表产出时间由 2 小时缩短至 20 分钟。",
                "设计并落地 A/B 测试评估框架，统一实验指标口径与显著性判定流程。",
                "与产品、运营团队协作梳理埋点需求，维护数据字典与指标文档。",
            ],
        },
        {
            "company": "海帆电商（示例公司）",
            "title": "数据分析师",
            "start": "2020-07",
            "end": "2022-03",
            "location": "上海",
            "bullets": [
                "负责大促活动数据复盘，输出活动效果分析报告 20 余份。",
                "用 Tableau 搭建销售看板，替换原有人工 Excel 周报。",
                "清洗与整合多来源订单数据，建立统一的订单主题数据模型。",
                "通过漏斗分析定位结算环节流失，配合产品完成流程优化。",
            ],
        },
        {
            "company": "晨星咨询（示例公司）",
            "title": "数据分析实习生",
            "start": "2019-09",
            "end": "2020-06",
            "location": "上海",
            "bullets": [
                "使用 Excel 与 SPSS 完成消费者调研数据清洗与描述性统计。",
                "协助撰写行业研究报告中的图表与数据附录。",
            ],
        },
    ],
    "projects": [
        {
            "name": "会员生命周期分析项目（示例项目）",
            "role": "项目负责人",
            "period": "2023-03 ~ 2023-09",
            "description": "围绕会员活跃与复购构建分析框架，交付可视化看板与运营建议。",
            "bullets": [
                "定义新客、活跃、沉睡、流失四类状态及迁移口径。",
                "输出月度会员健康度看板，供运营团队定期复盘使用。",
            ],
        },
        {
            "name": "自动化报表管道（示例项目）",
            "role": "主要开发",
            "period": "2022-08 ~ 2022-12",
            "description": "将重复性取数与报表生成流程脚本化。",
            "bullets": [
                "使用 Python 与 Airflow 编排每日取数任务。",
                "编写数据质量校验规则，异常时自动告警。",
            ],
        },
    ],
    "education": [
        {
            "school": "华东某高校（示例）",
            "degree": "本科",
            "major": "统计学",
            "start": "2015-09",
            "end": "2019-06",
        }
    ],
    "certifications": ["英语 CET-6", "Tableau Desktop Specialist（示例）"],
    "languages": ["中文（母语）", "英语（读写熟练）"],
    "extras": [
        {"heading": "其他", "lines": ["偏好数据驱动的业务分析团队，接受跨部门协作型岗位。"]}
    ],
    "_sample": True,
}

# A deliberately varied set: some high match, some medium, some low, so the
# ranking / threshold behaviour is visible immediately.
DEMO_JOBS: list[dict] = [
    {
        "external_id": "demo-1001",
        "source": "demo",
        "title": "高级数据分析师（经营分析方向）",
        "company": "澜图科技（演示职位）",
        "location": "上海",
        "url": "https://www.linkedin.com/jobs/view/demo-1001",
        "posted_at": "2026-11-04",
        "employment_type": "全职",
        "remote": False,
        "salary_text": "25-35K",
        "description": (
            "岗位职责：\n"
            "1. 负责业务经营指标体系搭建与日常监控，输出经营分析报告；\n"
            "2. 使用 SQL 与 Python 完成用户行为分析、会员分群与漏斗分析；\n"
            "3. 基于 BI 工具搭建数据可视化看板，推动报表自动化；\n"
            "4. 与产品、运营跨团队协作，梳理需求并设计 A/B 测试评估方案。\n\n"
            "任职要求：\n"
            "1. 本科及以上学历，统计学、数学或相关专业，3-5年数据分析经验；\n"
            "2. 熟练使用 SQL、Python、Pandas，熟悉 ClickHouse 或 Hive 等数据仓库组件；\n"
            "3. 熟悉 Tableau / Power BI，具备数据建模与 ETL 经验；\n"
            "4. 具备良好的沟通协调能力与文档写作能力，英语读写熟练者优先。\n"
            "加分项：有零售、电商行业经验，了解 Airflow 任务编排。"
        ),
    },
    {
        "external_id": "demo-1002",
        "source": "demo",
        "title": "BI 开发工程师 / 报表开发",
        "company": "启明数科（演示职位）",
        "location": "上海",
        "url": "https://www.linkedin.com/jobs/view/demo-1002",
        "posted_at": "2026-11-03",
        "employment_type": "全职",
        "remote": False,
        "salary_text": "20-30K",
        "description": (
            "岗位职责：负责企业级 BI 看板与报表开发，维护数据仓库主题模型，"
            "完成 ETL 流程开发与数据质量治理。\n"
            "任职要求：2年以上 BI 或报表开发经验；精通 SQL，熟悉 MySQL、ClickHouse；"
            "熟练使用 Tableau、Power BI 或 FineBI；掌握数据建模与维度建模；"
            "有 Python 脚本能力与 Airflow 调度经验优先；具备需求分析能力。"
        ),
    },
    {
        "external_id": "demo-1003",
        "source": "demo",
        "title": "Data Analyst, Growth",
        "company": "Northwind Commerce (demo job)",
        "location": "Shanghai, China",
        "url": "https://www.linkedin.com/jobs/view/demo-1003",
        "posted_at": "2026-11-05",
        "employment_type": "Full-time",
        "remote": True,
        "salary_text": "",
        "description": (
            "We are looking for a Data Analyst with 3+ years of experience in ecommerce analytics. "
            "You will build dashboards in Tableau, run A/B tests, analyse user funnels and "
            "write SQL against a ClickHouse warehouse. Strong Python (Pandas) skills required. "
            "Experience with member segmentation, retention analysis and stakeholder "
            "communication is a plus. Good written English needed."
        ),
    },
    {
        "external_id": "demo-1004",
        "source": "demo",
        "title": "数据产品经理（数据平台）",
        "company": "星河互联（演示职位）",
        "location": "上海",
        "url": "https://www.linkedin.com/jobs/view/demo-1004",
        "posted_at": "2026-11-02",
        "employment_type": "全职",
        "remote": False,
        "description": (
            "负责数据平台产品规划与需求分析，输出 PRD，推动指标体系建设。"
            "要求 4年以上产品经验，熟悉数据仓库、BI、数据治理，具备跨团队协作与"
            "项目管理能力。有 SQL 能力优先，熟悉敏捷开发流程。"
        ),
    },
    {
        "external_id": "demo-1005",
        "source": "demo",
        "title": "机器学习工程师（推荐算法）",
        "company": "深蓝智能（演示职位）",
        "location": "北京",
        "url": "https://www.linkedin.com/jobs/view/demo-1005",
        "posted_at": "2026-11-01",
        "employment_type": "全职",
        "remote": False,
        "description": (
            "负责推荐系统算法研发与线上效果优化。要求 5年以上机器学习经验，"
            "精通 PyTorch / TensorFlow、深度学习、特征工程，熟悉 Spark、Kafka、"
            "Docker、Kubernetes 与模型部署，有大规模推荐系统落地经验。"
        ),
    },
    {
        "external_id": "demo-1006",
        "source": "demo",
        "title": "商业分析师 / 经营分析",
        "company": "沐野消费（演示职位）",
        "location": "上海",
        "url": "https://www.linkedin.com/jobs/view/demo-1006",
        "posted_at": "2026-10-31",
        "employment_type": "全职",
        "remote": False,
        "description": (
            "负责零售业务经营分析、销售预测与预算跟踪，输出月度经营报告。"
            "要求 3年以上数据分析或财务分析经验，熟练 Excel、SQL、Power BI，"
            "具备业务梳理、报告撰写与跨部门沟通能力。有零售行业背景优先。"
        ),
    },
    {
        "external_id": "demo-1007",
        "source": "demo",
        "title": "Senior Backend Engineer (Java)",
        "company": "Vertex Systems (demo job)",
        "location": "Singapore",
        "url": "https://www.linkedin.com/jobs/view/demo-1007",
        "posted_at": "2026-10-30",
        "employment_type": "Full-time",
        "remote": True,
        "description": (
            "Design and build microservices with Java, Spring Boot and Kafka. "
            "Strong MySQL, Redis, Docker, Kubernetes and CI/CD experience required. "
            "8+ years of backend development, REST API design and unit testing."
        ),
    },
    {
        "external_id": "demo-1008",
        "source": "demo",
        "title": "用户增长运营（活动方向）",
        "company": "拾光生活（演示职位）",
        "location": "上海",
        "url": "https://www.linkedin.com/jobs/view/demo-1008",
        "posted_at": "2026-10-29",
        "employment_type": "全职",
        "remote": False,
        "description": (
            "负责用户增长活动策划与执行，提升拉新、留存与转化，进行竞品分析与"
            "内容运营。要求 2年以上增长运营经验，熟悉活动策划、SEO/SEM 与"
            "电商运营，具备数据分析能力者优先。"
        ),
    },
]
