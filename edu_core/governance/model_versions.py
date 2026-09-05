"""模型版本治理（对齐 knowforge 知识库版本状态机）。

生命周期：

    训练产物写入 models/versions/<ver>/
        -> register_version()        STAGED（注册）
        -> 评估 + 质量门禁
        -> activate_version()        ACTIVE（激活，旧 ACTIVE 归档）
        -> rollback_to_previous()    回滚（切回最近归档版本）

与旧版的本质区别：
- 旧版 models/heads/*.pt 直接覆盖，线上跑的是哪个版本说不清；
- 新版每个版本独立目录 + manifest，MySQL 注册表 + active 指针是唯一事实来源，
  推理服务启动时强校验 active 版本文件齐全，缺失直接拒绝启动。

版本目录规范：
    models/versions/<version>/
      ├── subject_head.pt / type_head.pt / knowledge_head.pt
      ├── manifest.json       （backbone_ref、标签规模、训练指标、描述）
      └── eval_report.json    （可选：评估报告快照）
权重文件不入 git（.gitignore），manifest 与评估报告入库。
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from edu_core.config.logging_config import get_logger
from edu_core.config.preflight import validate_version_dir
from edu_core.config.settings import Settings, get_settings
from edu_core.storage.stores import StoreBundle

logger = get_logger(__name__)

VALID_STATUSES = ("STAGED", "ACTIVE", "ARCHIVED")


def new_version_id(prefix: str = "v0.2") -> str:
    """生成新版本号：{prefix}-{YYYYMMDD-HHMMSS}。"""
    return f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def version_dir(settings: Settings, version: str) -> Path:
    return settings.abs_path(settings.model_versions_dir) / version


def write_manifest(version_dir_path: Path, manifest: dict) -> None:
    (version_dir_path / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


class ModelVersionManager:
    """版本治理入口：注册/激活/回滚/查询，全部落 MySQL 注册表。"""

    def __init__(self, stores: StoreBundle | None = None, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.stores = stores or StoreBundle(settings=self.settings)

    # ---------- 注册 ----------
    def register_version(self, version: str, directory: str | None = None,
                         metrics: dict | None = None, description: str = "") -> dict:
        """把 models/versions/<version> 注册为 STAGED；目录必须通过文件校验。"""
        vdir = version_dir(self.settings, version)
        if not vdir.is_dir():
            raise FileNotFoundError(f"版本目录不存在：{vdir}")
        validate_version_dir(vdir, project_root=self.settings.root())  # fail-fast：三头 + manifest + 权重齐全才允许注册
        if directory is None:
            directory = str(vdir.relative_to(self.settings.root()))
        manifest = json.loads((vdir / "manifest.json").read_text(encoding="utf-8"))
        self.stores.model_versions.register(
            version, directory, manifest=manifest, metrics=metrics, description=description)
        return {"version": version, "directory": directory, "status": "STAGED"}

    # ---------- 激活 / 回滚 ----------
    def activate_version(self, version: str, validate_files: bool = True) -> dict:
        """激活指定版本。文件校验失败时拒绝激活（不允许带残缺版本上线）。"""
        if validate_files:
            validate_version_dir(version_dir(self.settings, version),
                                 project_root=self.settings.root())
        self.stores.model_versions.activate(version)
        return {"version": version, "status": "ACTIVE"}

    def rollback_to_previous(self) -> dict:
        """回滚到最近一次归档的版本（激活时间倒序，取 ARCHIVED 最新注册的下一个）。"""
        versions = self.stores.model_versions.list_versions()
        previous = next((v for v in versions if v["status"] == "ARCHIVED"), None)
        if not previous:
            raise ValueError("没有可回滚的归档版本")
        validate_version_dir(version_dir(self.settings, previous["version"]),
                             project_root=self.settings.root())
        self.stores.model_versions.activate(previous["version"])
        logger.info("已回滚到版本 %s", previous["version"])
        return {"rolled_back_to": previous["version"], "status": "ACTIVE"}

    # ---------- 查询 ----------
    def get_active(self) -> dict:
        """当前 active 版本；无 active 版本时抛错（服务不应在无版本状态下运行）。"""
        active = self.stores.model_versions.get_active()
        if not active:
            raise ValueError("没有激活的模型版本：请先运行 scripts/rebuild_model_version.py")
        return active

    def list_versions(self) -> list[dict]:
        return self.stores.model_versions.list_versions()

    def get_active_dir(self) -> Path:
        """active 版本的绝对路径目录（供 predictor 加载）。"""
        active = self.get_active()
        directory = Path(active["directory"])
        return directory if directory.is_absolute() else self.settings.root(directory)
