"""知识点学科 mask 纯逻辑测试（改进计划 WP-G1）。"""

from __future__ import annotations

import torch

from edu_core.inference.predictor import build_knowledge_mask


def _labels():
    return {
        "subjects": ["数学", "物理"],
        "knowledge_points": ["数学::代数", "数学::几何", "物理::力学", "物理::热学", "数学::其他"],
        "subject_knowledge": {
            "数学": ["数学::代数", "数学::几何", "数学::其他"],
            "物理": ["物理::力学", "物理::热学"],
        },
    }


def test_mask_marks_illegal_combinations():
    mask = build_knowledge_mask(_labels())
    assert mask.shape == (2, 5)
    # 数学行：数学::开头合法，物理::开头非法
    assert mask[0].tolist() == [False, False, True, True, False]
    # 物理行：物理::开头合法，数学::开头非法
    assert mask[1].tolist() == [True, True, False, False, True]


def test_mask_blocks_only_masked_positions_after_fill():
    labels = _labels()
    mask = build_knowledge_mask(labels)
    logits = torch.tensor([[2.0, 1.0, 5.0, 4.0, 0.5], [1.0, 2.0, 9.0, 8.0, 0.0]])
    # 学科=数学（index 0）：物理 logits 被压制
    masked = logits[0].masked_fill(mask[0], float("-inf"))
    assert int(masked.argmax()) == 0  # 数学::代数
    # 学科=物理（index 1）：数学 logits 被压制
    masked = logits[1].masked_fill(mask[1], float("-inf"))
    assert int(masked.argmax()) == 2  # 物理::力学


def test_mask_missing_subject_means_all_illegal():
    labels = _labels()
    labels["subject_knowledge"] = {}  # 合法组合表缺失 → 全部非法（保守行为）
    mask = build_knowledge_mask(labels)
    assert bool(mask.all())
