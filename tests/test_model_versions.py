"""版本治理纯逻辑测试：版本号生成、模型产物 fail-fast（不加载真实权重）。"""

import pytest

from edu_core.inference.predictor import ModelArtifactError, HierarchicalPredictor
from edu_core.governance.model_versions import new_version_id
from edu_core.config.settings import Settings


def test_new_version_id_format():
    vid = new_version_id(prefix="v0.2")
    assert vid.startswith("v0.2-")
    parts = vid.split("-")
    assert len(parts) == 3 and len(parts[1]) == 8 and len(parts[2]) == 6


def test_predictor_fails_fast_without_manifest(tmp_path):
    """旧版静默降级缺陷的回归测试：产物缺失必须抛错而不是带随机头服务。"""
    vdir = tmp_path / "broken-version"
    vdir.mkdir()
    settings = Settings(_env_file=None)
    with pytest.raises(ModelArtifactError):
        HierarchicalPredictor(vdir, settings=settings, verbose=False)


def test_predictor_fails_fast_on_missing_backbone_ref(tmp_path):
    """manifest 指向不存在的主干目录 -> 拒绝加载（绝不回退原始主干）。"""
    vdir = tmp_path / "partial-version"
    vdir.mkdir()
    (vdir / "manifest.json").write_text(
        '{"version":"x","backbone_ref":"models/pretrained_backbone/not-exist-backbone"}',
        encoding="utf-8")
    settings = Settings(_env_file=None)
    with pytest.raises(ModelArtifactError):
        HierarchicalPredictor(vdir, settings=settings, verbose=False)


def test_predictor_fails_fast_on_missing_labels(tmp_path):
    """manifest 指向合法目录但标签文件不存在 -> 拒绝加载。"""
    vdir = tmp_path / "v-test"
    vdir.mkdir()
    backbone = tmp_path / "backbone"
    backbone.mkdir()
    (vdir / "manifest.json").write_text(
        json_manifest(backbone), encoding="utf-8")
    settings = Settings(_env_file=None, data_processed_dir=str(tmp_path / "no-such-data"))
    with pytest.raises(ModelArtifactError):
        HierarchicalPredictor(vdir, settings=settings, verbose=False)


def json_manifest(backbone_dir) -> str:
    import json
    return json.dumps({
        "version": "v-test",
        "backbone_ref": str(backbone_dir),
    }, ensure_ascii=False)
