import json
import sys

import httpx
import pytest

from edu_core.rag.replay import ReplayError, replay_case, replay_queries, replay_turn
from scripts import replay_rag_conversation as cli


def case():
    return {"id": "follow-1", "category": "follow_up", "role": "student", "ready": True,
            "filters": {"grade_band": "初中", "grade": "初三"}, "expect_refusal": False,
            "conversation_history": [{"role": "user", "content": "解释一次函数"},
                                     {"role": "assistant", "content": "不应上传的参考答案"}],
            "query": "那斜率呢？"}


def event(name, data):
    return f"event: {name}\ndata: {json.dumps(data)}\n\n"


def response_text(native=True, sid=9):
    return (event("session", {"session_id": sid, "message_id": None if native else 12})
            + event("token", {"text": "真实返回"})
            + event("citations", {"items": [{"source_name": "教材"}]})
            + event("done", {"refused": False, **({"message_id": 12} if native else {})}))


@pytest.mark.parametrize("native", [True, False])
def test_replay_sends_only_questions_and_reuses_real_session(native):
    sent = []
    def handler(request):
        if request.url.path.endswith("auth/me"):
            return httpx.Response(200, json={"id": 7, "role": "student"})
        if request.url.path.endswith("rag/scope"):
            return httpx.Response(200, json={"grade_band": "初中", "grade": "初三"})
        body = json.loads(request.content)
        sent.append(body)
        return httpx.Response(200, text=response_text(native), headers={"content-type": "text/event-stream"})
    with httpx.Client(base_url="https://test.example/api/v1/", transport=httpx.MockTransport(handler)) as client:
        report = {}
        replay_case(client, case(), 7, report)
    assert sent == [{"query": "解释一次函数", "session_id": None}, {"query": "那斜率呢？", "session_id": 9}]
    assert report["status"] == "replayed_pending_review"
    assert report["final_refusal_matches"] is True
    assert report["turns"][1]["answer"] == "真实返回"


@pytest.mark.parametrize("body", [
    event("session", {"session_id": 9}) + event("token", {"text": "半截"}),
    event("error", {"message": "secret-response"}),
    event("token", {"text": "缺少会话"}),
    response_text() + event("token", {"text": "结束后"}),
    event("session", {"session_id": 9}) + "event: token\ndata: broken\n\n",
])
def test_bad_stream_never_marks_complete_or_echoes_error_body(body):
    def handler(request):
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
    record = {}
    with httpx.Client(base_url="https://test.example/", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ReplayError) as error:
            replay_turn(client, "问题", None, record)
    assert "secret-response" not in str(error.value)
    assert record["status"] == "incomplete"
    assert "client_latency_ms" in record


def test_session_change_rejected():
    with httpx.Client(base_url="https://test.example/", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=response_text(sid=10),
                                          headers={"content-type": "text/event-stream"}))) as client:
        with pytest.raises(ReplayError, match="会话 ID"):
            replay_turn(client, "追问", 9, {})


@pytest.mark.parametrize("kind", ["identity", "scope"])
def test_preflight_mismatch_does_not_send_chat(kind):
    def handler(request):
        assert request.method == "GET"
        if request.url.path.endswith("auth/me"):
            return httpx.Response(200, json={"id": 8 if kind == "identity" else 7, "role": "student"})
        return httpx.Response(200, json={"grade_band": "高中", "grade": "高一"})
    with httpx.Client(base_url="https://test.example/", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ReplayError):
            replay_case(client, case(), 7, {})


@pytest.mark.parametrize("history", [[], [{"role": "user", "content": "问题"}],
                                       [{"role": "system", "content": "x"}, {"role": "assistant", "content": "x"}]])
def test_invalid_history_rejected(history):
    item = case()
    item["conversation_history"] = history
    with pytest.raises(ValueError):
        replay_queries(item)


@pytest.mark.parametrize("url", ["http://remote.example/api", "https://user:pass@test.example/api",
                                "https://test.example/api?key=secret", "file:///tmp/test"])
def test_unsafe_endpoint_rejected(url):
    with pytest.raises(ValueError):
        cli.validate_endpoint(url)


def test_cli_default_off_and_no_overwrite(tmp_path, monkeypatch):
    dataset, output = tmp_path / "cases.jsonl", tmp_path / "report.json"
    item = case()
    item["ready"] = False
    dataset.write_text(json.dumps(item), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["replay", "--dataset", str(dataset), "--case-id", item["id"], "--output", str(output)])
    def forbidden(**kwargs):
        pytest.fail("默认检查不得联网")
    monkeypatch.setattr(cli.httpx, "Client", forbidden)
    assert cli.main() == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "dry_run" and report["ready"] is False
    original = output.read_bytes()
    with pytest.raises(FileExistsError):
        cli.main()
    assert output.read_bytes() == original
    monkeypatch.setattr(sys, "argv", sys.argv + ["--execute"])
    with pytest.raises(SystemExit):
        cli.main()


def test_scope_uses_only_server_profile(monkeypatch):
    from edu_core.api import student
    from types import SimpleNamespace
    class Users:
        def get(self, user_id):
            assert user_id == 7
            return {"grade": "初三", "grade_band": "初中"}
    monkeypatch.setattr(student, "StoreBundle", lambda: SimpleNamespace(users=Users()))
    assert student.rag_scope({"id": 7, "grade": "高一"}) == {
        "grade": "初三", "grade_band": "初中", "subject": None, "knowledge_node_id": None}


def test_cli_failure_keeps_known_session_without_leaking_token(tmp_path, monkeypatch):
    dataset, output = tmp_path / "cases.jsonl", tmp_path / "report.json"
    dataset.write_text(json.dumps(case()), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["replay", "--dataset", str(dataset), "--case-id", "follow-1",
                        "--output", str(output), "--execute", "--expected-user-id", "7",
                        "--base-url", "https://test.example/api/v1"])
    monkeypatch.setenv("EDU_RAG_REPLAY_TOKEN", "private-test-token")
    client_class = httpx.Client
    def handler(request):
        assert request.headers["Authorization"] == "Bearer private-test-token"
        if request.url.path.endswith("auth/me"):
            return httpx.Response(200, json={"id": 7, "role": "student"})
        if request.url.path.endswith("rag/scope"):
            return httpx.Response(200, json={"grade_band": "初中", "grade": "初三"})
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              text=event("session", {"session_id": 9}) + event("token", {"text": "半截"}))
    def client_factory(**kwargs):
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        return client_class(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(cli.httpx, "Client", client_factory)
    assert cli.main() == 2
    raw = output.read_text(encoding="utf-8")
    assert "private-test-token" not in raw
    report = json.loads(raw)
    assert report["status"] == "incomplete"
    assert len(report["turns"]) == 1
    assert report["turns"][0]["session_id"] == 9
    assert report["turns"][0]["answer"] == "半截"
    assert report["turns"][0]["status"] == "incomplete"


def test_scope_route_rejects_teacher_before_storage_access(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from edu_core.api import student
    from edu_core.security.auth import get_current_user
    app = FastAPI()
    app.include_router(student.router)
    app.dependency_overrides[get_current_user] = lambda: {"id": 7, "role": "teacher"}
    def forbidden():
        pytest.fail("教师不可读取学生检索范围")
    monkeypatch.setattr(student, "StoreBundle", forbidden)
    with TestClient(app) as client:
        assert client.get("/student/rag/scope").status_code == 403
