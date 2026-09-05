"""置信度分级（纯函数，pytest 直接覆盖）。

三级平均置信度 -> 风险分级 -> 复核建议：
  high   (>= 0.80)：可直接采信
  medium (0.60~0.80)：建议人工复核
  low    (< 0.60)：必须人工复核

阈值来自 settings（EDU_CONFIDENCE_HIGH / EDU_CONFIDENCE_MEDIUM），
旧版界面里 0.9/0.7 的硬编码阈值由此收敛为配置项。
"""

from __future__ import annotations

HIGH = "high"
MEDIUM = "medium"
LOW = "low"

BAND_HINTS = {
    HIGH: "置信度较高，可直接采信",
    MEDIUM: "置信度一般，建议人工复核",
    LOW: "置信度较低，必须人工复核",
}


def average_confidence(conf: dict) -> float:
    """三级置信度平均（subject/question_type/knowledge_point）。"""
    values = [float(conf.get(k, 0.0)) for k in ("subject", "question_type", "knowledge_point")]
    return round(sum(values) / len(values), 4) if values else 0.0


def band(avg_confidence: float, high_threshold: float = 0.80,
         medium_threshold: float = 0.60) -> str:
    """置信度 -> 风险分级（纯函数）。"""
    if avg_confidence >= high_threshold:
        return HIGH
    if avg_confidence >= medium_threshold:
        return MEDIUM
    return LOW


def review_hint(band_name: str) -> str:
    """分级 -> 复核建议文案。"""
    return BAND_HINTS.get(band_name, BAND_HINTS[LOW])
