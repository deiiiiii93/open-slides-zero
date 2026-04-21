"""Business scenario catalog.

Each scenario maps to the rhetorical structures that best fit it. The outline_creator
uses scenario + (human-chosen) structure to pick the right slide_mix.
"""

from typing import Any

SCENARIO_DEFINITIONS: list[dict[str, Any]] = [
    {
        "id": "executive_decision",
        "name_en": "Executive Decision Briefing",
        "name_zh": "高管决策汇报",
        "structures": ["pyramid", "bluf"],
    },
    {
        "id": "problem_diagnosis",
        "name_en": "Problem Diagnosis & Recommendation",
        "name_zh": "问题诊断与方案",
        "structures": ["scqa", "problem_solution"],
    },
    {
        "id": "project_review",
        "name_en": "Project Review & Planning",
        "name_zh": "项目复盘与规划",
        "structures": ["chronological", "fivew2h"],
    },
    {
        "id": "tech_data_insight",
        "name_en": "Tech/Data Insight Report",
        "name_zh": "技术/数据洞察汇报",
        "structures": ["what_so_what_now_what"],
    },
    {
        "id": "sales_pitch",
        "name_en": "Sales Pitch & Conversion",
        "name_zh": "销售转化与提案",
        "structures": ["pas", "aida"],
    },
    {
        "id": "vision_keynote",
        "name_en": "Vision Keynote",
        "name_zh": "愿景宣讲/发布",
        "structures": ["hero_journey"],
    },
]


def get_scenario(scenario_id: str) -> dict[str, Any]:
    for s in SCENARIO_DEFINITIONS:
        if s["id"] == scenario_id:
            return s
    raise KeyError(f"Unknown scenario_id: {scenario_id}")


def structures_for(scenario_id: str) -> list[str]:
    return get_scenario(scenario_id)["structures"]
