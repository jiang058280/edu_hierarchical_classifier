from __future__ import annotations

from pathlib import Path

from scripts.build_capacity_report import endpoint_stats


def test_endpoint_stats_reads_locust_csv(tmp_path: Path):
    csv_path = tmp_path / "stats.csv"
    csv_path.write_text(
        "Type,Name,Request Count,Failure Count,Requests/s,50%,95%,99%,Max Response Time\n"
        "POST,/demo,50,0,25.5,10,40,55,80\n",
        encoding="utf-8",
    )
    assert endpoint_stats(csv_path, "/demo") == {
        "requests": 50, "failures": 0, "failure_rate": 0,
        "rps": 25.5, "p50_ms": 10.0, "p95_ms": 40.0,
        "p99_ms": 55.0, "max_ms": 80.0,
    }


def test_endpoint_stats_rejects_missing_endpoint(tmp_path: Path):
    csv_path = tmp_path / "stats.csv"
    csv_path.write_text(
        "Type,Name,Request Count,Failure Count,Requests/s,50%,95%,99%,Max Response Time\n",
        encoding="utf-8",
    )
    try:
        endpoint_stats(csv_path, "/missing")
    except RuntimeError as exc:
        assert "缺少端点" in str(exc)
    else:
        raise AssertionError("missing endpoint must be rejected")
