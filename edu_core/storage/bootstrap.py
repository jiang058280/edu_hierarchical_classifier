"""MySQL schema 迁移执行器：建库（如不存在）+ 按序应用 edu_core/storage/migrations/V*__*.sql。

设计（改进计划 WP-C，替代"单文件 runtime_schema.sql 全量执行"）：

- 迁移脚本目录：``migrations/V1__baseline.sql`` 起步（= 初版 runtime_schema.sql 快照），
  后续表结构变更一律新增 ``V{n}__{描述}.sql`` 增量脚本，只写差异；
- 已应用版本记录在 ``schema_migrations`` 表（version PRIMARY KEY, applied_at），
  重复启动/重复执行 init_db 均幂等：只应用未记录的版本；
- 失败语义（MySQL DDL 隐式提交、不可回滚，故保证为）：
  版本号只在**全部语句成功后**写入 schema_migrations；任一语句失败即中止并抛出，
  该版本不记录，下次启动自动重放 —— 因此迁移脚本必须写成可重入形式
  （DDL 一律带 IF NOT EXISTS / IF EXISTS，DML 需自行保证可重放）；
- ``runtime_schema.sql`` 退役为"当前全量结构参考视图"：每次新增迁移后手动同步，
  仅供阅读与评审，不再被代码执行。

启动顺序：validate_runtime_environment -> bootstrap_mysql_schema
  -> validate_active_model_version -> 模型预热。
"""

from __future__ import annotations

import re
from pathlib import Path

import pymysql

from edu_core.config.logging_config import get_logger
from edu_core.config.settings import Settings

logger = get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
# 参考视图（不再执行）：新增迁移后手动同步，保持与 migrations 应用结果一致
REFERENCE_SCHEMA_PATH = Path(__file__).resolve().parent / "runtime_schema.sql"

_VERSION_RE = re.compile(r"^V(\d+)__[\w\-]+\.sql$")


def _server_connection(settings: Settings, with_db: bool = True, autocommit: bool = True):
    """服务器级连接（with_db=False 时不选库，用于 CREATE DATABASE）。"""
    return pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_db if with_db else None,
        charset="utf8mb4",
        autocommit=autocommit,
    )


def _split_statements(sql_text: str) -> list[str]:
    """按分号切分语句；跳过纯注释段（SQL 文件内不含存储过程，简单切分即可）。"""
    statements: list[str] = []
    for raw in sql_text.split(";"):
        body = "\n".join(
            ln for ln in raw.splitlines() if not ln.strip().startswith("--")
        ).strip()
        if body:
            statements.append(body)
    return statements


def _migration_files() -> list[tuple[int, Path]]:
    """收集并按版本号排序全部迁移脚本。"""
    found: list[tuple[int, Path]] = []
    for fp in MIGRATIONS_DIR.glob("V*__*.sql"):
        match = _VERSION_RE.match(fp.name)
        if not match:
            raise ValueError(f"迁移脚本命名不合规（应为 V{{n}}__{{描述}}.sql）：{fp.name}")
        found.append((int(match.group(1)), fp))
    found.sort(key=lambda x: x[0])
    if not found:
        raise FileNotFoundError(f"迁移目录为空：{MIGRATIONS_DIR}")
    # 版本号必须连续（防止漏拷贝中间脚本）
    for idx, (version, _) in enumerate(found, start=1):
        if version != idx:
            raise ValueError(f"迁移版本号不连续：期望 V{idx}，实际 V{version}（{found[idx-1][1].name}）")
    return found


def _applied_versions(cur) -> set[str]:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version VARCHAR(64) PRIMARY KEY,
            applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)
    cur.execute("SELECT version FROM schema_migrations")
    return {row[0] for row in cur.fetchall()}


def apply_pending_migrations(settings: Settings) -> dict:
    """建库（IF NOT EXISTS）+ 应用未执行的迁移。返回执行摘要（含本次应用的版本列表）。"""
    # 1) 建库
    with _server_connection(settings, with_db=False) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{settings.mysql_db}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    logger.info("MySQL database ready: %s@%s:%s",
                settings.mysql_db, settings.mysql_host, settings.mysql_port)

    # 2) 按序应用未执行的迁移（版本记录在全部语句成功后写入，保证幂等重放）
    migrations = _migration_files()
    applied_now: list[str] = []
    with _server_connection(settings, autocommit=False) as conn:
        try:
            with conn.cursor() as cur:
                applied = _applied_versions(cur)
                for version, fp in migrations:
                    version_str = f"V{version}"
                    if version_str in applied:
                        continue
                    statements = _split_statements(fp.read_text(encoding="utf-8"))
                    for stmt in statements:
                        cur.execute(stmt)
                    cur.execute(
                        "INSERT INTO schema_migrations (version) VALUES (%s)", (version_str,))
                    applied_now.append(version_str)
                    logger.info("迁移已应用：%s（%d 条语句）", fp.name, len(statements))
            conn.commit()
        except Exception:
            conn.rollback()  # MySQL DDL 会隐式提交；此处回滚的是同批 DML，版本未记录即重放入口
            raise

    summary = {
        "database": settings.mysql_db,
        "migrations_total": [f"V{v}" for v, _ in migrations],
        "migrations_applied_now": applied_now,
        "migrations_dir": str(MIGRATIONS_DIR),
    }
    if applied_now:
        logger.info("MySQL migrations applied: %s", applied_now)
    else:
        logger.info("MySQL migrations up-to-date: %s", summary["migrations_total"])
    return summary


def bootstrap_mysql_schema(settings: Settings) -> dict:
    """兼容入口（app.py lifespan / init_db 调用）：等价于 apply_pending_migrations。"""
    return apply_pending_migrations(settings)
