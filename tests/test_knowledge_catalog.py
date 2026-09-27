"""细粒度知识点目录构建测试。"""

from edu_core.application.knowledge_catalog import build_catalog


def test_groups_by_subject_and_counts_by_status():
    rows = [
        {"subject": "数学", "knowledge_point": "绝对值", "status": "published", "n": 3},
        {"subject": "数学", "knowledge_point": "绝对值", "status": "draft", "n": 1},
        {"subject": "数学", "knowledge_point": "相反数", "status": "published", "n": 2},
        {"subject": "化学", "knowledge_point": "氧气的性质", "status": "published", "n": 5},
    ]
    catalog = build_catalog(rows)
    assert catalog["total_points"] == 3
    subjects = {item["subject"]: item for item in catalog["subjects"]}
    # 化学仅 1 点 5 题，数学 2 点 6 题 → 数学按题量降序在前
    assert [item["subject"] for item in catalog["subjects"]] == ["数学", "化学"]
    math_points = subjects["数学"]["points"]
    assert math_points[0]["knowledge_point"] == "绝对值" and math_points[0]["published"] == 3
    assert math_points[0]["draft"] == 1
    assert math_points[1]["knowledge_point"] == "相反数"


def test_skips_blank_points_and_keeps_unknown_status():
    rows = [
        {"subject": "生物", "knowledge_point": "", "status": "published", "n": 2},
        {"subject": "生物", "knowledge_point": "  ", "status": "draft", "n": 1},
        {"subject": "生物", "knowledge_point": "细胞", "status": "pending_review", "n": 4},
    ]
    catalog = build_catalog(rows)
    assert catalog["total_points"] == 1
    point = catalog["subjects"][0]["points"][0]
    assert point["published"] == 0 and point["draft"] == 0 and point["pending_review"] == 4


def test_empty_input():
    assert build_catalog([]) == {"subjects": [], "total_points": 0}
