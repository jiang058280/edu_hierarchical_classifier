"""R1.3 知识库 Store 集成测试；独立临时库，绝不碰开发资料。"""

from __future__ import annotations

import socket

import pytest
from sqlalchemy import create_engine, text

from edu_core.config.settings import Settings
from edu_core.storage.bootstrap import apply_pending_migrations
from edu_core.storage.stores import RagKnowledgeBaseStore


DB_NAME = "edu_classifier_rag_store_test"


def _reachable(settings: Settings) -> bool:
    with socket.socket() as sock:
        sock.settimeout(2)
        try:
            sock.connect((settings.mysql_host, settings.mysql_port))
        except OSError:
            return False
    return True


@pytest.fixture()
def rag_store():
    # 继承项目 .env：本机 Docker MySQL 映射端口由该配置决定。
    settings = Settings(mysql_db=DB_NAME)
    if not _reachable(settings):
        pytest.skip("MySQL 不可达，跳过知识库 Store 集成测试")
    _drop_test_db(settings)
    apply_pending_migrations(settings)
    engine = create_engine(settings.mysql_url, future=True)
    try:
        yield RagKnowledgeBaseStore(engine)
    finally:
        engine.dispose()
        _drop_test_db(settings)


def _drop_test_db(settings: Settings) -> None:
    import pymysql
    with pymysql.connect(host=settings.mysql_host, port=settings.mysql_port,
                         user=settings.mysql_user, password=settings.mysql_password,
                         charset="utf8mb4", autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS `{DB_NAME}`")


def test_kb_version_document_job_and_chunks_lifecycle(rag_store):
    version_id = rag_store.create_version("kb-r1-store-test", created_by=101, description="测试版本")
    assert rag_store.get_version(version_id)["status"] == "STAGED"
    with pytest.raises(ValueError, match="质量检查"):
        rag_store.activate_version(version_id)

    assert rag_store.set_quality_report(version_id, {"passed": True, "chunk_count": 2})
    assert rag_store.activate_version(version_id)["status"] == "ACTIVE"
    assert rag_store.get_active_version()["id"] == version_id

    document_id = rag_store.create_document(
        kb_version_id=version_id, created_by=101, source_name="一次函数讲义.md",
        source_type="markdown", content_hash="a" * 64, subject="数学",
        grade_band="初中", grade="初三")
    document = rag_store.get_document(document_id)
    assert document["allowed_roles"] == ["student", "teacher", "admin"]
    assert rag_store.find_reusable_document(created_by=101, content_hash="a" * 64) is None

    job_id = rag_store.create_job(document_id=document_id, kb_version_id=version_id, requested_by=101)
    assert rag_store.update_job(job_id, status="RUNNING", attempt_no=1)
    chunk_ids = rag_store.replace_chunks(document_id, version_id, [
        {"chunk_kind": "parent", "order_no": 1, "content": "一次函数的概念。",
         "content_hash": "b" * 64, "metadata": {"source_name": "一次函数讲义.md"}},
        {"chunk_kind": "child", "order_no": 1, "content": "y=kx+b 是一次函数。",
         "content_hash": "c" * 64, "parent_order_no": 1, "metadata": {"chapter": "第一章"}},
    ])
    assert len(chunk_ids) == 2
    children = rag_store.list_document_chunks(document_id, chunk_kind="child")
    assert children[0]["parent_chunk_id"] == chunk_ids[0]
    assert rag_store.update_job(job_id, status="SUCCEEDED", metrics={"chunk_count": 2})


def test_replacing_chunks_rejects_published_document(rag_store):
    version_id = rag_store.create_version("kb-r1-published-test", created_by=102)
    document_id = rag_store.create_document(
        kb_version_id=version_id, created_by=102, source_name="资料.txt", source_type="txt",
        content_hash="d" * 64)
    with rag_store.engine.begin() as conn:
        conn.execute(text("UPDATE rag_documents SET status = 'PUBLISHED' WHERE id = :id"),
                     {"id": document_id})
    with pytest.raises(ValueError, match="已发布"):
        rag_store.replace_chunks(document_id, version_id, [])


def test_document_and_job_require_matching_version(rag_store):
    version_id = rag_store.create_version("kb-r1-version-guard", created_by=103)
    other_version_id = rag_store.create_version("kb-r1-version-other", created_by=103)
    document_id = rag_store.create_document(
        kb_version_id=version_id, created_by=103, source_name="资料.txt", source_type="txt",
        content_hash="e" * 64)
    with pytest.raises(ValueError, match="不属于"):
        rag_store.create_job(document_id=document_id, kb_version_id=other_version_id, requested_by=103)
    with pytest.raises(ValueError, match="不属于"):
        rag_store.replace_chunks(document_id, other_version_id, [])


def test_publication_and_quality_report_lifecycle(rag_store):
    version_id = rag_store.create_version("kb-r1-publication-test", created_by=104)
    assert rag_store.build_quality_report(version_id, created_by=104)["passed"] is False
    document_id = rag_store.create_document(
        kb_version_id=version_id, created_by=104, source_name="函数.md", source_type="markdown",
        content_hash="f" * 64)
    rag_store.replace_chunks(document_id, version_id, [
        {"chunk_kind": "parent", "order_no": 1, "content": "函数概念", "content_hash": "1" * 64},
        {"chunk_kind": "child", "order_no": 1, "parent_order_no": 1,
         "content": "函数的定义", "content_hash": "2" * 64},
    ])
    rag_store.update_document_status(document_id, status="PROCESSED")
    published = rag_store.set_document_publication(document_id, created_by=104, published=True)
    assert published["status"] == "PUBLISHED"
    assert rag_store.list_document_chunks(document_id, chunk_kind="child")[0]["status"] == "PUBLISHED"
    child_id = rag_store.list_document_chunks(document_id, chunk_kind="child")[0]["id"]
    hits = rag_store.get_retrieval_chunks([child_id], kb_version_id=version_id, role="student", subject="数学")
    assert hits == []  # 本资料未标注数学，学科硬过滤不得放行
    with rag_store.engine.begin() as conn:
        conn.execute(text("UPDATE rag_documents SET subject = '数学', allowed_roles_json = JSON_ARRAY('student') WHERE id = :id"), {"id": document_id})
    assert len(rag_store.get_retrieval_chunks([child_id], kb_version_id=version_id, role="student", subject="数学")) == 1
    assert rag_store.get_retrieval_chunks([child_id], kb_version_id=version_id, role="teacher", subject="数学") == []
    report = rag_store.build_quality_report(version_id, created_by=104)
    assert report["passed"] is True and report["child_chunk_count"] == 1
    unpublished = rag_store.set_document_publication(document_id, created_by=104, published=False)
    assert unpublished["status"] == "UNPUBLISHED"
    with pytest.raises(ValueError, match="仅已发布"):
        rag_store.set_document_publication(document_id, created_by=104, published=False)
    archived = rag_store.archive_document(document_id, created_by=104)
    assert archived["status"] == "ARCHIVED"
    assert rag_store.list_document_chunks(document_id, chunk_kind="child")[0]["status"] == "ARCHIVED"


def test_active_version_can_roll_back_to_prior_archived_version(rag_store):
    first = rag_store.create_version("kb-r1-rollback-first", created_by=105)
    second = rag_store.create_version("kb-r1-rollback-second", created_by=105)
    rag_store.set_quality_report(first, {"passed": True})
    rag_store.set_quality_report(second, {"passed": True})
    rag_store.activate_version(first)
    rag_store.activate_version(second)
    assert rag_store.get_version(first)["status"] == "ARCHIVED"
    rag_store.activate_version(first)
    assert rag_store.get_active_version()["id"] == first


def test_delete_document_removes_related_records_and_preserves_shared_storage(rag_store):
    version_id = rag_store.create_version("kb-r1-delete-test", created_by=106)
    first = rag_store.create_document(
        kb_version_id=version_id, created_by=106, source_name="资料.txt", source_type="txt",
        content_hash="9" * 64, storage_key="data/rag_uploads/106/shared.txt")
    second = rag_store.create_document(
        kb_version_id=version_id, created_by=106, source_name="资料副本.txt", source_type="txt",
        content_hash="8" * 64, storage_key="data/rag_uploads/106/shared.txt")
    rag_store.create_job(document_id=first, kb_version_id=version_id, requested_by=106)
    chunk_ids = rag_store.replace_chunks(first, version_id, [{
        "chunk_kind": "parent", "order_no": 1, "content": "一次函数", "content_hash": "7" * 64,
    }])
    cleanup = rag_store.delete_document(first, created_by=106)
    assert cleanup["chunk_ids"] == chunk_ids
    assert cleanup["delete_storage"] is False
    assert rag_store.get_document(first) is None
    assert rag_store.get_document(second) is not None
    with rag_store.engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM rag_ingestion_jobs WHERE document_id = :id"), {"id": first}).scalar_one() == 0


def test_sync_published_questions_creates_local_token_free_knowledge_source(rag_store):
    with rag_store.engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO questions (content, subject, question_type, knowledge_point, source, text_hash, status, answer, analysis)
            VALUES ('已知 f(x)=x+1，求 f(2)。', '数学', '解答题', '数学::一次函数', 'manual', :hash, 'published', '3', '代入计算')
        """), {"hash": "b" * 64})
        conn.execute(text("""
            INSERT INTO questions (content, subject, source, text_hash, status)
            VALUES ('草稿题', '数学', 'manual', :hash, 'draft')
        """), {"hash": "c" * 64})
    result = rag_store.sync_published_questions()
    assert result["synced"] == 1
    rows = rag_store.list_local_question_knowledge(subject="数学")
    assert len(rows) == 1 and rows[0]["answer"] == "3" and rows[0]["analysis"] == "代入计算"
