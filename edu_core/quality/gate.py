"""质量门禁：评估指标低于基线即阻断激活。

对齐 knowforge 的 check_evaluation_gate：企业级主门禁以确定性指标为准
（准确率 / F1 / 级联准确率 / 延迟），可配置阈值（settings.gate_*）。
门禁判定是纯函数，方便 pytest 直接覆盖。
"""

from __future__ import annotations

from edu_core.config.settings import Settings, get_settings


def check_gate(metrics: dict, settings: Settings | None = None) -> dict:
    """对评估指标执行门禁判定。

    Args:
        metrics: evaluation.evaluate_predictor 产出的 metrics dict。
        settings: 提供阈值；缺省取全局单例。
    Returns:
        {"passed": bool, "checks": [...], "failures": [...]}
    """
    s = settings or get_settings()

    checks = [
        ("subject_acc", metrics.get("subject_acc", 0.0), s.gate_min_subject_acc, "min"),
        ("type_f1", metrics.get("type_f1", 0.0), s.gate_min_type_f1, "min"),
        ("knowledge_f1", metrics.get("knowledge_f1", 0.0), s.gate_min_knowledge_f1, "min"),
        ("cascade_acc", metrics.get("cascade_acc", 0.0), s.gate_min_cascade_acc, "min"),
        ("avg_latency_ms", metrics.get("avg_latency_ms", 0.0), s.gate_max_latency_ms, "max"),
    ]
    results = []
    for name, value, threshold, direction in checks:
        passed = value >= threshold if direction == "min" else value <= threshold
        results.append({
            "name": name,
            "value": value,
            "threshold": threshold,
            "direction": direction,
            "passed": passed,
        })
    failures = [r for r in results if not r["passed"]]
    return {"passed": not failures, "checks": results, "failures": failures}
