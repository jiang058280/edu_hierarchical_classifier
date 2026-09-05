# -*- coding: utf-8 -*-
"""
教育题目层级分类系统 - 试卷数据爬虫
爬取 https://www.shijuan1.com/ 网站的高考试卷列表页数据。

爬取范围：
  - 9 个学科的高考试卷列表页（gk 后缀）
  - 每个学科前 3 页列表

输出目录：data/raw/shijuan1/
输出格式：按学科保存 JSON 文件，每条记录：
  {"title": "试卷标题", "subject": "学科", "grade": "年级", "url": "详情页URL"}

运行：venv\\Scripts\\python src\\scraper.py
"""
import os
import re
import sys
import json
import time
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import get_project_root, setup_environment, ensure_dirs

ROOT = get_project_root()
setup_environment()

# ============ 输出目录 ============
OUTPUT_DIR = os.path.join(ROOT, "data", "raw", "shijuan1")
ensure_dirs("data/raw/shijuan1")

# ============ 日志配置 ============
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(ROOT, "logs", "scraper.log"), encoding="utf-8"
        ),
    ],
)
logger = logging.getLogger("scraper")

# ============ 学科 URL 映射（高考试卷 gk 后缀）============
# 学科名 -> 列表页路径片段
SUBJECT_URL_MAP = {
    "语文": "sjywgk",
    "数学": "sjsxgk",
    "英语": "sjyygk",
    "物理": "sjwlgk",
    "化学": "sjhxgk",
    "政治": "sjzzgk",
    "历史": "sjlsgk",
    "地理": "sjdlgk",
    "生物": "sjswgk",
}

BASE_URL = "https://www.shijuan1.com"
GRADE = "高考"          # 本次只爬高考试卷
MAX_PAGES = 3           # 每个学科最多爬取页数
REQUEST_DELAY = 1.0     # 每次请求间隔（秒）
REQUEST_TIMEOUT = 20    # 请求超时（秒）
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# 详情页链接正则：/a/<subject_path>/<id>.html
DETAIL_LINK_RE = re.compile(r"/a/(\w+)/(\d+)\.html")


def _build_http_get():
    """优先使用 requests，不可用时回退到 urllib.request。
    返回一个统一的 get(url) -> (status_code, text) 函数。
    """
    try:
        import requests  # type: ignore

        def requests_get(url):
            resp = requests.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT,
            )
            # shijuan1 站点为 UTF-8 编码
            if resp.encoding is None or resp.encoding.lower() != "utf-8":
                resp.encoding = "utf-8"
            return resp.status_code, resp.text

        logger.info("HTTP 后端：requests")
        return requests_get
    except ImportError:
        import urllib.request
        import urllib.error

        def urllib_get(url):
            req = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT}
            )
            try:
                with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
                    raw = r.read()
                    status = r.getcode()
            except urllib.error.HTTPError as e:
                return e.code, e.read().decode("utf-8", errors="ignore")
            return status, raw.decode("utf-8", errors="ignore")

        logger.info("HTTP 后端：urllib.request（requests 不可用）")
        return urllib_get


http_get = _build_http_get()


def list_page_urls(subject_path: str):
    """生成某学科前 N 页的列表页 URL。
    第 1 页：/a/<path>/index.html
    第 2 页起：/a/<path>/index_<n>.html
    """
    urls = [f"{BASE_URL}/a/{subject_path}/index.html"]
    for page in range(2, MAX_PAGES + 1):
        urls.append(f"{BASE_URL}/a/{subject_path}/index_{page}.html")
    return urls


def parse_list_page(html: str, subject_path: str, subject_name: str):
    """从列表页 HTML 中提取试卷详情页链接与标题。

    策略：
      1. 用正则匹配所有 /a/<subject_path>/<id>.html 形式的链接
      2. 取链接 <a> 标签内的文本作为标题（剥离空白）
      3. 若 <a> 标签无文本，回退使用 title 属性
      4. 去重（按详情页 URL）
    """
    items = []
    seen_urls = set()

    # 同时捕获 a 标签内的属性和文本，便于提取 title
    # 形如：<a href="/a/sjhxgk/334084.html" ...>标题文本</a>
    anchor_re = re.compile(
        r'<a[^>]+href=["\']/a/' + re.escape(subject_path) + r"/(\d+)\.html[\"'][^>]*>(.*?)</a>",
        re.IGNORECASE | re.DOTALL,
    )

    for match in anchor_re.finditer(html):
        paper_id = match.group(1)
        inner = match.group(2)
        # 从 inner 中剥离 HTML 标签和空白
        title = re.sub(r"<[^>]+>", "", inner).strip()
        # 解析常见的 HTML 实体
        title = title.replace("&amp;", "&").replace("&nbsp;", " ").replace("&quot;", '"')
        title = title.replace("&#39;", "'").replace("&lt;", "<").replace("&gt;", ">")
        title = re.sub(r"\s+", " ", title).strip()

        if not title:
            # 回退：尝试从 href 标签中取 title 属性
            attr_match = re.search(r'title=["\'](.*?)["\']', match.group(0), re.IGNORECASE)
            if attr_match:
                title = attr_match.group(1).strip()

        if not title:
            # 仍无标题，使用试卷 ID 占位
            title = f"试卷_{paper_id}"

        detail_url = f"{BASE_URL}/a/{subject_path}/{paper_id}.html"
        if detail_url in seen_urls:
            continue
        seen_urls.add(detail_url)

        items.append(
            {
                "title": title,
                "subject": subject_name,
                "grade": GRADE,
                "url": detail_url,
            }
        )

    return items


def scrape_subject(subject_name: str, subject_path: str):
    """爬取单个学科的高考试卷列表页"""
    logger.info(f"开始爬取学科：{subject_name}（/{subject_path}/）")
    all_items = []

    for page_idx, url in enumerate(list_page_urls(subject_path), start=1):
        logger.info(f"  [{subject_name}] 第 {page_idx} 页：{url}")
        try:
            status, html = http_get(url)
        except Exception as e:
            logger.warning(f"  [{subject_name}] 第 {page_idx} 页请求失败：{e}")
            time.sleep(REQUEST_DELAY)
            continue

        if status != 200:
            logger.warning(f"  [{subject_name}] 第 {page_idx} 页返回状态码 {status}，跳过")
            time.sleep(REQUEST_DELAY)
            continue

        if not html:
            logger.warning(f"  [{subject_name}] 第 {page_idx} 页内容为空")
            time.sleep(REQUEST_DELAY)
            continue

        items = parse_list_page(html, subject_path, subject_name)
        logger.info(f"  [{subject_name}] 第 {page_idx} 页解析到 {len(items)} 条记录")

        # 若本页未解析到任何记录，认为是已到末页，停止翻页
        if not items and page_idx > 1:
            logger.info(f"  [{subject_name}] 第 {page_idx} 页无记录，停止翻页")
            break

        all_items.extend(items)
        time.sleep(REQUEST_DELAY)

    # 按 URL 去重（跨页去重）
    seen = set()
    deduped = []
    for it in all_items:
        if it["url"] in seen:
            continue
        seen.add(it["url"])
        deduped.append(it)

    # 保存到 JSON 文件（按学科命名，使用拼音首字母对应的英文文件名）
    out_name = _subject_filename(subject_name)
    out_path = os.path.join(OUTPUT_DIR, f"{out_name}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(deduped, f, ensure_ascii=False, indent=2)

    logger.info(f"  [{subject_name}] 共保存 {len(deduped)} 条到 {out_path}")
    return len(deduped)


def _subject_filename(subject_name: str) -> str:
    """学科中文名 -> 英文文件名（与 k12edubench 保持一致风格）"""
    mapping = {
        "语文": "Chinese",
        "数学": "Mathematics",
        "英语": "English",
        "物理": "Physics",
        "化学": "Chemistry",
        "政治": "Politics",
        "历史": "History",
        "地理": "Geography",
        "生物": "Biology",
    }
    return mapping.get(subject_name, subject_name)


def main():
    logger.info("=" * 60)
    logger.info("shijuan1.com 高考试卷爬虫启动")
    logger.info(f"输出目录：{OUTPUT_DIR}")
    logger.info(f"学科数：{len(SUBJECT_URL_MAP)}，每学科最多 {MAX_PAGES} 页，"
                f"请求间隔 {REQUEST_DELAY}s")
    logger.info("=" * 60)

    summary = {"success": [], "failed": [], "total_records": 0}

    for subject_name, subject_path in SUBJECT_URL_MAP.items():
        try:
            count = scrape_subject(subject_name, subject_path)
            summary["success"].append(subject_name)
            summary["total_records"] += count
        except Exception as e:
            logger.error(f"学科 {subject_name} 爬取失败：{e}", exc_info=True)
            summary["failed"].append(subject_name)

    # 写入汇总报告
    report_path = os.path.join(OUTPUT_DIR, "_scrape_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    logger.info("=" * 60)
    logger.info(f"爬取完成：成功 {len(summary['success'])}/{len(SUBJECT_URL_MAP)} 学科，"
                f"共 {summary['total_records']} 条记录")
    if summary["failed"]:
        logger.warning(f"失败学科：{summary['failed']}")
    logger.info(f"汇总报告：{report_path}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
