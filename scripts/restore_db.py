"""从 JSON 快照恢复 MySQL 业务库（改进计划 A3）。

用法：
    venv\\Scripts\\python scripts\\restore_db.py [--snapshot backups/snapshot_YYYYMMDD_HHMM.json]

行为：
    1. 读取快照（backup_db.py 产物）；
    2. 按迁移重建表：SET FOREIGN_KEY_CHECKS=0 后 DROP 快照内全部业务表，
       再重放 edu_core/storage/migrations/V*__*.sql（与 init_db 同源的分句执行器）；
    3. 逐表回灌：先 DELETE（表刚重建本为空，保留语义）后按快照行参数化 INSERT；
    4. 打印各表恢复行数与总耗时。

设计说明（A3）：
    - 表结构唯一执行源是 migrations（runtime_schema.sql 自 2026-09-06 起仅供阅读参考）；
    - schema_migrations 元数据表不随快照回灌：重建后保留库内原状（记录版本仍为已应用）；
    - 业务表无跨表外键依赖，回灌无需排序。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

for _parent in Path(__file__).resolve().parents:
    if (_parent / "edu_core").is_dir():
        sys.path.insert(0, str(_parent))
        break

from edu_core.config.settings import get_settings  # noqa: E402
from edu_core.storage.bootstrap import MIGRATIONS_DIR, _server_connection, _split_statements  # noqa: E402


def _to_param(v):
    """JSON 快照值 → MySQL 参数：None 原样；dict/list 重新序列化；其余转 str 交由 MySQL 隐式转换。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return v


def restore(settings, snapshot_fp: Path) -> dict:
    with open(snapshot_fp, encoding="utf-8") as f:
        snapshot = json.load(f)

    tables = snapshot.get("tables", {})
    t0 = _dt.datetime.now()

    migration_files = sorted(MIGRATIONS_DIR.glob("V*__*.sql"))
    with _server_connection(settings) as conn:
        with conn.cursor() as cur:
            # 1) 重建：关闭外键检查后 DROP 全部业务表，再重放迁移建表语句
            cur.execute("SET FOREIGN_KEY_CHECKS=0")
            for t in tables:
                cur.execute(f"DROP TABLE IF EXISTS `{t}`")
            for fp in migration_files:
                for stmt in _split_statements(fp.read_text(encoding="utf-8")):
                    if stmt.strip():
                        cur.execute(stmt)
            # 2) 回灌：逐表 DELETE 后 INSERT（参数化）
            for t, rows in tables.items():
                if not rows:
                    continue
                cols = list(rows[0].keys())
                placeholders = ", ".join(["%s"] * len(cols))
                col_sql = ", ".join(f"`{c}`" for c in cols)
                cur.execute(f"DELETE FROM `{t}`")
                cur.executemany(
                    f"INSERT INTO `{t}` ({col_sql}) VALUES ({placeholders})",
                    [tuple(_to_param(r.get(c)) for c in cols) for r in rows],
                )
            cur.execute("SET FOREIGN_KEY_CHECKS=1")

    elapsed_s = (_dt.datetime.now() - t0).total_seconds()
    summary = {t: len(rows) for t, rows in tables.items()}
    return {"elapsed_s": elapsed_s, "row_counts": summary}


def main() -> int:
    parser = argparse.ArgumentParser(description="从 JSON 快照恢复 MySQL 业务库")
    parser.add_argument("--snapshot", default=None,
                        help="快照文件路径；缺省取 backups/snapshot_*.json 中最新的")
    args = parser.parse_args()

    settings = get_settings()
    if args.snapshot:
        fp = Path(args.snapshot)
    else:
        import glob
        candidates = sorted(glob.glob(str(Path(__file__).resolve().parents[1] / "backups" / "snapshot_*.json")))
        if not candidates:
            print("未找到快照，请先运行 scripts/backup_db.py")
            return 1
        fp = Path(candidates[-1])

    print(f"恢复快照: {fp}")
    result = restore(settings, fp)
    print(f"恢复完成  总耗时: {result['elapsed_s']:.2f}s  表数: {len(result['row_counts'])}")
    for t, n in result["row_counts"].items():
        print(f"  {t}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
