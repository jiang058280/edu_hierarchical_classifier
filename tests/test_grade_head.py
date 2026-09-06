"""学段头与掩码损失纯逻辑测试（数据扩充轮）。"""

from __future__ import annotations

import pytest
import torch

from edu_core.training.train import LabelSmoothCE, _masked_loss


def test_masked_loss_ignores_missing_labels():
    torch.manual_seed(0)
    logits = torch.randn(6, 3)
    targets = torch.tensor([0, 1, 2, -100, -100, 1])
    fn = LabelSmoothCE(label_smooth=0.05)

    masked = _masked_loss(fn, logits, targets, torch.device("cpu"))
    manual = fn(logits[[0, 1, 2, 5]], targets[[0, 1, 2, 5]])
    assert masked is not None
    assert torch.isclose(masked, manual, atol=1e-6)


def test_masked_loss_all_missing_returns_none():
    logits = torch.randn(2, 3)
    targets = torch.tensor([-100, -100])
    assert _masked_loss(LabelSmoothCE(), logits, targets, torch.device("cpu")) is None


def test_masked_loss_requires_grad_path():
    logits = torch.randn(4, 3, requires_grad=True)
    targets = torch.tensor([0, -100, 2, -100])
    loss = _masked_loss(LabelSmoothCE(), logits, targets, torch.device("cpu"))
    assert loss is not None
    loss.backward()
    assert logits.grad is not None
    # 被掩码位置的梯度应经由过滤不产生直接损失贡献（第 1、3 行梯度可为非零但行 0/2 必有）
    assert logits.grad[0].abs().sum() > 0
    assert logits.grad[2].abs().sum() > 0


def test_label_smooth_ce_shapes():
    logits = torch.randn(8, 5)
    targets = torch.randint(0, 5, (8,))
    loss = LabelSmoothCE(label_smooth=0.05)(logits, targets)
    assert loss.ndim == 0 and torch.isfinite(loss)


@pytest.mark.parametrize("grade_bands,expected_head", [(["初中", "高中"], True), ([], False)])
def test_dataset_grade_masking(grade_bands, expected_head):
    """QuestionDataset 的 grade 掩码逻辑经 DataFrame 驱动验证（无 tokenizer 依赖列）。"""
    import pandas as pd

    from edu_core.training.train import QuestionDataset

    labels = {
        "subject2id": {"数学": 0},
        "type2id": {"选择题": 0},
        "knowledge2id": {"数学::代数": 0},
        "grade_bands": grade_bands,
    }
    df = pd.DataFrame({
        "text": ["题一", "题二"],
        "subject": ["数学", "数学"],
        "question_type": ["选择题", "选择题"],
        "knowledge_point": ["数学::代数", ""],   # 第二条缺失知识点
        "grade_band": ["初中", ""],              # 第二条缺失学段
    })
    ds = QuestionDataset(df, tokenizer=None, labels=labels, max_len=16)
    assert (ds.grades is not None) == expected_head
    assert ds.knowledge[0] == 0 and ds.knowledge[1] == -100
    if expected_head:
        assert ds.grades[0] == 0 and ds.grades[1] == -100
