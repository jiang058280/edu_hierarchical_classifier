from edu_core.application.entry_annotation import (
    extract_embedded_metadata,
    normalize_knowledge_point,
    suggest_difficulty,
)


def test_extract_flattened_question_metadata_and_clean_content():
    result = extract_embedded_metadata(
        "1. -3的绝对值是（ ） A.-3 B.3 C.1/3 D.-1/3 考查知识点：数学::绝对值的意义 答案：B 解析：|-3|=3。 难度：1 学段：初中",
        subject="数学",
    )
    assert result["text"].endswith("D.-1/3")
    assert result["metadata"] == {
        "knowledge_point": "绝对值的意义", "answer": "B", "analysis": "|-3|=3。",
        "difficulty": 1, "grade_band": "初中",
    }


def test_normalize_knowledge_point_removes_internal_prefixes():
    assert normalize_knowledge_point("数学::数学::三角函数", "数学") == "三角函数"
    assert normalize_knowledge_point("数学：三角函数", "数学") == "三角函数"


def test_difficulty_suggestion_is_local_and_bounded():
    assert suggest_difficulty("下列说法正确的是（ ）", "选择题") == 1
    assert suggest_difficulty("阅读下列材料，综合探究并证明结论。", "解答题") >= 4


def test_preannotation_prefers_embedded_fields_without_extra_model_calls():
    from edu_core.api.teacher import _preannotate

    class Predictor:
        def predict(self, text):
            assert "考查知识点" not in text
            return {"subject": "数学", "question_type": "选择题", "knowledge_point": "数学::三角函数",
                    "grade_band": "高中", "confidence": {"subject": .8, "question_type": .8, "knowledge_point": .2}}

    result = _preannotate("1. 求值（ ） 考查知识点：数学::绝对值 答案：B 难度：2", type("Service", (), {"predictor": Predictor()})())
    assert result["text"] == "1. 求值（ ）"
    assert result["knowledge_point"] == "绝对值"
    assert result["answer"] == "B"
    assert result["difficulty"] == 2
    assert result["confidences"]["knowledge_point"] == 1.0
