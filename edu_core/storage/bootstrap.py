"""MySQL schema bootstrap：建库（如不存在）+ 执行 runtime_schema.sql。

设计对齐 knowforge-rag-platform：
- 表结构集中在 runtime_schema.sql 一个文件里，便于阅读与评审；
- bootstrap 只负责"建库 + 执行 DDL"，业务读写全部走各 Store；
- 启动顺序：validate_runtime_environment -> bootstrap_mysql_schema
  -> validate_active_model_version -> 模型预热。
"""

from __future__ import annotations

from pathlib import Path

import pymysql

from edu_core.config.logging_config import get_logger
from edu_core.config.settings import Settings

logger = get_logger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parent / "runtime_schema.sql"


def _server_connection(settings: Settings, with_db: bool = True):
    """服务器级连接（with_db=False 时不选库，用于 CREATE DATABASE）。"""
    return pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_db if with_db else None,
        charset="utf8mb4",
        autocommit=True,
    )


def bootstrap_mysql_schema(settings: Settings) -> dict:
    """建库（IF NOT EXISTS）+ 执行集中 DDL。返回执行摘要。"""
    # 1) 建库
    with _server_connection(settings, with_db=False) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{settings.mysql_db}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    logger.info("MySQL database ready: %s@%s:%s",
                settings.mysql_db, settings.mysql_host, settings.mysql_port)

    # 2) 执行 DDL（按分号切分语句；SQL 文件内不含存储过程，简单切分即可）
    statements = [s.strip() for s in SCHEMA_PATH.read_text(encoding="utf-8").split(";")]
    executed = 0
    with _server_connection(settings) as conn:
        with conn.cursor() as cur:
            for stmt in statements:
                if not stmt or stmt.lstrip().startswith("--"):
                    # 跳过纯注释段（注释后仍可能跟语句，按是否含关键字判断）
                    body = "\n".join(
                        ln for ln in stmt.splitlines() if not ln.strip().startswith("--")
                    ).strip()
                    if not body:
                        continue
                    stmt = body
                cur.execute(stmt)
                executed += 1
    summary = {
        "database": settings.mysql_db,
        "schema_file": str(SCHEMA_PATH),
        "statements_executed": executed,
    }
    logger.info("Runtime MySQL schema bootstrap passed: %s", summary)
    return summary
