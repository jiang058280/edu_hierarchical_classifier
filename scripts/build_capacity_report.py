"""Build the E6 capacity report from Locust CSV files and isolated DB facts."""
from __future__ import annotations

import csv
from datetime import datetime
import json
from pathlib import Path
import sys

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edu_core.config.settings import get_settings  # noqa: E402

LOAD_DIR = ROOT / "reports" / "loadtest"
VERIFY_REPORT = ROOT / "reports" / "verification" / "e6_capacity_latest.json"


def endpoint_stats(path: Path, endpoint: str) -> dict:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        row = next((item for item in csv.DictReader(stream) if item["Name"] == endpoint), None)
    if row is None:
        raise RuntimeError(f"压测结果缺少端点 {endpoint}: {path}")
    count, failures = int(row["Request Count"]), int(row["Failure Count"])
    return {
        "requests": count,
        "failures": failures,
        "failure_rate": round(failures / count, 6) if count else 0,
        "rps": round(float(row["Requests/s"]), 2),
        "p50_ms": float(row["50%"]),
        "p95_ms": float(row["95%"]),
        "p99_ms": float(row["99%"]),
        "max_ms": float(row["Max Response Time"]),
    }


def main() -> int:
    fixture_path = LOAD_DIR / "runtime_fixture.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    database = str(fixture["database"])
    if not database.endswith("_loadtest") or database == get_settings().mysql_db:
        raise RuntimeError("仅允许读取隔离压测库")
    settings = get_settings().model_copy(update={"mysql_db": database})
    engine = create_engine(settings.mysql_url, pool_pre_ping=True, future=True)
    with engine.connect() as conn:
        submissions = int(conn.execute(text(
            "SELECT COUNT(*) FROM submissions WHERE assignment_id=:assignment AND status='submitted'"
        ), {"assignment": int(fixture["assignment_id"])}).scalar_one())
        answers = int(conn.execute(text("""
            SELECT COUNT(*) FROM answer_records ar JOIN submissions s ON s.id=ar.submission_id
            WHERE s.assignment_id=:assignment
        """), {"assignment": int(fixture["assignment_id"])}).scalar_one())
        rag_messages = int(conn.execute(text("SELECT COUNT(*) FROM rag_messages")).scalar_one())
        try:
            slow_rows = conn.execute(text("""
                SELECT LEFT(DIGEST_TEXT, 240) AS digest_text,
                       COUNT_STAR AS executions,
                       ROUND(AVG_TIMER_WAIT / 1000000000, 3) AS avg_ms,
                       ROUND(MAX_TIMER_WAIT / 1000000000, 3) AS max_ms
                FROM performance_schema.events_statements_summary_by_digest
                WHERE SCHEMA_NAME=:database AND DIGEST_TEXT IS NOT NULL
                ORDER BY MAX_TIMER_WAIT DESC LIMIT 5
            """), {"database": database}).mappings().all()
            slow_queries = []
            for row in slow_rows:
                item = dict(row)
                for key in ("avg_ms", "max_ms"):
                    item[key] = float(item[key]) if item.get(key) is not None else None
                item["executions"] = int(item["executions"])
                slow_queries.append(item)
        except Exception as exc:  # performance_schema may be disabled by the local image
            slow_queries = [{"status": "unavailable", "reason": type(exc).__name__}]

    scenarios = {
        "classify_50": endpoint_stats(
            LOAD_DIR / "classify_valid_stats.csv", "/api/v1/classify"
        ),
        "submit_50": endpoint_stats(
            LOAD_DIR / "submit_valid_stats.csv", "/student/assignments/:id/submit"
        ),
        "rag_stream_10": endpoint_stats(
            LOAD_DIR / "rag_valid_stats.csv", "/api/v1/student/rag/chat/stream"
        ),
    }
    checks = {
        "all_http_failures_zero": all(item["failures"] == 0 for item in scenarios.values()),
        "exactly_50_first_submissions": submissions == 50,
        "exactly_50_answer_records": answers == 50,
        "rag_messages_persisted": rag_messages > 0,
    }
    report = {
        "verified_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "passed" if all(checks.values()) else "failed",
        "scope": {
            "host": "127.0.0.1:7861", "database": database,
            "business_database_touched": False,
            "rate_limit": "isolated instance override only",
            "rag_path": "streaming no-evidence refusal; no external LLM call",
        },
        "scenarios": scenarios,
        "database_facts": {
            "submissions": submissions, "answer_records": answers,
            "rag_messages": rag_messages,
        },
        "checks": checks,
        "slow_query_sample": slow_queries,
    }
    VERIFY_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
