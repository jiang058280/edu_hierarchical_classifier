"""R3.1 学生错题本与自主练习服务。"""

from __future__ import annotations

from typing import Any

from edu_core.application.grading import grade_submission
from edu_core.application.mastery import profile_for_records
from edu_core.application.recommendation import (
    SemanticRecaller,
    clean_seed_text,
    score_candidate,
    score_candidate_semantic,
)
from edu_core.application.consolidation import ordered_by_difficulty, unique_ids
from edu_core.application.analytics import reteach_items


class LearningService:
    def __init__(self, stores, semantic: SemanticRecaller | None = None):
        self.stores = stores
        self.semantic = semantic

    def wrong_book(self, student_id: int, **filters) -> list[dict]:
        return self.stores.learning.list_wrong(student_id, **filters)

    def profile(self, student_id: int) -> dict:
        grouped: dict[tuple[str, str], list[dict]] = {}
        for row in self.stores.learning.mastery_records(student_id):
            grouped.setdefault((row["subject"] or "未分类", row["knowledge_point"]), []).append(row)
        items=[]
        for (subject, knowledge_point), records in grouped.items():
            items.append({"subject":subject,"knowledge_point":knowledge_point,**profile_for_records(records)})
        items.sort(key=lambda item:(item["mastery_score"],-item["attempt_count"]))
        self.stores.learning.save_mastery(student_id, items)
        return {"items":items,"weak_points":items[:5],"total":len(items)}

    def recommend(self, student_id: int, limit: int = 10) -> dict:
        self.profile(student_id)
        user = self.stores.users.get(student_id)
        if not user:
            raise ValueError("学生不存在")
        profiles, questions, seen = self.stores.learning.recommendation_data(
            student_id, grade_band=user.get("grade_band") or ""
        )
        candidates = []
        for profile in profiles:
            for question in questions:
                if question.get('subject') != profile.get('subject'):
                    continue
                score, reasons = score_candidate(profile, question, seen)
                candidates.append({**question, 'score': score, 'reasons': reasons, 'profile': profile['knowledge_point']})
        if self.semantic is not None and candidates:
            self._apply_semantic_bonus(student_id, candidates)
        candidates.sort(key=lambda item: item['score'], reverse=True)
        selected = []
        used = set()
        for item in candidates:
            if item['id'] not in used:
                selected.append(item)
                used.add(item['id'])
            if len(selected) >= limit:
                break
        run_id = self.stores.learning.save_recommendation_run(student_id, profiles, candidates[:100], selected)
        return {'run_id': run_id, 'items': selected, 'total': len(selected)}

    def _apply_semantic_bonus(self, student_id: int, candidates: list[dict]) -> None:
        """错题为种子取语义近邻，就地为候选加分并追加可解释理由；索引不可用时静默跳过。"""
        wrong = self.wrong_book(student_id)[:int(self.semantic.settings.recommend_semantic_seeds)]
        seeds = [clean_seed_text(item.get("content")) for item in wrong]
        seeds = [seed for seed in seeds if seed]
        if not seeds:
            return
        exclude_ids = {int(item["question_id"]) for item in wrong}
        sim_map = self.semantic.similarity_map(seeds, exclude_ids)
        if not sim_map:
            return
        settings = self.semantic.settings
        for item in candidates:
            info = sim_map.get(int(item["id"]))
            if not info:
                continue
            item["score"], item["reasons"] = score_candidate_semantic(
                float(item["score"]), list(item["reasons"]), info["similarity"],
                low=float(settings.recommend_semantic_low), high=float(settings.recommend_semantic_high),
                weight=float(settings.recommend_semantic_weight))

    def _student_and_profile(self, student_id: int) -> tuple[dict, dict]:
        user = self.stores.users.get(student_id)
        if not user:
            raise ValueError("学生不存在")
        return user, self.profile(student_id)

    def _questions_by_ids(self, ids: list[int]) -> list[dict]:
        return self.stores.learning.questions_for_practice(ids)

    def wrong_focus(self, student_id: int, question_id: int, *, limit: int = 5) -> dict:
        user, _ = self._student_and_profile(student_id)
        wrong = next((item for item in self.wrong_book(student_id) if item["question_id"] == question_id), None)
        if not wrong:
            raise ValueError("该题不在当前待巩固错题中")
        pool = self.stores.learning.practice_questions(subject=wrong["subject"], knowledge_point=wrong["knowledge_point"],
                                                       grade_band=user.get("grade_band") or "", limit=50)
        ids = [int(item["id"]) for item in ordered_by_difficulty(pool, wrong.get("difficulty")) if int(item["id"]) != question_id][:limit]
        plan_id = self.stores.learning.save_consolidation_plan(student_id, "wrong_focus",
            {"source_question_id": question_id, "knowledge_point": wrong["knowledge_point"]}, ids)
        return {"plan_id": plan_id, "plan_type": "wrong_focus", "title": "错题巩固", "items": self._questions_by_ids(ids),
                "message": "围绕同一知识点，按难度由易到难安排不同题目。"}

    def weak_focus(self, student_id: int, *, top_n: int = 3, per_point: int = 3) -> dict:
        user, profile = self._student_and_profile(student_id)
        ids: list[int] = []
        points = profile["weak_points"][:top_n]
        for point in points:
            pool = self.stores.learning.practice_questions(subject=point["subject"], knowledge_point=point["knowledge_point"],
                                                           grade_band=user.get("grade_band") or "", limit=50)
            ids.extend(int(item["id"]) for item in ordered_by_difficulty(pool, point["recommended_difficulty"])[:per_point])
        ids = unique_ids(ids, limit=top_n * per_point)
        plan_id = self.stores.learning.save_consolidation_plan(student_id, "weak_focus",
            {"weak_points": points, "top_n": top_n, "per_point": per_point}, ids)
        return {"plan_id": plan_id, "plan_type": "weak_focus", "title": "薄弱知识点专项", "items": self._questions_by_ids(ids),
                "message": "按薄弱知识点分组，题目在每组内由易到难排列。"}

    def daily_plan(self, student_id: int, *, limit: int = 10) -> dict:
        user, profile = self._student_and_profile(student_id)
        wrong_ids = [int(item["question_id"]) for item in self.wrong_book(student_id)[:max(1, limit // 3)]]
        recommended = self.recommend(student_id, limit=max(1, limit // 2))["items"]
        recommended_ids = [int(item["id"]) for item in recommended]
        review_ids = self.stores.learning.review_question_ids(student_id, grade_band=user.get("grade_band") or "", limit=limit)
        ids = unique_ids(wrong_ids, recommended_ids, review_ids, limit=limit)
        plan_id = self.stores.learning.save_consolidation_plan(student_id, "daily",
            {"weak_points": profile["weak_points"], "mix": {"wrong": wrong_ids, "new": recommended_ids, "review": review_ids}}, ids)
        return {"plan_id": plan_id, "plan_type": "daily", "title": "今日个性化练习", "items": self._questions_by_ids(ids),
                "message": "题单混合旧错题、薄弱点新题与间隔复习题，并自动去重。"}

    def coaching_context(self, student_id: int) -> dict:
        """R3.5 给问答使用的最小化、无身份信息学习摘要。"""
        profile = self.profile(student_id)
        points = [{key: item[key] for key in ("subject", "knowledge_point", "mastery_score", "attempt_count",
                                               "recent_correct_rate", "profile_confidence", "recommended_difficulty")}
                  for item in profile["weak_points"][:3]]
        return {"weak_points": points, "profile_count": profile["total"]}

    def recommendation_quality(self, student_id: int) -> dict:
        return self.stores.learning.recommendation_metrics(student_id)

    def class_analytics(self, class_id: int) -> dict:
        students = self.stores.users.list_by_class(class_id)
        profiles = []
        for student in students:
            profile = self.profile(int(student["id"]))
            profiles.append({"student_id": student["id"], "student_name": student.get("real_name") or student.get("username"),
                             "items": profile["items"]})
        stats = self.stores.learning.class_learning_stats(class_id)
        matrix_points = sorted({item["knowledge_point"] for profile in profiles for item in profile["items"]})[:12]
        matrix = []
        for profile in profiles:
            by_point = {item["knowledge_point"]: item for item in profile["items"]}
            matrix.append({"student_id": profile["student_id"], "student_name": profile["student_name"],
                           "scores": [{"knowledge_point": point, "mastery_score": by_point.get(point, {}).get("mastery_score"),
                                       "attempt_count": by_point.get(point, {}).get("attempt_count", 0)} for point in matrix_points]})
        return {"students": len(students), "completion": stats["completion"], "knowledge": stats["knowledge"],
                "reteach": reteach_items(stats["knowledge"]), "question_errors": stats["questions"],
                "matrix_points": matrix_points, "matrix": matrix}

    def resolve_wrong(self, student_id: int, question_id: int, *, resolved: bool, note: str | None = None) -> None:
        if not self.stores.learning.mark_resolved(student_id, question_id, resolved=resolved, note=note):
            raise ValueError("错题不存在或不属于当前学生")

    def practice_questions(self, student_id: int, *, subject: str = "", knowledge_point: str = "",
                           limit: int = 10) -> list[dict]:
        user = self.stores.users.get(student_id)
        if not user:
            raise ValueError("学生不存在")
        return self.stores.learning.practice_questions(subject=subject, knowledge_point=knowledge_point,
                                                       grade_band=user.get("grade_band") or "", limit=limit)

    def submit_practice(self, student_id: int, answers: list[dict[str, Any],], *, source: str = "practice") -> dict:
        if source not in {"practice", "wrong_redo", "wrong_focus", "weak_focus", "daily"}:
            raise ValueError("非法练习来源")
        if not isinstance(answers, list) or not answers or len(answers) > 30:
            raise ValueError("练习答案数量需在 1~30 之间")
        ids = []
        answer_by_id: dict[int, str | None] = {}
        for item in answers:
            try:
                question_id = int(item["question_id"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("每份答案必须包含合法题目编号") from exc
            answer = item.get("answer")
            if answer is not None and (not isinstance(answer, str) or len(answer) > 512):
                raise ValueError("答案必须是 512 字以内的文本")
            ids.append(question_id)
            answer_by_id[question_id] = answer
        if len(set(ids)) != len(ids):
            raise ValueError("同一题只能提交一次")
        questions = self.stores.learning.questions_for_practice(ids)
        if len(questions) != len(ids):
            raise ValueError("包含不存在或不可练习的题目")
        graded = grade_submission(questions, [{"question_id": key, "answer": value} for key, value in answer_by_id.items()])
        records = [{"question_id": item["question_id"], "answer": answer_by_id[item["question_id"]],
                    "is_correct": item["is_correct"]} for item in graded]
        self.stores.learning.save_practice(student_id, records, source=source)
        correct = sum(item["is_correct"] is True for item in records)
        objective = sum(item["is_correct"] is not None for item in records)
        return {"records": records, "objective_count": objective, "correct_count": correct,
                "score": round(correct / objective * 100, 1) if objective else None}
