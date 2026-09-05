"""质量门禁测试：全指标通过 / 各类指标失败 / 边界值。"""

from edu_core.config.settings import Settings
from edu_core.quality.gate import check_gate


def make_settings(**overrides) -> Settings:
    s = Settings(_env_file=None)
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


GOOD_METRICS = {
    "subject_acc": 0.97, "type_f1": 0.91, "knowledge_f1": 0.55,
    "cascade_acc": 0.71, "avg_latency_ms": 120.0,
}


def test_gate_pass():
    gate = check_gate(GOOD_METRICS, make_settings())
    assert gate["passed"] is True
    assert gate["failures"] == []
    assert len(gate["checks"]) == 5


def test_gate_fail_low_knowledge_f1():
    metrics = {**GOOD_METRICS, "knowledge_f1": 0.30}
    gate = check_gate(metrics, make_settings())
    assert gate["passed"] is False
    names = [f["name"] for f in gate["failures"]]
    assert names == ["knowledge_f1"]


def test_gate_fail_latency_over_threshold():
    metrics = {**GOOD_METRICS, "avg_latency_ms": 2000.0}
    gate = check_gate(metrics, make_settings())
    assert gate["passed"] is False
    assert gate["failures"][0]["name"] == "avg_latency_ms"
    assert gate["failures"][0]["direction"] == "max"


def test_gate_threshold_override():
    # 阈值收紧后同样的指标应失败
    gate = check_gate(GOOD_METRICS, make_settings(gate_min_knowledge_f1=0.9))
    assert gate["passed"] is False


def test_gate_boundary_equals_threshold_passes():
    metrics = {**GOOD_METRICS, "subject_acc": 0.85, "avg_latency_ms": 1500.0}
    gate = check_gate(metrics, make_settings())
    assert gate["passed"] is True


def test_gate_missing_metrics_fail_closed():
    gate = check_gate({}, make_settings())
    assert gate["passed"] is False
