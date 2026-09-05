"""工程守护检查（对齐 knowforge 的 check_project_guardrails.py）。

防止旧版反模式回流主链路：
1. 禁止 Gradio 回归：主链路（app.py / edu_core / scripts / tests）不得 import gradio，
   不得调用 Gradio 私有 API（_set_html_css_theme_variables / demo.config 等）；
2. 禁止硬编码个人路径与盘符路径（legacy/ 除外）；
3. 禁止 os.chdir（工作目录副作用，legacy/ 除外）；
4. 禁止 CORS 通配符 allow_origins=["*"]（legacy/ 除外）；
5. 依赖锁定：requirements.txt 除注释与 index-url 外必须全部 == 精确锁定；
6. 关键结构存在：edu_core 分层包 / runtime_schema.sql / static 前端 / tests。

退出码 0 通过 / 1 存在违规。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 主链路扫描范围（legacy/ 明确豁免）
SCAN_DIRS = ["app.py", "edu_core", "scripts", "tests", "static"]
FORBIDDEN_PATTERNS = [
    (r"^\s*(import gradio|from gradio)", "禁止 import gradio（Gradio 已退役到 legacy/）"),
    (r"_set_html_css_theme_variables|demo\.config|demo\.app\b", "禁止 Gradio 私有 API"),
    (r" toumanfen|d:/pycharm|d:\\pycharm", "禁止硬编码个人机器路径"),
    (r"^\s*os\.chdir\(", "禁止 os.chdir（工作目录副作用）"),
    (r"allow_origins\s*=\s*\[?\s*[\"']\*[\"']", "禁止 CORS 通配符"),
    (r"yaml\.safe_load", "主链路禁止读取旧 config.yaml（配置统一走 pydantic-settings）"),
]

# requirements.txt 允许的非锁定行前缀
REQ_ALLOWED_PREFIXES = ("#", "--extra-index-url", "--index-url", "-i ", "")


def iter_py_files() -> list[Path]:
    # 排除守护脚本自身：其文档字符串与正则字面量天然包含被禁模式的引用
    self_name = Path(__file__).resolve()
    files: list[Path] = []
    for item in SCAN_DIRS:
        p = ROOT / item
        if p.is_file() and p.suffix == ".py":
            files.append(p)
        elif p.is_dir():
            files.extend(p.rglob("*.py"))
    return [f for f in files
            if "__pycache__" not in f.parts and f.resolve() != self_name]


def check_source_patterns() -> list[str]:
    issues: list[str] = []
    for f in iter_py_files():
        rel = f.relative_to(ROOT).as_posix()
        content = f.read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(content.splitlines(), 1):
            for pattern, message in FORBIDDEN_PATTERNS:
                if re.search(pattern, line):
                    issues.append(f"[{rel}:{lineno}] {message}: {line.strip()[:100]}")
    return issues


def check_requirements_pinned() -> list[str]:
    issues: list[str] = []
    req = ROOT / "requirements.txt"
    if not req.is_file():
        return ["requirements.txt 不存在"]
    for lineno, line in enumerate(req.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if any(stripped.startswith(prefix) for prefix in REQ_ALLOWED_PREFIXES):
            continue
        if "==" not in stripped:
            issues.append(f"[requirements.txt:{lineno}] 依赖未锁定版本（需 ==）：{stripped}")
    return issues


def check_structure() -> list[str]:
    issues: list[str] = []
    required = [
        "edu_core/__init__.py",
        "edu_core/config/settings.py",
        "edu_core/config/preflight.py",
        "edu_core/inference/model.py",
        "edu_core/inference/predictor.py",
        "edu_core/application/service.py",
        "edu_core/storage/runtime_schema.sql",
        "edu_core/governance/model_versions.py",
        "edu_core/quality/gate.py",
        "edu_core/api/classify.py",
        "app.py",
        "static/index.html",
        "static/admin.html",
        "tests/__init__.py",
        ".env.example",
        "Dockerfile",
        "docker-compose.yml",
    ]
    for rel in required:
        if not (ROOT / rel).is_file():
            issues.append(f"[结构缺失] {rel}")
    return issues


def main() -> int:
    issues = check_source_patterns() + check_requirements_pinned() + check_structure()
    if issues:
        print(f"工程守护检查未通过，共 {len(issues)} 处违规：\n")
        for issue in issues:
            print(f"  - {issue}")
        return 1
    print("工程守护检查通过 ✓（无 Gradio 回归 / 无硬编码路径 / 依赖已锁定 / 结构完整）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
