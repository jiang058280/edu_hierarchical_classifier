"""R1 教育知识库验收与入库报告。"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text

for parent in Path(__file__).resolve().parents:
    if (parent / "edu_core").is_dir():
        sys.path.insert(0, str(parent))
        break

from edu_core.config.settings import get_settings
from edu_core.storage.stores import get_engine


def _test_result() -> dict:
    settings = get_settings()
    root = settings.root()
    temp_dir = settings.abs_path(settings.reports_dir) / "tmp" / f"r1_verify_{uuid4().hex}"
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "pytest", "tests/test_rag_store.py",
         "tests/test_rag_ingestion.py", "tests/test_rag_indexing.py", "tests/test_rag_providers.py",
         "tests/test_migrations.py", "-q", "-p", "no:cacheprovider", f"--basetemp={temp_dir}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(root), timeout=180)
    return {"passed": proc.returncode == 0, "detail": (proc.stdout + proc.stderr).strip()[-1200:]}


def _ingestion_report() -> dict:
    engine = get_engine()
    with engine.connect() as conn:
        document_rows = conn.execute(text("""
            SELECT status, COUNT(*) AS total FROM rag_documents GROUP BY status
        """)).mappings().all()
        document_count = sum(int(row["total"]) for row in document_rows)
        chunk_count = int(conn.execute(text("SELECT COUNT(*) FROM rag_document_chunks")).scalar_one())
        orphan_count = int(conn.execute(text("""
            SELECT COUNT(*) FROM rag_document_chunks c
            LEFT JOIN rag_documents d ON d.id = c.document_id
            WHERE d.id IS NULL
        """)).scalar_one())
        empty_count = int(conn.execute(text("""
            SELECT COUNT(*) FROM rag_document_chunks WHERE CHAR_LENGTH(TRIM(content)) = 0
        """)).scalar_one())
        failed_jobs = int(conn.execute(text("""
            SELECT COUNT(*) FROM rag_ingestion_jobs WHERE status = 'FAILED'
        """)).scalar_one())
        reused_count = int(conn.execute(text("""
            SELECT COUNT(*) FROM audit_logs
            WHERE action = 'upload_rag_document'
              AND JSON_EXTRACT(detail_json, '$.reused') = true
        """)).scalar_one())
        active = conn.execute(text("""
            SELECT v.version FROM rag_active_kb_pointer p
            JOIN rag_kb_versions v ON v.id = p.active_version_id WHERE p.id = 1
        """)).scalar_one_or_none()
    return {
        "document_count": document_count,
        "document_status_counts": {row["status"]: int(row["total"]) for row in document_rows},
        "chunk_count": chunk_count,
        "api_tracked_reuse_count": reused_count,
        "failed_job_count": failed_jobs,
        "orphan_chunk_count": orphan_count,
        "empty_chunk_count": empty_count,
        "empty_chunk_ratio": round(empty_count / chunk_count, 6) if chunk_count else 0.0,
        "active_kb_version": active,
    }


def main() -> int:
    tests = _test_result()
    report = _ingestion_report()
    passed = tests["passed"] and report["orphan_chunk_count"] == 0 and report["empty_chunk_count"] == 0
    payload = {"verified_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "all_passed": passed,
               "checks": {"rag_test_suite": tests, "ingestion_report": report}}
    output = get_settings().abs_path(get_settings().reports_dir) / "verification" / "r1_rag_latest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"R1 RAG 验收：{'通过 ✓' if passed else '未通过 ✗'}（报告：{output}）")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
