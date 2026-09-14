"""全局配置测试：默认值、环境变量覆盖、派生属性。"""

from edu_core.config.settings import Settings


def test_defaults():
    s = Settings(_env_file=None)
    assert s.mysql_db == "edu_classifier"
    assert s.max_seq_length == 256
    assert s.classify_max_chars == 4000
    assert s.confidence_high == 0.80 and s.confidence_medium == 0.60
    assert s.gate_min_subject_acc == 0.85
    assert s.rag_enabled is False
    assert s.rag_documents_collection == "edu_rag_documents"
    assert s.rag_top_k == 12 and s.rag_rerank_top_k == 5
    assert s.rag_enabled is False
    assert s.rag_documents_collection == "edu_rag_documents"
    assert s.rag_top_k == 12 and s.rag_rerank_top_k == 5
    # 不允许默认 CORS 通配符
    assert "*" not in s.cors_origins


def test_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("EDU_MYSQL_HOST", "10.0.0.8")
    monkeypatch.setenv("EDU_MYSQL_PORT", "3307")
    monkeypatch.setenv("EDU_CLASSIFY_MAX_CHARS", "800")
    monkeypatch.setenv("EDU_RAG_ENABLED", "true")
    monkeypatch.setenv("EDU_RAG_TOP_K", "8")
    monkeypatch.setenv("EDU_RAG_EMBEDDING_MODEL", "text-embedding-test")
    monkeypatch.setenv("EDU_RAG_ENABLED", "true")
    monkeypatch.setenv("EDU_RAG_TOP_K", "8")
    monkeypatch.setenv("EDU_RAG_EMBEDDING_MODEL", "text-embedding-test")
    s = Settings(_env_file=None)
    assert s.mysql_host == "10.0.0.8"
    assert s.mysql_port == 3307
    assert s.classify_max_chars == 800
    assert s.rag_enabled is True
    assert s.rag_top_k == 8
    assert s.rag_embedding_model == "text-embedding-test"
    assert s.rag_enabled is True
    assert s.rag_top_k == 8
    assert s.rag_embedding_model == "text-embedding-test"


def test_mysql_url_and_cors_parsing():
    s = Settings(_env_file=None, mysql_user="u1", mysql_password="p1",
                 mysql_host="h1", mysql_port=3306, mysql_db="db1",
                 cors_allow_origins="http://a.com, http://b.com")
    assert s.mysql_url == "mysql+pymysql://u1:p1@h1:3306/db1?charset=utf8mb4"
    assert s.cors_origins == ["http://a.com", "http://b.com"]


def test_abs_path_resolution():
    s = Settings(_env_file=None)
    p = s.abs_path("models/versions")
    assert p.is_absolute()
    assert "models" in str(p) and "versions" in str(p)
