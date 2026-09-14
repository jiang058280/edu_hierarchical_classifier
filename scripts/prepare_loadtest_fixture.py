"""Create an isolated, append-only E6 load-test fixture.

The script refuses to target the business database. It creates/uses a database
whose name ends in ``_loadtest`` and never deletes existing rows or databases.
Runtime credentials are written below reports/loadtest/, which is gitignored.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import secrets
import sys

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edu_core.config.settings import get_settings  # noqa: E402
from edu_core.security.auth import hash_password  # noqa: E402
from edu_core.storage.bootstrap import bootstrap_mysql_schema  # noqa: E402
from edu_core.storage.stores import StoreBundle  # noqa: E402

TARGET_DB = "edu_classifier_loadtest"
STUDENT_COUNT = 50


def main() -> int:
    source_settings = get_settings()
    if TARGET_DB == source_settings.mysql_db or not TARGET_DB.endswith("_loadtest"):
        raise RuntimeError("隔离压测库名称必须以 _loadtest 结尾，且不能等于业务库")
    if not re.fullmatch(r"[A-Za-z0-9_]+", TARGET_DB):
        raise RuntimeError("压测库名称不安全")

    target_settings = source_settings.model_copy(update={"mysql_db": TARGET_DB})
    bootstrap_mysql_schema(target_settings)
    source_engine = create_engine(source_settings.mysql_url, pool_pre_ping=True, future=True)
    target_engine = create_engine(target_settings.mysql_url, pool_pre_ping=True, future=True)

    with source_engine.connect() as conn:
        active = conn.execute(text("""
            SELECT v.version, v.directory, v.manifest_json, v.metrics_json, v.description
            FROM active_model_pointer p
            JOIN model_versions v ON v.version = p.active_version
            WHERE p.id = 1
        """)).mappings().first()
    if not active:
        raise RuntimeError("业务库没有 ACTIVE 模型，无法启动隔离压测服务")
    with target_engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO model_versions
                (version, directory, manifest_json, metrics_json, status, description)
            VALUES (:version, :directory, :manifest, :metrics, 'ACTIVE', :description)
            ON DUPLICATE KEY UPDATE directory=VALUES(directory),
                manifest_json=VALUES(manifest_json), metrics_json=VALUES(metrics_json),
                status='ACTIVE', description=VALUES(description)
        """), {
            "version": active["version"], "directory": active["directory"],
            "manifest": active["manifest_json"], "metrics": active["metrics_json"],
            "description": active["description"],
        })
        conn.execute(text("""
            INSERT INTO active_model_pointer (id, active_version) VALUES (1, :version)
            ON DUPLICATE KEY UPDATE active_version=VALUES(active_version)
        """), {"version": active["version"]})

    stores = StoreBundle(engine=target_engine)
    batch = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(3)
    common_password = secrets.token_urlsafe(24)
    password_hash = hash_password(common_password)
    teacher_username = f"load_teacher_{batch}"
    teacher_id = stores.users.create(
        teacher_username, password_hash, role="teacher", real_name="容量测试教师"
    )
    class_id = stores.classes.create(
        f"容量测试班_{batch}", "初中", "初三", teacher_id, secrets.token_hex(3).upper()
    )
    student_users = []
    for index in range(1, STUDENT_COUNT + 1):
        username = f"load_student_{batch}_{index:02d}"
        stores.users.create(
            username, password_hash, role="student", real_name=f"容量学生{index:02d}",
            grade_band="初中", grade="初三", class_id=class_id,
            student_no=f"LT{batch[-6:]}{index:02d}",
        )
        student_users.append(username)

    question_id = stores.questions.insert(
        f"[容量测试 {batch}] 已知 1+1 的值是？", "数学", "选择题", "有理数",
        source="loadtest", options=[{"key": "A", "text": "2"}, {"key": "B", "text": "3"}],
        answer="A", analysis="1+1=2。", difficulty=1, grade_band="初中", grade="初三",
        created_by=teacher_id, status="published",
    )
    paper_id = stores.papers.create(
        f"容量测试卷_{batch}", "数学", "初中", teacher_id, [question_id]
    )
    assignment_id = stores.assignments.create(
        paper_id=paper_id, class_id=class_id, title=f"容量交卷_{batch}",
        created_by=teacher_id, due_at=datetime.now() + timedelta(days=1),
    )

    output = ROOT / "reports" / "loadtest" / "runtime_fixture.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "database": TARGET_DB,
        "batch": batch,
        "teacher_user": teacher_username,
        "student_users": student_users,
        "common_password": common_password,
        "class_id": class_id,
        "question_id": question_id,
        "paper_id": paper_id,
        "assignment_id": assignment_id,
        "created_at": datetime.now().isoformat(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": "ready", "database": TARGET_DB, "students": len(student_users),
        "assignment_id": assignment_id, "runtime_file": str(output),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
