"""迁移机制测试（改进计划 WP-C）。

分两层：
1. 纯逻辑：迁移文件收集 / 版本号连续性 / 语句切分（无需数据库）；
2. 集成（MySQL 可达时执行）：对临时库跑全量迁移 + 重复执行幂等 + 表齐全校验，
   MySQL 不可达时自动 skip，保证纯逻辑测试在 CI（无数据库）可跑。
"""

from __future__ import annotations

import socket

import pytest

from edu_core.config.settings import Settings, get_settings
from edu_core.storage.bootstrap import (
    MIGRATIONS_DIR,
    _migration_files,
    _split_statements,
)

SCRATCH_DB = "edu_classifier_migration_test"


# ---------------------------------------------------------------------------
# 纯逻辑
# ---------------------------------------------------------------------------

def test_migration_files_sorted_and_contiguous():
    files = _migration_files()
    versions = [v for v, _ in files]
    assert versions == list(range(1, len(versions) + 1))
    assert all(fp.suffix == ".sql" for _, fp in files)


def test_split_statements_skips_comments_only_segments():
    sql = """
    -- 纯注释段
    CREATE TABLE t1 (id INT PRIMARY KEY);
    -- 另一段注释
    -- 仍然没有语句
    ;
    INSERT INTO t1 VALUES (1);
    """
    stmts = _split_statements(sql)
    assert len(stmts) == 2
    assert stmts[0].startswith("CREATE TABLE t1")
    assert stmts[1].startswith("INSERT INTO t1")


def test_baseline_contains_core_tables():
    v1 = MIGRATIONS_DIR / "V1__baseline.sql"
    text = v1.read_text(encoding="utf-8")
    for table in ("model_versions", "active_model_pointer", "classifications",
                  "questions", "feedback", "daily_stats"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in text


# ---------------------------------------------------------------------------
# 集成（MySQL 可达时执行）
# ---------------------------------------------------------------------------

def _mysql_reachable(settings: Settings) -> bool:
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect((settings.mysql_host, settings.mysql_port))
        return True
    except OSError:
        return False
    finally:
        s.close()


@pytest.fixture()
def scratch_settings():
    settings = Settings(mysql_db=SCRATCH_DB)
    if not _mysql_reachable(settings):
        pytest.skip("MySQL 不可达，跳过迁移集成测试")
    return settings


def _drop_scratch_db(settings: Settings) -> None:
    import pymysql
    with pymysql.connect(
        host=settings.mysql_host, port=settings.mysql_port,
        user=settings.mysql_user, password=settings.mysql_password,
        charset="utf8mb4", autocommit=True,
    ) as conn, conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS `{SCRATCH_DB}`")


def test_migrations_fresh_run_and_idempotent(scratch_settings):
    from edu_core.storage.bootstrap import apply_pending_migrations

    try:
        summary = apply_pending_migrations(scratch_settings)
        assert summary["migrations_applied_now"] == summary["migrations_total"]

        # 重复执行：全部已应用，无新增
        summary2 = apply_pending_migrations(scratch_settings)
        assert summary2["migrations_applied_now"] == []

        # 表齐全校验
        import pymysql
        with pymysql.connect(
            host=scratch_settings.mysql_host, port=scratch_settings.mysql_port,
            user=scratch_settings.mysql_user, password=scratch_settings.mysql_password,
            database=SCRATCH_DB, charset="utf8mb4", autocommit=True,
        ) as conn, conn.cursor() as cur:
            cur.execute("SHOW TABLES")
            tables = {row[0] for row in cur.fetchall()}
        expected = {"schema_migrations", "model_versions", "active_model_pointer",
                    "classifications", "questions", "feedback", "daily_stats"}
        assert expected <= tables
    finally:
        _drop_scratch_db(scratch_settings)


def test_migration_failure_not_recorded_and_recoverable(scratch_settings, monkeypatch, tmp_path):
    """坏迁移：失败后版本不得记录，修复后重放可恢复（MySQL DDL 隐式提交，语义见 bootstrap.py）。"""
    import shutil

    import pymysql

    from edu_core.storage import bootstrap as bootstrap_mod
    from edu_core.storage.bootstrap import apply_pending_migrations
    try:
        # 先正常应用基线
        apply_pending_migrations(scratch_settings)

        # 构造临时迁移目录：真实 V1 + 必然失败的 V2（版本号连续，走真实 SQL 失败路径）
        shutil.copy(MIGRATIONS_DIR / "V1__baseline.sql", tmp_path / "V1__baseline.sql")
        (tmp_path / "V2__bad.sql").write_text("THIS IS NOT VALID SQL;", encoding="utf-8")
        monkeypatch.setattr(bootstrap_mod, "MIGRATIONS_DIR", tmp_path)

        with pytest.raises(pymysql.err.Error):
            apply_pending_migrations(scratch_settings)

        with pymysql.connect(
            host=scratch_settings.mysql_host, port=scratch_settings.mysql_port,
            user=scratch_settings.mysql_user, password=scratch_settings.mysql_password,
            database=SCRATCH_DB, charset="utf8mb4", autocommit=True,
        ) as conn, conn.cursor() as cur:
            cur.execute("SELECT version FROM schema_migrations WHERE version = 'V2'")
            assert cur.fetchone() is None, "失败迁移不得记录版本号"

        # 修复后重放：替换为合法 V2，成功应用并记录
        (tmp_path / "V2__bad.sql").unlink()
        (tmp_path / "V2__recovered.sql").write_text(
            "CREATE TABLE IF NOT EXISTS recovered_tbl (id INT PRIMARY KEY);", encoding="utf-8")
        summary = apply_pending_migrations(scratch_settings)
        assert summary["migrations_applied_now"] == ["V2"]

        with pymysql.connect(
            host=scratch_settings.mysql_host, port=scratch_settings.mysql_port,
            user=scratch_settings.mysql_user, password=scratch_settings.mysql_password,
            database=SCRATCH_DB, charset="utf8mb4", autocommit=True,
        ) as conn, conn.cursor() as cur:
            cur.execute("SHOW TABLES LIKE 'recovered_tbl'")
            assert cur.fetchone() is not None
    finally:
        _drop_scratch_db(scratch_settings)
