"""R3.3 语义召回单元测试：卡带过滤、加成打分、理由与降级。"""
import pytest

from edu_core.application.learning_service import LearningService
from edu_core.application.recommendation import (
    SemanticRecaller,
    clean_seed_text,
    score_candidate_semantic,
)
from edu_core.config.settings import Settings


def semantic_settings(**kwargs):
    return Settings(_env_file=None, recommend_semantic_enabled=True, **kwargs)


class FakeDedup:
    def __init__(self, canned: dict[str, list[dict]]):
        self.canned = canned
        self.calls: list[str] = []

    def search_similar(self, text, top_k=None, exclude_id=None):
        self.calls.append(text)
        return self.canned.get(text, [])


def test_similarity_map_band_filtering_and_seed_maximum():
    seed_a, seed_b = "seed A 的题干", "seed B 的题干"
    dedup = FakeDedup({
        seed_a: [{"question_id": 1, "similarity": 0.97},   # ≥ high：近似重复，排除
                 {"question_id": 2, "similarity": 0.78},   # 带内，保留
                 {"question_id": 3, "similarity": 0.40}],  # < low：噪声，排除
        seed_b: [{"question_id": 2, "similarity": 0.66},   # 同题再次命中：取更高分
                 {"question_id": 4, "similarity": 0.61}],
    })
    recaller = SemanticRecaller(FakeDedup({}), semantic_settings())
    recaller.dedup = dedup
    mapping = recaller.similarity_map([seed_a, seed_b], exclude_ids={900})
    assert set(mapping) == {2, 4}
    assert mapping[2]["similarity"] == 0.78
    assert mapping[4]["similarity"] == 0.61


def test_similarity_map_unavailable_index_returns_empty():
    class DownDedup:
        def search_similar(self, text, top_k=None, exclude_id=None):
            return []
    recaller = SemanticRecaller(DownDedup(), semantic_settings())
    assert recaller.similarity_map(["任意种子"], set()) == {}


def test_score_candidate_semantic_bonus_and_reason():
    score, reasons = score_candidate_semantic(
        0.5, ["同知识点"], 0.75, low=0.55, high=0.95, weight=0.12)
    assert score == pytest.approx(0.5 + 0.12 * (0.75 - 0.55) / 0.4, abs=1e-4)
    assert reasons[-1] == "与错题语义相关 75%"


def test_score_candidate_semantic_caps_at_one():
    score, _ = score_candidate_semantic(0.99, [], 0.9, low=0.55, high=0.95, weight=0.12)
    assert score == 1.0


def test_clean_seed_text_strips_paste_number_and_truncates():
    assert clean_seed_text("28. sin30°的值是（　　）").startswith("sin30°")
    assert len(clean_seed_text("长" * 500)) <= 200


class FakeStores:
    class Users:
        @staticmethod
        def get(student_id):
            return {"id": student_id, "grade_band": "初中"}

    class Learning:
        @staticmethod
        def recommendation_data(student_id, *, grade_band=""):
            profiles = [{"subject": "数学", "knowledge_point": "锐角三角函数",
                         "mastery_score": 0.3, "recommended_difficulty": 2}]
            questions = [
                {"id": 2, "subject": "数学", "knowledge_point": "锐角三角函数", "difficulty": 2},
                {"id": 7, "subject": "数学", "knowledge_point": "勾股定理", "difficulty": 2},
            ]
            return profiles, questions, set()

        @staticmethod
        def save_recommendation_run(student_id, profiles, candidates, selected):
            return 1

    users = Users()
    learning = Learning()


def test_recommend_applies_semantic_bonus_and_reason():
    service = LearningService(stores=FakeStores(), semantic=SemanticRecaller(
        FakeDedup({"sin30°的值是（　　）": [{"question_id": 7, "similarity": 0.82}]}),
        semantic_settings()))
    service.profile = lambda student_id: {"items": [], "weak_points": [], "total": 0}
    service.wrong_book = lambda student_id, **filters: [
        {"question_id": 900, "content": "28. sin30°的值是（　　）"}]

    result = service.recommend(2, limit=5)
    by_id = {item["id"]: item for item in result["items"]}
    assert "与错题语义相关 82%" in by_id[7]["reasons"]
    assert not any("语义相关" in reason for reason in by_id[2]["reasons"])
    # 规则分（弱项加权 + 跨知识点 0.35 + 难度适配 + 新颖度）0.7025，加成 0.12*(0.82-0.55)/0.4≈0.081
    assert by_id[7]["score"] == pytest.approx(0.7835, abs=1e-3)
    # 同薄弱知识点精确匹配仍主导排序——语义加成温和、规则主导
    assert by_id[2]["score"] > by_id[7]["score"]


def test_recommend_without_semantic_stays_pure_rule():
    service = LearningService(stores=FakeStores())
    service.profile = lambda student_id: {"items": [], "weak_points": [], "total": 0}
    result = service.recommend(2, limit=5)
    assert not any("语义相关" in reason for item in result["items"] for reason in item["reasons"])
