"""对指定模型版本跑 golden set 回归评估，产出 JSON 报告（不激活、不判门禁）。

用法：
    venv\\Scripts\\python scripts\\evaluate_core_model.py --version v0.1-base
    venv\\Scripts\\python scripts\\evaluate_core_model.py --version v0.1-base --limit 30 --output reports/evaluation/smoke.json
"""

from __future__ import annotations

import argparse

import sys as _sys
from pathlib import Path as _Path
for _parent in _Path(__file__).resolve().parents:
    if (_parent / "edu_core").is_dir():
        _sys.path.insert(0, str(_parent))
        break
from scripts.common import get_root  # noqa: F401  — 确保 edu_core 可导入
from edu_core.config.logging_config import get_logger
from edu_core.config.settings import get_settings
from edu_core.governance.model_versions import version_dir
from edu_core.inference.predictor import HierarchicalPredictor
from edu_core.quality.evaluation import evaluate_predictor, load_golden_set, save_report

logger = get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="golden set 回归评估")
    parser.add_argument("--version", required=True, help="模型版本号（models/versions/<version>）")
    parser.add_argument("--limit", type=int, default=None, help="只评估前 N 条（冒烟用）")
    parser.add_argument("--output", default=None, help="报告输出路径（默认 reports/evaluation/<version>_evaluation.json）")
    args = parser.parse_args()

    settings = get_settings()
    vdir = version_dir(settings, args.version)
    if not vdir.is_dir():
        raise SystemExit(f"版本目录不存在：{vdir}")

    cases = load_golden_set(settings)
    if args.limit:
        cases = cases[: args.limit]

    logger.info("加载模型版本 %s（%s 条用例）", args.version, len(cases))
    predictor = HierarchicalPredictor(vdir, settings=settings, verbose=True)
    report = evaluate_predictor(predictor.predict, cases, version=args.version)

    m = report["metrics"]
    print("=" * 60)
    print(f"版本 {args.version} | golden set {m['n_samples']} 条")
    print(f"学科 acc {m['subject_acc']:.4f} | 题型 F1 {m['type_f1']:.4f} (acc {m['type_acc']:.4f})")
    print(f"知识点 F1 {m['knowledge_f1']:.4f} (acc {m['knowledge_acc']:.4f})")
    print(f"级联 acc {m['cascade_acc']:.4f} | 平均延迟 {m['avg_latency_ms']:.1f} ms")
    print("=" * 60)

    out = save_report(report, settings, name=args.output)
    print(f"报告已写入：{out}")


if __name__ == "__main__":
    main()
