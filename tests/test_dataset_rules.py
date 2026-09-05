"""数据层纯逻辑测试：题型规则推断 / 清洗 / 去重 / 小类合并 / 标签构建 / 分层划分。"""

from edu_core.data.dataset import (
    build_labels, clean_text, dedup, infer_question_type,
    merge_small_knowledge, merge_small_types, stratified_split,
)


def test_infer_question_type():
    # 客观题：含选项 -> 选择题；无选项 -> 判断题
    assert infer_question_type("客观题", "下列正确的是（ ）\n A、选项一 B、选项二") == "选择题"
    assert infer_question_type("客观题", "地球是圆的。（ ）") == "判断题"
    # 主观题关键词
    assert infer_question_type("主观题", "请____空白处。") == "填空题"
    assert infer_question_type("主观题", "求证：三角形内角和为180度。") == "证明题"
    assert infer_question_type("主观题", "简述光合作用的过程。") == "解答题"


def test_clean_text():
    assert clean_text("  a \r\n\n b  ") == "a\nb"
    assert clean_text("") == ""
    assert clean_text(None) == ""


def test_dedup():
    samples = [{"text": "x"}, {"text": "x"}, {"text": "y"}]
    assert len(dedup(samples)) == 2


def test_merge_small_knowledge():
    samples = [{"knowledge_point": "数学::代数"}] * 5 + [{"knowledge_point": "数学::几何"}] * 20
    merged = merge_small_knowledge(samples, min_count=10)
    assert all(s["knowledge_point"] == "数学::其他" for s in merged[:5])
    assert merged[5]["knowledge_point"] == "数学::几何"


def test_merge_small_types():
    samples = [{"question_type": "填空题"}] * 3 + [{"question_type": "选择题"}] * 60
    merged = merge_small_types(samples, min_count=50)
    assert merged[0]["question_type"] == "解答题"
    assert merged[3]["question_type"] == "选择题"


def test_build_labels_and_split():
    samples = [{"text": f"t{i}", "subject": s, "question_type": "选择题",
                "knowledge_point": f"{s}::k", "answer": ""} for i, s in enumerate(["数学", "语文"] * 50)]
    train, val, test = stratified_split(samples)
    assert len(train) + len(val) + len(test) == len(samples)
    # 分层：两个学科都应出现在训练集
    subjects_in_train = {s["subject"] for s in train}
    assert subjects_in_train == {"数学", "语文"}

    labels = build_labels(train, samples)
    assert labels["subjects"] == ["数学", "语文"]
    assert labels["subject2id"]["数学"] == labels["subjects"].index("数学")
    assert labels["stats"]["n_samples"] == len(samples)
