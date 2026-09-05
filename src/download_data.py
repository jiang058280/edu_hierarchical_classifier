# -*- coding: utf-8 -*-
"""
教育题目层级分类系统 - 数据下载脚本
下载 K-12EduBench 数据集（GitHub 官方仓库），9 个学科 JSON 文件。
直连失败时自动切换国内镜像（ghproxy.net / gitclone.com）兜底。

数据来源：https://github.com/shida-edu4ai/K-12EduBench
输出目录：D:/edu_hierarchical_classifier/data/raw/k12edubench/
"""
import os
import sys
import json
import time
import urllib.request

# ============ 项目根目录与缓存重定向（防 C 盘污染）============
PROJECT_ROOT = r"D:\edu_hierarchical_classifier"
os.chdir(PROJECT_ROOT)
os.environ["HF_HOME"] = os.path.join(PROJECT_ROOT, ".huggingface_cache")
os.environ["TORCH_HOME"] = os.path.join(PROJECT_ROOT, ".torch_cache")

RAW_DIR = os.path.join(PROJECT_ROOT, "data", "raw", "k12edubench")
os.makedirs(RAW_DIR, exist_ok=True)

# K-12EduBench 9 个学科文件名
SUBJECT_FILES = [
    "Mathematics", "Physics", "Chemistry", "Biology", "Chinese",
    "English", "History", "Geography", "Politics",
]

# 下载源（依次尝试，直到成功）
GITHUB_RAW = "https://api.github.com/repos/shida-edu4ai/K-12EduBench/contents/data/{name}.json"
GH_MIRROR = "https://ghproxy.net/https://raw.githubusercontent.com/shida-edu4ai/K-12EduBench/main/data/{name}.json"


def download_one(name, url, dest, timeout=120, retries=2):
    """下载单个文件，失败重试"""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/vnd.github.v3.raw", "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as f:
                f.write(resp.read())
            # 校验：非空且可解析为 JSON
            size = os.path.getsize(dest)
            with open(dest, "rb") as f:
                json.loads(f.read().decode("utf-8"))
            print(f"  ✓ {name}: {size/1024:.0f} KB")
            return True
        except Exception as e:
            print(f"  第 {attempt+1} 次失败: {e}")
            if attempt < retries:
                time.sleep(3)
            elif os.path.exists(dest):
                os.remove(dest)
    return False


def main():
    print("=" * 60)
    print("K-12EduBench 数据下载开始")
    print("=" * 60)
    report = {"source": "K-12EduBench(GitHub)", "downloaded": [], "failed": []}

    for subj in SUBJECT_FILES:
        dest = os.path.join(RAW_DIR, f"{subj}.json")
        if os.path.exists(dest) and os.path.getsize(dest) > 1000:
            print(f"  - {subj}: 已存在，跳过")
            report["downloaded"].append(subj)
            continue
        print(f"下载 {subj} ...")
        ok = False
        # 1) 直连 GitHub API
        if not ok:
            ok = download_one(subj, GITHUB_RAW.format(name=subj), dest)
        # 2) 国内镜像兜底
        if not ok:
            print(f"  直连失败，尝试国内镜像 ghproxy.net ...")
            ok = download_one(subj, GH_MIRROR.format(name=subj), dest, timeout=180)
        if ok:
            report["downloaded"].append(subj)
        else:
            report["failed"].append(subj)

    report_path = os.path.join(PROJECT_ROOT, "data", "raw", "download_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("=" * 60)
    print(f"下载完成：成功 {len(report['downloaded'])}/{len(SUBJECT_FILES)}")
    if report["failed"]:
        print(f"失败：{report['failed']}")
        sys.exit(1)
    print(f"报告已保存：{report_path}")


if __name__ == "__main__":
    main()
