# -*- coding: utf-8 -*-
"""
教育题目层级分类系统 - 工具函数模块
提供项目根路径、环境重定向、配置加载、日志等公共能力。
所有脚本必须通过 get_project_root() 动态拼接路径，严禁硬编码 C 盘路径。
"""
import os
import sys
from datetime import datetime

# 项目硬性根目录（对应文档第八节强制规范）
PROJECT_ROOT = r"D:\edu_hierarchical_classifier"


def get_project_root() -> str:
    """返回项目根目录（D:/edu_hierarchical_classifier）"""
    return PROJECT_ROOT


def setup_environment() -> None:
    """切换工作目录并重定向 HuggingFace / Torch 缓存到 D 盘（防 C 盘污染）"""
    os.chdir(PROJECT_ROOT)
    os.environ["HF_HOME"] = os.path.join(PROJECT_ROOT, ".huggingface_cache")
    os.environ["TORCH_HOME"] = os.path.join(PROJECT_ROOT, ".torch_cache")
    os.environ["TRANSFORMERS_CACHE"] = os.path.join(PROJECT_ROOT, ".huggingface_cache")
    # 确保 src 可被导入
    src_dir = os.path.join(PROJECT_ROOT, "src")
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)


def load_config() -> dict:
    """读取 config.yaml 配置"""
    import yaml
    cfg_path = os.path.join(PROJECT_ROOT, "config.yaml")
    with open(cfg_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def write_log(module: str, message: str, level: str = "INFO") -> None:
    """写入运行日志 logs/run.log 并打印到控制台（兼容 Windows GBK 控制台）"""
    log_dir = os.path.join(PROJECT_ROOT, "logs")
    os.makedirs(log_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] [{module}] {message}"
    # 安全打印：Windows 控制台可能不支持部分 Unicode 字符
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", errors="replace").decode())
    try:
        with open(os.path.join(log_dir, "run.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass  # 日志写入失败不影响主流程


def ensure_dirs(*relative_paths: str) -> None:
    """确保若干相对路径目录存在"""
    for rel in relative_paths:
        os.makedirs(os.path.join(PROJECT_ROOT, rel), exist_ok=True)
