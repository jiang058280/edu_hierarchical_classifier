"""E6 容量压测场景。

仅允许 127.0.0.1/localhost。账号与作业夹具全部从环境变量读取，脚本不创建、
不删除业务数据。建议分别运行 --tags classify / submit / rag。
"""
from __future__ import annotations

from collections import deque
import json
from pathlib import Path
from urllib.parse import urlparse

from locust import HttpUser, between, events, task, tag
from locust.exception import StopUser


_submit_users: deque[str] = deque()


@events.init_command_line_parser.add_listener
def add_arguments(parser):
    parser.add_argument("--teacher-user", env_var="LOADTEST_TEACHER_USER", default="")
    parser.add_argument("--teacher-password", env_var="LOADTEST_TEACHER_PASSWORD", default="")
    parser.add_argument("--student-user", env_var="LOADTEST_STUDENT_USER", default="")
    parser.add_argument("--student-password", env_var="LOADTEST_STUDENT_PASSWORD", default="")
    parser.add_argument("--assignment-id", env_var="LOADTEST_ASSIGNMENT_ID", default="")
    parser.add_argument("--question-id", env_var="LOADTEST_QUESTION_ID", default="")
    parser.add_argument("--student-users-file", env_var="LOADTEST_STUDENT_USERS_FILE", default="")
    parser.add_argument("--rag-queries-file", env_var="LOADTEST_RAG_QUERIES_FILE", default="")


@events.test_start.add_listener
def guard_environment(environment, **_kwargs):
    host = environment.host or getattr(environment.runner.user_classes[0], "host", "")
    if urlparse(host).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("压测仅允许本机回环地址")
    users_file = getattr(environment.parsed_options, "student_users_file", "")
    if users_file:
        payload = json.loads(Path(users_file).read_text(encoding="utf-8"))
        users = payload.get("student_users") if isinstance(payload, dict) else payload
        if not isinstance(users, list) or not all(isinstance(item, str) for item in users):
            raise RuntimeError("student-users-file 必须包含 student_users 字符串数组")
        _submit_users.extend(users)


def login(client, username: str, password: str) -> str:
    if not username or not password:
        raise RuntimeError("必须提供专用压测账号，禁止使用脚本内置账号")
    response = client.post("/api/v1/auth/login", data={"username": username, "password": password})
    response.raise_for_status()
    return response.json()["access_token"]


class ClassifyUser(HttpUser):
    wait_time = between(.2, .8)

    def on_start(self):
        token = login(self.client, self.environment.parsed_options.teacher_user,
                      self.environment.parsed_options.teacher_password)
        self.client.headers.update({"Authorization": f"Bearer {token}"})

    @tag("classify")
    @task
    def classify(self):
        self.client.post("/api/v1/classify", json={"text": "已知一次函数 y=2x+1，求 x=2 时的函数值。"})


class SubmitUser(HttpUser):
    wait_time = between(.5, 1.2)

    def on_start(self):
        if not _submit_users:
            raise StopUser()
        username = _submit_users.popleft()
        token = login(self.client, username, self.environment.parsed_options.student_password)
        self.client.headers.update({"Authorization": f"Bearer {token}"})
        self.assignment_id = int(self.environment.parsed_options.assignment_id or 0)
        self.question_id = int(self.environment.parsed_options.question_id or 0)
        if not self.assignment_id:
            raise RuntimeError("submit 场景必须提供专用 LOADTEST_ASSIGNMENT_ID")
        if not self.question_id:
            raise RuntimeError("submit 场景必须提供专用 LOADTEST_QUESTION_ID")

    @tag("submit")
    @task
    def submit(self):
        with self.client.post(
            f"/api/v1/student/assignments/{self.assignment_id}/submit",
            json={"answers": [{"question_id": self.question_id, "answer": "A"}]},
            name="/student/assignments/:id/submit", catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"首次交卷应为 200，实际 {response.status_code}")
        raise StopUser()


class RagUser(HttpUser):
    wait_time = between(1, 2)

    def on_start(self):
        token = login(self.client, self.environment.parsed_options.student_user,
                      self.environment.parsed_options.student_password)
        self.client.headers.update({"Authorization": f"Bearer {token}"})

    @tag("rag")
    @task
    def ask(self):
        self.client.post("/api/v1/student/rag/chat/stream",
                         json={"query": "一次函数图像的增减性怎样判断？", "socratic": True})


class RagRealUser(HttpUser):
    """带真实 LLM 生成的问答压测：问题来自外部文件，须能命中激活知识库证据。"""

    wait_time = between(3, 6)
    _queries: list[str] = []
    _next_index = 0

    def on_start(self):
        if not RagRealUser._queries:
            queries_file = self.environment.parsed_options.rag_queries_file
            if not queries_file:
                raise RuntimeError("rag_real 场景必须提供 LOADTEST_RAG_QUERIES_FILE")
            payload = json.loads(Path(queries_file).read_text(encoding="utf-8"))
            queries = payload.get("queries") if isinstance(payload, dict) else payload
            if not isinstance(queries, list) or not queries or \
                    not all(isinstance(q, str) and q.strip() for q in queries):
                raise RuntimeError("rag-queries-file 必须提供非空问题字符串数组")
            RagRealUser._queries = queries
        token = login(self.client, self.environment.parsed_options.student_user,
                      self.environment.parsed_options.student_password)
        self.client.headers.update({"Authorization": f"Bearer {token}"})

    @tag("rag_real")
    @task
    def ask_real(self):
        query = RagRealUser._queries[RagRealUser._next_index % len(RagRealUser._queries)]
        RagRealUser._next_index += 1
        with self.client.post("/api/v1/student/rag/chat/stream",
                              json={"query": query}, name="/student/rag/chat/stream[real]",
                              catch_response=True, timeout=120) as response:
            if response.status_code != 200:
                response.failure(f"真实问答应为 200，实际 {response.status_code}")
