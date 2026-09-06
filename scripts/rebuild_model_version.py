"""模型版本重建流水线（对齐 knowforge 的 rebuild_kb_version.py）。

把 models/versions/<version>/ 的训练产物走完：
    注册 STAGED -> golden set 评估 -> 质量门禁 -> 激活 ACTIVE

用法：
    # 完整流水线：评估 + 门禁 + 激活
    venv\\Scripts\\python scripts\\rebuild_model_version.py --version v0.2-x --gate --activate

    # 只评估不入库（对已注册版本重跑评估）
    venv\\Scripts\\python scripts\\rebuild_model_version.py --version v0.1-base --evaluate-only

    # 已有评估报告，直接注册 + 激活（跳过评估，例如复跑）
    venv\\Scripts\\python scripts\\rebuild_model_version.py --version v0.1-base --skip-evaluation --activate
"""

from __future__ import annotations

import argparse
import json

import sys as _sys
from pathlib import Path as _Path
for _parent in _Path(__file__).resolve().parents:
    if (_parent / "edu_core").is_dir():
        _sys.path.insert(0, str(_parent))
        break
from scripts.common import get_root  # noqa: F401  — 确保 edu_core 可导入
from edu_core.config.logging_config import get_logger
from edu_core.config.settings import get_settings
from edu_core.governance.model_versions import ModelVersionManager, version_dir
from edu_core.inference.predictor import HierarchicalPredictor
from edu_core.quality.evaluation import evaluate_predictor, load_golden_set, save_report
from edu_core.quality.gate import check_gate

logger = get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="模型版本重建流水线：注册->评估->门禁->激活")
    parser.add_argument("--version", required=True, help="版本号（models/versions/<version>）")
    parser.add_argument("--description", default="rebuild_model_version", help="版本描述")
    parser.add_argument("--limit", type=int, default=None, help="评估只取前 N 条（冒烟）")
    parser.add_argument("--evaluate-only", action="store_true", help="只评估并保存报告，不注册不激活")
    parser.add_argument("--skip-evaluation", action="store_true", help="跳过评估（要求已有评估报告或注册指标）")
    parser.add_argument("--gate", action="store_true", help="评估后执行质量门禁")
    parser.add_argument("--activate", action="store_true", help="门禁通过后激活")
    args = parser.parse_args()

    settings = get_settings()
    vdir = version_dir(settings, args.version)
    if not vdir.is_dir():
        raise SystemExit(f"版本目录不存在：{vdir}")
    manager = ModelVersionManager(settings=settings)

    metrics: dict | None = None

    # ---------- 评估 ----------
    if not args.skip_evaluation:
        cases = load_golden_set(settings)
        if args.limit:
            cases = cases[: args.limit]
        logger.info("评估版本 %s（%s 条用例）", args.version, len(cases))
        predictor = HierarchicalPredictor(vdir, settings=settings, verbose=True)
        report = evaluate_predictor(predictor.predict, cases, version=args.version)
        report_path = save_report(report, settings)
        metrics = report["metrics"]
        print(f"评估完成：{report_path}")
        print(f"  学科 acc {metrics['subject_acc']} | 题型 F1 {metrics['type_f1']} | "
              f"知识点 F1 {metrics['knowledge_f1']} | 级联 acc {metrics['cascade_acc']} | "
              f"延迟 {metrics['avg_latency_ms']}ms")
        # 人工标签占比提示（WP-F：规则标签天花板需靠人工复核数据逐步替换）
        data_manifest_path = settings.abs_path(settings.data_processed_dir) / "data_manifest.json"
        if data_manifest_path.is_file():
            ls = (json.loads(data_manifest_path.read_text(encoding="utf-8"))
                  .get("labels_source") or {})
            ratio = ls.get("manual_ratio")
            if ratio is not None and ratio < 0.05:
                print(f"  [提示] 训练集人工标签占比 {ratio:.2%}（<5%）：题型/知识点标签仍以规则推断为主，"
                      f"建议持续用 scripts/merge_reviewed_cases.py 回灌人工复核数据")

    # ---------- 门禁 ----------
    if args.gate and metrics is not None:
        gate = check_gate(metrics, settings)
        for c in gate["checks"]:
            print(f"  [{'PASS' if c['passed'] else 'FAIL'}] {c['name']} = {c['value']} "
                  f"({'≥' if c['direction'] == 'min' else '≤'} {c['threshold']})")
        if not gate["passed"]:
            print("门禁未通过：版本保持未注册/未激活状态 ✗")
            raise SystemExit(1)
        print("门禁通过 ✓")

    if args.evaluate_only:
        return

    # ---------- 注册 + 激活 ----------
    try:
        manager.register_version(args.version, metrics=metrics, description=args.description)
        print(f"版本已注册（STAGED）：{args.version}")
    except ValueError as exc:
        print(f"注册跳过：{exc}（如需覆盖请先删除注册表记录）")
        return

    if args.activate:
        manager.activate_version(args.version)
        print(f"版本已激活（ACTIVE）：{args.version}")
        print("重启 API 服务后生效：venv\\Scripts\\python -m uvicorn app:app --port 7860")
    else:
        print("版本保持 STAGED；确认无误后运行："
              f"python scripts/rebuild_model_version.py --version {args.version} "
              "--skip-evaluation --gate --activate")


if __name__ == "__main__":
    main()
