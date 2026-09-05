"""API 端到端冒烟：TestClient 驱动完整 lifespan（preflight → 建库 → active 版本 → 模型预热），
再走 真实推理分类 → 反馈 → 录题（含查重） → 统计 → 版本列表 全链路。

用法（需 MySQL 可达，Milvus 可选）：
    set EDU_MYSQL_PORT=3307
    venv\\Scripts\\python scripts\\api_smoke.py
"""

from __future__ import annotations

import os
import sys

for _parent in __import__("pathlib").Path(__file__).resolve().parents:
    if (_parent / "edu_core").is_dir():
        sys.path.insert(0, str(_parent))
        break

os.environ.setdefault("EDU_MYSQL_PORT", "3307")

from fastapi.testclient import TestClient  # noqa: E402

import app as app_module  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    CHECKS.append((name, passed, detail))
    print(f"  [{'PASS' if passed else 'FAIL'}] {name} {detail}")


def main() -> int:
    with TestClient(app_module.app) as client:
        # 1. 健康检查（lifespan 预热已在本行触发完成）
        r = client.get("/api/v1/health")
        check("GET /health", r.status_code == 200, f"model={r.json().get('model_version')} "
              f"dedup={r.json().get('dedup_available')}")
        dedup_ok = r.json().get("dedup_available", False)

        # 2. 真实推理分类
        r = client.post("/api/v1/classify", json={"text": "已知函数 f(x)=x²+1，求 f(2) 的值。"})
        body = r.json()
        check("POST /classify", r.status_code == 200 and body.get("subject"),
              f"-> {body.get('subject')}/{body.get('q_type')}/{body.get('knowledge')} "
              f"band={body.get('band')} {body.get('latency_ms')}ms")
        classification_id = body.get("id")

        # 3. 参数校验（超长文本 -> 400）
        r = client.post("/api/v1/classify", json={"text": "长" * 5000})
        check("POST /classify 超长拒绝", r.status_code == 400, str(r.json())[:60])

        # 4. 反馈（关联分类留痕）
        r = client.post("/api/v1/feedback",
                        json={"classification_id": classification_id, "correct": False,
                              "corrected_subject": "数学"})
        check("POST /feedback", r.status_code == 200 and r.json().get("total_wrong", 0) >= 1)

        # 5. 录题两次（第二次应触发语义查重，若 Milvus 可用）
        q = {"text": "已知函数 f(x)=x²+1，求 f(2) 的值。", "subject": "数学",
             "q_type": "解答题", "knowledge": "数学::代数"}
        r1 = client.post("/api/v1/questions", json=q)
        r2 = client.post("/api/v1/questions", json=q)
        dups = r2.json().get("duplicates", []) if r2.status_code == 200 else []
        check("POST /questions", r1.status_code == 200 and r2.status_code == 200,
              f"dedup_indexed={r2.json().get('dedup_indexed')} "
              f"duplicates={len(dups)}")
        if dedup_ok:
            check("语义查重检出重复题", bool(dups),
                  f"相似度 {[d['similarity'] for d in dups]}")

        # 6. 题库列表
        r = client.get("/api/v1/questions")
        check("GET /questions", r.status_code == 200 and r.json().get("total", 0) >= 2,
              f"total={r.json().get('total')}")

        # 7. 统计（今日分类数应 >= 1）
        r = client.get("/api/v1/stats")
        check("GET /stats", r.status_code == 200 and r.json().get("total_processed", 0) >= 1,
              f"processed={r.json().get('total_processed')}")

        # 8. 版本治理接口
        r = client.get("/api/v1/models")
        active = r.json().get("active_version")
        check("GET /models", r.status_code == 200 and active,
              f"active={active}")

        # 9. 旧契约别名（/api/classify 无 v1 前缀）
        r = client.post("/api/classify", json={"text": "He usually ______ to school by bike. A. go"})
        check("旧契约 /api/classify", r.status_code == 200 and r.json().get("subject"))

        # 10. 前端页面
        for path in ("/", "/admin"):
            r = client.get(path)
            check(f"GET {path}", r.status_code == 200)

    failed = [c for c in CHECKS if not c[1]]
    print("=" * 60)
    print(f"API 冒烟：{len(CHECKS) - len(failed)}/{len(CHECKS)} 通过")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
