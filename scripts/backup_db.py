"""MySQL 业务库 JSON 快照备份（改进计划 A3）。

用法：
    venv\\Scripts\\python scripts\\backup_db.py [--out-dir backups]

行为：
    1. 连接 MySQL（读取 .env 的 EDU_MYSQL_*），导出除 schema_migrations 外的全部业务表；
    2. 写入 <out_dir>/snapshot_YYYYMMDD_HHMM.json（通用 JSON 格式，日期取当前时间，分钟精度）；
    3. 仅保留最近 7 份 snapshot_*.json，更早的自动清理；
    4. 打印每表行数与快照体积；行数超过告警阈值的表打印告警。

设计说明（A3）：
    - 不依赖宿主机 mysqldump，数据为通用 JSON 快照，表结构由 restore 侧按迁移重建；
    - schema_migrations 为迁移元数据表，不随数据备份，restore 时保留库内原状。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import os
import sys
from pathlib import Path

for _parent in Path(__file__).resolve().parents:
    if (_parent / "edu_core").is_dir():
        sys.path.insert(0, str(_parent))
        break

from edu_core.config.settings import get_settings  # noqa: E402
from edu_core.storage.bootstrap import _server_connection  # noqa: E402

# 快照保留份数
KEEP = 7
# 行数告警阈值（大表体积增长告警，A3 风险项）
WARN_ROWS = 200_000
# 不随业务数据导出的元数据表
EXCLUDED_TABLES = {"schema_migrations"}


def main() -> int:
    parser = argparse.ArgumentParser(description="MySQL JSON 快照备份")
    parser.add_argument("--out-dir", default="backups", help="快照输出目录（默认 backups）")
    args = parser.parse_args()

    settings = get_settings()
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = Path(__file__).resolve().parents[1] / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M")
    fp = out_dir / f"snapshot_{stamp}.json"

    snapshot: dict = {"exported_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                      "engine": "pymysql-json", "tables": {}}
    row_count = {}
    t0 = _dt.datetime.now()
    with _server_connection(settings) as conn:
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES")
            tables = sorted(r[0] for r in cur.fetchall() if r[0] not in EXCLUDED_TABLES)
            for t in tables:
                cur.execute(f"SELECT * FROM `{t}`")
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                snapshot["tables"][t] = rows
                row_count[t] = len(rows)
    elapsed_s = (_dt.datetime.now() - t0).total_seconds()

    tmp_fp = fp.with_suffix(".json.tmp")
    with open(tmp_fp, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, default=str, indent=1)
    os.replace(tmp_fp, fp)

    # 仅保留最近 KEEP 份
    snapshots = sorted(glob.glob(str(out_dir / "snapshot_*.json")))
    removed = []
    for old in snapshots[:-KEEP] if len(snapshots) > KEEP else []:
        try:
            os.remove(old)
            removed.append(os.path.basename(old))
        except OSError:
            pass

    size_mb = round(fp.stat().st_size / 1024 / 1024, 2)
    print(f"备份完成: {fp}")
    print(f"表数: {len(row_count)}  耗时: {elapsed_s:.2f}s  体积: {size_mb}MB")
    warnings = []
    for t, n in row_count.items():
        flag = "  <-- WARN 行数超过告警阈值" if n > WARN_ROWS else ""
        print(f"  {t}: {n}{flag}")
        if flag:
            warnings.append(t)
    if removed:
        print(f"已清理旧快照: {removed}")
    if warnings:
        print(f"[告警] 大表体积增长提示: {warnings}（快照仅保留 {KEEP} 份，请关注体积增长）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
