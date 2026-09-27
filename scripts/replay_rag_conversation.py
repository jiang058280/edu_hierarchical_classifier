"""回放单个已审核多轮用例；默认不联网，执行需显式 --execute。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx

from edu_core.rag.replay import ReplayError, replay_case, replay_queries


def validate_endpoint(value: str) -> str:
    url = urlsplit(value)
    if (not url.hostname or url.username or url.password or url.query or url.fragment
            or url.scheme not in {"https", "http"}):
        raise ValueError("接口地址必须是无凭据、查询参数或片段的 HTTP(S) URL")
    if url.scheme == "http" and url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("非本机接口必须使用 HTTPS")
    return value.rstrip("/") + "/"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--output", type=Path, required=True, help="新报告路径，不覆盖已有文件")
    parser.add_argument("--base-url", help="隔离测试服务 API 前缀，例如 http://127.0.0.1:8000/api/v1")
    parser.add_argument("--expected-user-id", type=int)
    parser.add_argument("--execute", action="store_true", help="将创建真实会话并可能产生模型费用")
    args = parser.parse_args()
    cases = [json.loads(line) for line in args.dataset.read_text(encoding="utf-8").splitlines() if line.strip()]
    matches = [case for case in cases if case.get("id") == args.case_id]
    if len(matches) != 1:
        parser.error("case-id 必须唯一匹配一条用例")
    case = matches[0]
    try:
        queries = replay_queries(case)
        if type(case.get("expect_refusal")) is not bool:
            raise ValueError("用例缺少布尔型 expect_refusal")
        filters = case.get("filters") or {}
        if not isinstance(filters, dict):
            raise ValueError("filters 必须为对象")
        if args.execute:
            if case.get("ready") is not True:
                raise ValueError("待审核用例不能执行，请先完成人工审核")
            if not args.base_url or not args.expected_user_id or args.expected_user_id < 1:
                raise ValueError("执行必须指定隔离测试服务及测试学生 ID")
            endpoint = validate_endpoint(args.base_url)
            token = os.environ.get("EDU_RAG_REPLAY_TOKEN", "").strip()
            if not token:
                raise ValueError("请在本机环境变量 EDU_RAG_REPLAY_TOKEN 设置测试学生令牌")
    except ValueError as exc:
        parser.error(str(exc))
    report = {"case_id": case["id"], "status": "dry_run", "ready": case.get("ready") is True,
              "planned_turns": len(queries), "history_policy": "仅发送历史用户问题；实际生成回答替代参考回答",
              "ignored_filter_keys": [key for key, value in filters.items()
                                      if value is not None and key not in {"grade", "grade_band"}],
              "limitations": ["权限范围由测试账号及服务端决定，不传客户端 filters",
                              "回放完成不等于答案正确或具备上下文记忆；须审核真实回答",
                              "接口未返回分块排名，不计算 Recall、MRR 或 nDCG"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # 先独占创建，防止执行后才发现输出覆盖或不可写。
    with args.output.open("x", encoding="utf-8") as destination:
        json.dump(report, destination, ensure_ascii=False, indent=2)
        destination.flush()
        if args.execute:
            report["status"] = "incomplete"
            try:
                with httpx.Client(base_url=endpoint, headers={"Authorization": f"Bearer {token}"},
                                  timeout=120, follow_redirects=False, trust_env=False) as client:
                    replay_case(client, case, args.expected_user_id, report)
            except ReplayError as exc:
                report["error"] = str(exc)
            finally:
                destination.seek(0)
                json.dump(report, destination, ensure_ascii=False, indent=2)
                destination.truncate()
    print(json.dumps({"case_id": case["id"], "status": report["status"],
                      "planned_turns": len(queries)}, ensure_ascii=False))
    return 2 if report["status"] == "incomplete" else 0


if __name__ == "__main__":
    raise SystemExit(main())
