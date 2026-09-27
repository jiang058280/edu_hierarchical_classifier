"""细粒度知识点目录：题库筛选、AI 录入确认与组卷共用的同一口径。"""

from __future__ import annotations

from collections import defaultdict


def build_catalog(rows: list[dict]) -> dict:
    """将 (subject, knowledge_point, status, n) 聚合行构建为学科分组的两级目录。

    - 空白知识点跳过（历史遗留行不进入目录）；
    - 学科按题量降序，知识点按已发布数降序再按名称稳定排序；
    - status 计数仅识别 published/draft，其余归入 draft 口径外的原始状态名。
    """
    grouped: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        subject = str(row.get("subject") or "").strip()
        point = str(row.get("knowledge_point") or "").strip()
        if not subject or not point:
            continue
        status = str(row.get("status") or "published")
        entry = grouped[subject].setdefault(point, {"knowledge_point": point, "published": 0, "draft": 0})
        if status in ("published", "draft"):
            entry[status] += int(row.get("n") or 0)
        else:
            entry[status] = entry.get(status, 0) + int(row.get("n") or 0)
    subjects = []
    for subject in sorted(grouped, key=lambda s: (
            -sum(point["published"] + point["draft"] for point in grouped[s].values()), s)):
        points = sorted(grouped[subject].values(),
                        key=lambda p: (-p["published"], -p["draft"], p["knowledge_point"]))
        subjects.append({"subject": subject, "point_count": len(points), "points": points})
    return {"subjects": subjects, "total_points": sum(item["point_count"] for item in subjects)}
