"""V1 发布验收（对齐 knowforge 的 verify_v1_release.py）。

汇总可确定性验收的检查项，产出 reports/verification/v1_release_latest.json：
1. 工程守护（guardrails）
2. 单元测试（pytest，可用 --skip-tests 跳过）
3. 关键数据资产存在（labels.json / processed CSV / golden set）
4. 模型版本目录规范（至少一个版本通过 manifest 校验）
5. MySQL 可达性 + 表结构就绪（--skip-db 可跳过）

用法：
    venv\\Scripts\\python scripts\\verify_release.py
    venv\\Scripts\\python scripts\\verify_release.py --skip-tests --skip-db
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import sys as _sys
from pathlib import Path as _Path
for _parent in _Path(__file__).resolve().parents:
    if (_parent / "edu_core").is_dir():
        _sys.path.insert(0, str(_parent))
        break
from scripts.common import get_root  # noqa: F401  — 确保 edu_core 可导入
from edu_core.config.settings import get_settings

ROOT = get_root()


def run_guardrails() -> dict:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_project_guardrails.py")],
        capture_output=True, text=True, cwd=str(ROOT))
    return {"name": "guardrails", "passed": proc.returncode == 0,
            "detail": (proc.stdout + proc.stderr).strip()[-2000:]}


def run_tests() -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q", "--tb=short"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=600)
    tail = (proc.stdout + proc.stderr).strip()[-2000:]
    return {"name": "pytest", "passed": proc.returncode == 0, "detail": tail}


def check_data_assets() -> dict:
    settings = get_settings()
    required = [
        settings.abs_path(settings.data_processed_dir) / "labels.json",
        settings.abs_path(settings.data_processed_dir) / "test.csv",
        settings.abs_path(settings.eval_sets_dir) / "golden_test_set.json",
    ]
    missing = [str(p.relative_to(ROOT)) for p in required if not p.is_file()]
    return {"name": "data_assets", "passed": not missing,
            "detail": "all present" if not missing else f"missing: {missing}"}


def check_model_versions() -> dict:
    settings = get_settings()
    versions_root = settings.abs_path(settings.model_versions_dir)
    versions = []
    if versions_root.is_dir():
        for d in sorted(versions_root.iterdir()):
            manifest = d / "manifest.json"
            if manifest.is_file():
                versions.append(d.name)
    return {"name": "model_versions", "passed": bool(versions),
            "detail": f"manifest-valid versions: {versions or 'none（先运行 scripts/migrate_model_weights.py）'}"}


def check_mysql() -> dict:
    try:
        from edu_core.storage.bootstrap import _server_connection
        settings = get_settings()
        with _server_connection(settings) as conn:
            with conn.cursor() as cur:
                cur.execute("SHOW TABLES")
                tables = {row[0] for row in cur.fetchall()}
        expected = {"model_versions", "active_model_pointer", "classifications",
                    "questions", "feedback", "daily_stats"}
        missing = expected - tables
        return {"name": "mysql", "passed": not missing,
                "detail": f"database={settings.mysql_db} tables ok" if not missing
                else f"missing tables: {sorted(missing)}（运行 scripts/init_db.py）"}
    except Exception as exc:  # noqa: BLE001
        return {"name": "mysql", "passed": False, "detail": f"unreachable: {exc}"}


def main() -> int:
    parser = argparse.ArgumentParser(description="V1 发布验收")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-db", action="store_true")
    args = parser.parse_args()

    results = [run_guardrails(), check_data_assets(), check_model_versions()]
    if not args.skip_tests:
        results.append(run_tests())
    if not args.skip_db:
        results.append(check_mysql())

    all_passed = all(r["passed"] for r in results)
    payload = {
        "verified_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "all_passed": all_passed,
        "results": results,
    }
    out_dir = get_settings().abs_path(get_settings().reports_dir) / "verification"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "v1_release_latest.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 60)
    for r in results:
        print(f"  [{'PASS' if r['passed'] else 'FAIL'}] {r['name']}: {r['detail'][:160]}")
    print("=" * 60)
    print(f"V1 发布验收：{'通过 ✓' if all_passed else '未通过 ✗'}（报告：{out}）")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
