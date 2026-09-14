from edu_core.application.consolidation import ordered_by_difficulty, unique_ids


def test_consolidation_ids_keep_source_priority_and_deduplicate():
    assert unique_ids([3, 1], [1, 2], [2, 4], limit=3) == [3, 1, 2]


def test_weak_focus_orders_questions_from_easy_to_hard():
    items = [{"id": 2, "difficulty": 3}, {"id": 1, "difficulty": 1}, {"id": 3, "difficulty": 2}]
    assert [item["id"] for item in ordered_by_difficulty(items)] == [1, 3, 2]
