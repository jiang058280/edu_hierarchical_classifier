from edu_core.application.analytics import reteach_items


def test_reteach_requires_enough_evidence_and_low_correct_rate():
    rows = [{"knowledge_point": "函数", "attempts": 5, "correct_rate": .4},
            {"knowledge_point": "几何", "attempts": 2, "correct_rate": .1},
            {"knowledge_point": "方程", "attempts": 4, "correct_rate": .75}]
    result = reteach_items(rows)
    assert [item["knowledge_point"] for item in result] == ["函数"]
    assert result[0]["error_rate"] == .6
