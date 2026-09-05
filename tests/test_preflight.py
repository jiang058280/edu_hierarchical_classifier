"""preflight 文件系统校验测试（使用临时目录，不触碰真实模型文件）。"""

import json

import pytest

from edu_core.config.preflight import (
    PreflightError, validate_backbone, validate_labels_file, validate_version_dir,
)


def _make_backbone(tmp_path: pytest.TempPathFactory) -> str:
    d = tmp_path / "backbone"
    d.mkdir()
    (d / "config.json").write_text("{}", encoding="utf-8")
    (d / "vocab.txt").write_text("[PAD]", encoding="utf-8")
    (d / "tokenizer.json").write_text("{}", encoding="utf-8")
    (d / "model.safetensors").write_text("x", encoding="utf-8")
    return str(d)


def test_validate_backbone_ok_and_missing(tmp_path):
    d = tmp_path / "bb"
    d.mkdir()
    (d / "config.json").write_text("{}", encoding="utf-8")
    (d / "vocab.txt").write_text("x", encoding="utf-8")
    (d / "tokenizer.json").write_text("{}", encoding="utf-8")
    (d / "pytorch_model.bin").write_bytes(b"x")
    assert validate_backbone(d)["ok"] is True

    (d / "pytorch_model.bin").unlink()
    (d / "model.safetensors").unlink(missing_ok=True)
    with pytest.raises(PreflightError):
        validate_backbone(d)


def test_validate_labels_file(tmp_path):
    labels = {"subjects": [], "subject2id": {}, "question_types": [], "type2id": {},
              "knowledge_points": [], "knowledge2id": {}}
    p = tmp_path / "labels.json"
    p.write_text(json.dumps(labels), encoding="utf-8")
    assert validate_labels_file(p)["ok"] is True

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"subjects": []}), encoding="utf-8")
    with pytest.raises(PreflightError):
        validate_labels_file(bad)


def test_validate_version_dir(tmp_path):
    backbone = _make_backbone(tmp_path)
    vdir = tmp_path / "v0.1-test"
    vdir.mkdir()
    for f in ("subject_head.pt", "type_head.pt", "knowledge_head.pt"):
        (vdir / f).write_bytes(b"fake")
    (vdir / "manifest.json").write_text(
        json.dumps({"version": "v0.1-test", "backbone_ref": backbone}), encoding="utf-8")
    assert validate_version_dir(vdir)["ok"] is True

    # 缺 manifest -> 拒绝
    vdir2 = tmp_path / "v0.2-test"
    vdir2.mkdir()
    (vdir2 / "subject_head.pt").write_bytes(b"x")
    with pytest.raises(PreflightError):
        validate_version_dir(vdir2)

    # manifest 缺 backbone_ref -> 拒绝
    vdir3 = tmp_path / "v0.3-test"
    vdir3.mkdir()
    for f in ("subject_head.pt", "type_head.pt", "knowledge_head.pt"):
        (vdir3 / f).write_bytes(b"x")
    (vdir3 / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(PreflightError):
        validate_version_dir(vdir3)
