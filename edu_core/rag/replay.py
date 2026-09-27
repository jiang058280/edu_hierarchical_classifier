"""学生 SSE 接口回放；不注入参考回答，不替代检索或生成。"""

from __future__ import annotations

import json
from time import perf_counter

import httpx


class ReplayError(RuntimeError):
    """不包含凭据、响应正文或内部地址的受控错误。"""


def replay_queries(case: dict) -> list[str]:
    if case.get("category") != "follow_up" or case.get("role", "student") != "student":
        raise ValueError("仅支持学生 follow_up 用例")
    history = case.get("conversation_history")
    if not isinstance(history, list) or not history or len(history) > 20 or len(history) % 2:
        raise ValueError("历史必须为 1 至 10 组 user/assistant 消息")
    for index, turn in enumerate(history):
        if (not isinstance(turn, dict) or turn.get("role") != ("user" if index % 2 == 0 else "assistant")
                or not isinstance(turn.get("content"), str) or not turn["content"].strip()):
            raise ValueError("历史消息必须按 user/assistant 交替且内容非空")
    queries = [turn["content"] for turn in history[::2]] + [case.get("query")]
    if any(not isinstance(q, str) or not q.strip() or len(q) > 2000 for q in queries):
        raise ValueError("每轮问题必须非空且不超过 2000 字符")
    return queries


def _events(lines):
    event, data, size = "", [], 0
    for line in lines:
        size += len(line)
        if size > 200_000:
            raise ReplayError("SSE 事件过大")
        if line == "":
            if data:
                try:
                    payload = json.loads("\n".join(data))
                except ValueError:
                    raise ReplayError("SSE JSON 无效") from None
                if not isinstance(payload, dict):
                    raise ReplayError("SSE 内容不是对象")
                yield event, payload
            event, data, size = "", [], 0
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
    if data:
        raise ReplayError("SSE 最后一个事件不完整")


def _positive_id(value):
    return type(value) is int and value > 0


def replay_turn(client: httpx.Client, query: str, session_id: int | None, record: dict) -> None:
    """逐步更新 record，失败时保留已知会话 ID 和部分文本供审计。"""
    started = perf_counter()
    record.update(query=query, session_id=session_id, status="incomplete", answer="", citations=[])
    seen_session, seen_citations, completed = False, False, False
    try:
        with client.stream("POST", "student/rag/chat/stream",
                           json={"query": query, "session_id": session_id}) as response:
            if response.status_code != 200:
                raise ReplayError(f"问答接口 HTTP {response.status_code}")
            if response.headers.get("content-type", "").split(";")[0].strip() != "text/event-stream":
                raise ReplayError("问答接口未返回 SSE")
            for event, payload in _events(response.iter_lines()):
                if completed:
                    raise ReplayError("done 后出现额外事件")
                if event == "error":
                    raise ReplayError("服务端报告回答未完成")
                if event == "session":
                    returned = payload.get("session_id")
                    if seen_session or not _positive_id(returned) or (session_id is not None and returned != session_id):
                        raise ReplayError("会话 ID 不符合预期")
                    seen_session = True
                    record.update(session_id=returned, message_id=payload.get("message_id"))
                elif not seen_session:
                    raise ReplayError("收到回答前缺少 session 事件")
                elif event == "token":
                    part = payload.get("text")
                    if seen_citations or not isinstance(part, str):
                        raise ReplayError("token 事件格式或顺序无效")
                    if len(record["answer"]) + len(part) > 100_000:
                        raise ReplayError("回答长度超限")
                    record["answer"] += part
                    record.setdefault("first_token_ms", round((perf_counter() - started) * 1000, 2))
                elif event == "citations":
                    items = payload.get("items")
                    if seen_citations or not isinstance(items, list) or any(not isinstance(i, dict) for i in items):
                        raise ReplayError("引用事件格式或顺序无效")
                    seen_citations = True
                    record["citations"] = items
                elif event == "done":
                    message_id = payload.get("message_id", record.get("message_id"))
                    if (not seen_citations or not record["answer"].strip()
                            or type(payload.get("refused")) is not bool or not _positive_id(message_id)):
                        raise ReplayError("完成事件缺少有效回答、引用状态或消息 ID")
                    record.update(message_id=message_id, refused=payload["refused"], reason=payload.get("reason"),
                                  server_latency_ms=payload.get("latency_ms"))
                    completed = True
                else:
                    raise ReplayError("未知 SSE 事件")
        if not completed:
            raise ReplayError("连接结束但未收到 done")
        record["status"] = "completed"
    except httpx.RequestError:
        raise ReplayError("接口网络请求失败") from None
    finally:
        record["client_latency_ms"] = round((perf_counter() - started) * 1000, 2)


def check_identity(client: httpx.Client, expected_user_id: int, filters: dict) -> dict:
    try:
        response = client.get("auth/me")
        if response.status_code != 200:
            raise ReplayError(f"身份检查 HTTP {response.status_code}")
        profile = response.json()
    except httpx.RequestError:
        raise ReplayError("身份检查网络失败") from None
    except ValueError:
        raise ReplayError("身份响应无效") from None
    if not isinstance(profile, dict) or profile.get("role") != "student" or profile.get("id") != expected_user_id:
        raise ReplayError("登录身份与指定测试学生不一致")
    try:
        response = client.get("student/rag/scope")
        if response.status_code != 200:
            raise ReplayError(f"范围检查 HTTP {response.status_code}")
        scope = response.json()
    except httpx.RequestError:
        raise ReplayError("范围检查网络失败") from None
    except ValueError:
        raise ReplayError("范围响应无效") from None
    if not isinstance(scope, dict):
        raise ReplayError("范围响应无效")
    for key in ("grade", "grade_band"):
        if filters.get(key) is not None and scope.get(key) != filters[key]:
            raise ReplayError(f"测试学生 {key} 与用例不一致")
    return scope


def replay_case(client: httpx.Client, case: dict, expected_user_id: int, report: dict) -> None:
    queries = replay_queries(case)
    report["actual_scope"] = check_identity(client, expected_user_id, case.get("filters") or {})
    session_id = None
    report["turns"] = []
    for query in queries:
        record = {}
        report["turns"].append(record)
        replay_turn(client, query, session_id, record)
        session_id = record["session_id"]
    report.update(status="replayed_pending_review", session_id=session_id,
                  final_refusal_matches=(report["turns"][-1]["refused"] == case["expect_refusal"]))
