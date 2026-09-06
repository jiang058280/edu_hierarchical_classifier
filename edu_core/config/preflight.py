"""启动前置校验（preflight）。

对齐 knowforge-rag-platform 的理念：基础环境是硬性前置条件，缺失即启动失败，
不提供技术降级路径。本模块只做文件系统级校验（模型资产/标签/版本目录），
数据库与 active 版本校验由 storage.bootstrap 和 governance 完成。

历史缺陷修复：旧版 src/predict.py 在找不到微调权重时用"随机初始化的头"
静默对外服务（只打 WARN 日志）。新架构下该情况直接 PreflightError 拒绝启动。
"""

from __future__ import annotations

import json
from pathlib import Path

from edu_core.config.logging_config import get_logger
from edu_core.config.settings import Settings

logger = get_logger(__name__)

# 主干目录必须存在的文件（tokenizer 至少其一，权重至少其一）
_BACKBONE_REQUIRED = ["config.json", "vocab.txt"]
_TOKENIZER_ANY = ["tokenizer.json", "tokenizer_config.json"]
_WEIGHTS_ANY = ["model.safetensors", "pytorch_model.bin"]

# 模型版本目录必须存在的文件
_VERSION_REQUIRED = ["subject_head.pt", "type_head.pt", "knowledge_head.pt", "manifest.json"]

# 拒绝上生产的示例 JWT 密钥（与 .env.example 的占位值保持一致）
_WEAK_SECRETS = {"", "change-me-to-a-long-random-string", "change-me"}


class PreflightError(RuntimeError):
    """启动前置条件不满足。服务必须拒绝启动，不允许降级运行。"""


def _missing(directory: Path, names: list[str]) -> list[str]:
    return [n for n in names if not (directory / n).is_file()]


def validate_backbone(backbone_dir: Path) -> dict:
    """校验 BERT 主干目录（config/tokenizer/权重）。"""
    if not backbone_dir.is_dir():
        raise PreflightError(f"主干目录不存在：{backbone_dir}")
    missing = _missing(backbone_dir, _BACKBONE_REQUIRED)
    if not any((backbone_dir / n).is_file() for n in _TOKENIZER_ANY):
        missing.append("tokenizer.json / tokenizer_config.json（至少其一）")
    if not any((backbone_dir / n).is_file() for n in _WEIGHTS_ANY):
        missing.append("model.safetensors / pytorch_model.bin（至少其一）")
    if missing:
        raise PreflightError(f"主干资产不完整：{backbone_dir} 缺少 {missing}")
    return {"backbone_dir": str(backbone_dir), "ok": True}


def validate_labels_file(labels_path: Path) -> dict:
    """校验标签映射文件存在且包含三个层级的 id 映射。"""
    if not labels_path.is_file():
        raise PreflightError(f"标签映射文件不存在：{labels_path}")
    try:
        labels = json.loads(labels_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PreflightError(f"标签映射文件不是合法 JSON：{labels_path}（{exc}）") from exc
    for key in ("subjects", "subject2id", "question_types", "type2id", "knowledge_points", "knowledge2id"):
        if key not in labels:
            raise PreflightError(f"标签映射文件缺少字段 {key}：{labels_path}")
    return {
        "labels_path": str(labels_path),
        "n_subjects": len(labels["subjects"]),
        "n_types": len(labels["question_types"]),
        "n_knowledge": len(labels["knowledge_points"]),
        "ok": True,
    }


def validate_version_dir(version_dir: Path, project_root: Path | None = None) -> dict:
    """校验单个模型版本目录（三头 + manifest 齐全）。

    manifest.backbone_ref 指向微调权重目录（只需 pytorch_model.bin / model.safetensors）；
    tokenizer 与模型结构来自 settings.backbone_dir（原始主干），由
    validate_runtime_environment 单独校验，两目录职责分离。

    Args:
        version_dir: 版本目录（约定 <root>/models/versions/<ver>）。
        project_root: 项目根目录；backbone_ref 为相对路径时必须提供。
    """
    if not version_dir.is_dir():
        raise PreflightError(f"模型版本目录不存在：{version_dir}")
    missing = _missing(version_dir, _VERSION_REQUIRED)
    if missing:
        raise PreflightError(f"模型版本目录不完整：{version_dir} 缺少 {missing}")
    try:
        manifest = json.loads((version_dir / "manifest.json").read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PreflightError(f"manifest.json 不是合法 JSON：{version_dir}") from exc
    backbone_ref = manifest.get("backbone_ref")
    if not backbone_ref:
        raise PreflightError(f"manifest.json 缺少 backbone_ref 字段：{version_dir}")
    ref = Path(backbone_ref)
    if not ref.is_absolute():
        if project_root is None:
            raise PreflightError(
                f"backbone_ref 为相对路径（{backbone_ref}），必须提供 project_root 才能解析")
        ref = project_root / ref
    if not ref.is_dir():
        raise PreflightError(f"微调权重目录不存在：{ref}")
    if not any((ref / w).is_file() for w in ("pytorch_model.bin", "model.safetensors")):
        raise PreflightError(f"微调权重目录缺少 pytorch_model.bin / model.safetensors：{ref}")
    return {"version_dir": str(version_dir), "backbone_ref": str(ref), "ok": True}


def validate_auth_settings(settings: Settings) -> dict:
    """鉴权配置校验（改进计划 WP-D）：鉴权开启时拒绝弱密钥上生产。"""
    if settings.auth_disabled:
        logger.warning("鉴权已通过 EDU_AUTH_DISABLED 关闭——仅允许本机开发使用，生产严禁")
        return {"auth": "disabled", "ok": True}
    secret = settings.jwt_secret
    if secret in _WEAK_SECRETS or len(secret) < 16:
        raise PreflightError(
            "鉴权已开启但 EDU_JWT_SECRET 缺失或过弱（需 ≥16 字符且非示例值）。"
            "请在 .env 配置随机密钥，或本机调试时显式设置 EDU_AUTH_DISABLED=true")
    return {"auth": "enabled", "secret_length": len(secret), "ok": True}


def validate_runtime_environment(settings: Settings) -> dict:
    """服务启动前的文件系统级校验汇总（不含数据库）。"""
    summary = {
        "backbone": validate_backbone(settings.abs_path(settings.backbone_dir)),
        "labels": validate_labels_file(settings.abs_path(settings.data_processed_dir) / "labels.json"),
        "model_versions_dir": str(settings.abs_path(settings.model_versions_dir)),
        "auth": validate_auth_settings(settings),
    }
    logger.info("Runtime preflight passed: %s", summary)
    return summary
