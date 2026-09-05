"""评测质量门禁：读取评估报告 JSON，指标低于基线则退出码 1（阻断 CI / 激活流程）。

用法：
    venv\\Scripts\\python scripts\\quality\\check_evaluation_gate.py --report reports/evaluation/v0.1-base_evaluation.json
    可选覆盖阈值：--min-subject-acc 0.9 --max-latency-ms 1200
"""

from __future__ import annotations

import argparse
import json
import sys

import sys as _sys
from pathlib import Path as _Path
for _parent in _Path(__file__).resolve().parents:
    if (_parent / "edu_core").is_dir():
        _sys.path.insert(0, str(_parent))
        break
from scripts.common import get_root  # noqa: F401  — 确保 edu_core 可导入
from edu_core.config.settings import Settings, get_settings
from edu_core.quality.gate import check_gate


def main() -> int:
    parser = argparse.ArgumentParser(description="评测质量门禁")
    parser.add_argument("--report", required=True, help="评估报告 JSON 路径")
    parser.add_argument("--min-subject-acc", type=float, default=None)
    parser.add_argument("--min-type-f1", type=float, default=None)
    parser.add_argument("--min-knowledge-f1", type=float, default=None)
    parser.add_argument("--min-cascade-acc", type=float, default=None)
    parser.add_argument("--max-latency-ms", type=float, default=None)
    args = parser.parse_args()

    from pathlib import Path
    p = Path(args.report)
    if not p.is_absolute():
        p = get_root() / p
    if not p.is_file():
        print(f"评估报告不存在：{p}")
        return 1
    report = json.loads(p.read_text(encoding="utf-8"))

    settings: Settings = get_settings()
    if args.min_subject_acc is not None:
        settings.gate_min_subject_acc = args.min_subject_acc
    if args.min_type_f1 is not None:
        settings.gate_min_type_f1 = args.min_type_f1
    if args.min_knowledge_f1 is not None:
        settings.gate_min_knowledge_f1 = args.min_knowledge_f1
    if args.min_cascade_acc is not None:
        settings.gate_min_cascade_acc = args.min_cascade_acc
    if args.max_latency_ms is not None:
        settings.gate_max_latency_ms = args.max_latency_ms

    gate = check_gate(report["metrics"], settings)

    print("=" * 60)
    print(f"质量门禁 | 版本 {report.get('version')} | 报告 {p.name}")
    for check in gate["checks"]:
        mark = "PASS" if check["passed"] else "FAIL"
        print(f"  [{mark}] {check['name']} = {check['value']} "
              f"({'≥' if check['direction'] == 'min' else '≤'} {check['threshold']})")
    print("=" * 60)

    if gate["passed"]:
        print("门禁通过 ✓")
        return 0
    print("门禁未通过 ✗（指标低于基线，禁止激活）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
