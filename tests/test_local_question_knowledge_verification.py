from edu_core.config.settings import Settings
from edu_core.rag.retrieval import RetrievalFilters, RagRetrievalService


class LocalStore:
    def list_local_question_knowledge(self, **_kwargs):
        return [{"question_id": 12, "question_text": "已知函数 f(x)=x+1，求 f(2) 的值。",
                 "answer": "3", "analysis": "代入计算", "subject": "数学", "grade_band": "初中", "grade": "初三",
                 "knowledge_point": "数学::一次函数", "question_type": "解答题"}]


def test_local_question_knowledge_query_is_exact_and_token_free():
    service = RagRetrievalService(LocalStore(), Settings(_env_file=None))
    hits = service.local_question_candidates("已知函数 f(x)=x+1，求 f(2) 的值。",
                                             filters=RetrievalFilters(subject="数学", grade_band="初中", grade="初三"))
    assert hits[0]["question_id"] == 12
    assert hits[0]["score"] == 1.0
