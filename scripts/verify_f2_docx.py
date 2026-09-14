"""F2 DOCX 导入本机验收：50 题提取、切分与 AI 预标注。"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from io import BytesIO
from pathlib import Path
from time import perf_counter

import httpx
from docx import Document

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from edu_core.security.auth import create_access_token
from edu_core.storage.stores import StoreBundle


def _fixture() -> bytes:
    document = Document()
    for index in range(1, 51):
        document.add_paragraph(f"{index}. 已知函数 f(x)=x²+{index}，求 f(2) 的值。")
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def main() -> int:
    stores = StoreBundle()
    user = stores.users.get_by_username("lgq")
    if not user or user["role"] not in {"teacher", "admin"}:
        raise RuntimeError("教师账号 lgq 不存在")
    token = create_access_token(user["username"], user["role"], int(user["id"]))
    started = perf_counter()
    with httpx.Client(base_url="http://127.0.0.1:7860",
                      headers={"Authorization": f"Bearer {token}"}, timeout=130) as client:
        response = client.post(
            "/api/v1/teacher/questions/import-docx",
            files={"file": ("50题验收.docx", _fixture(),
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
        response.raise_for_status()
    elapsed = perf_counter() - started
    payload = response.json()
    items = payload.get("items") or []
    if len(items) != 50:
        raise RuntimeError(f"预期 50 题，实际 {len(items)} 题")
    if any(not {"subject", "question_type", "knowledge_point", "confidences"} <= set(item) for item in items):
        raise RuntimeError("预标注响应缺少必要字段")
    if elapsed >= 120:
        raise RuntimeError(f"50 题 DOCX 预标注耗时 {elapsed:.3f}s，不满足 <120s")
    report = {
        "verified_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "passed", "question_count": len(items),
        "elapsed_seconds": round(elapsed, 3),
        "all_confidences_present": True,
    }
    output = ROOT / "reports" / "verification" / "f2_docx_latest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
