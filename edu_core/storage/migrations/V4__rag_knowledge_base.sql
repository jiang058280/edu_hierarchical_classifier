-- ============================================================
-- V4__rag_knowledge_base：教育知识库（R1）
-- MySQL 是资料、分块、版本与任务的唯一事实源；Milvus 只保存最小检索元数据。
-- ============================================================

CREATE TABLE IF NOT EXISTS rag_kb_versions (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    version VARCHAR(64) NOT NULL UNIQUE COMMENT '知识库版本号，如 kb-20260909-001',
    status VARCHAR(16) NOT NULL DEFAULT 'STAGED' COMMENT 'STAGED/ACTIVE/ARCHIVED',
    description VARCHAR(512) NULL,
    quality_report_json JSON NULL COMMENT '入库质量检查与统计',
    created_by BIGINT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    activated_at DATETIME NULL,
    archived_at DATETIME NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_rag_kb_versions_status (status),
    INDEX idx_rag_kb_versions_creator (created_by)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rag_active_kb_pointer (
    id TINYINT PRIMARY KEY COMMENT '固定单行，id=1',
    active_version_id BIGINT NOT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rag_documents (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    kb_version_id BIGINT NOT NULL,
    created_by BIGINT NOT NULL COMMENT '资料所属教师',
    source_name VARCHAR(255) NOT NULL,
    source_type VARCHAR(16) NOT NULL COMMENT 'pdf/docx/markdown/txt/question_bank',
    storage_key VARCHAR(512) NULL COMMENT '项目相对存储键，不能写绝对路径',
    mime_type VARCHAR(128) NULL,
    file_size BIGINT NULL,
    content_hash CHAR(64) NOT NULL COMMENT '原文件或规范化题库内容 SHA-256',
    subject VARCHAR(32) NULL,
    grade_band VARCHAR(8) NULL,
    grade VARCHAR(16) NULL,
    knowledge_node_id BIGINT NULL,
    allowed_roles_json JSON NULL COMMENT '默认 student/teacher/admin',
    status VARCHAR(16) NOT NULL DEFAULT 'STAGED' COMMENT 'STAGED/PROCESSING/PROCESSED/PUBLISHED/UNPUBLISHED/FAILED/ARCHIVED',
    failure_reason VARCHAR(1024) NULL,
    published_at DATETIME NULL,
    archived_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_rag_documents_version (kb_version_id),
    INDEX idx_rag_documents_creator (created_by),
    INDEX idx_rag_documents_hash (content_hash),
    INDEX idx_rag_documents_status (status),
    INDEX idx_rag_documents_filter (subject, grade_band, grade)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rag_document_chunks (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    document_id BIGINT NOT NULL,
    kb_version_id BIGINT NOT NULL,
    parent_chunk_id BIGINT NULL,
    chunk_kind VARCHAR(16) NOT NULL COMMENT 'parent/child',
    order_no INT NOT NULL,
    content MEDIUMTEXT NOT NULL,
    content_hash CHAR(64) NOT NULL,
    chapter VARCHAR(255) NULL,
    page_number INT NULL,
    char_start INT NULL,
    char_end INT NULL,
    metadata_json JSON NULL COMMENT '向量索引所需最小 metadata 的 MySQL 镜像',
    status VARCHAR(16) NOT NULL DEFAULT 'STAGED' COMMENT 'STAGED/PUBLISHED/ARCHIVED',
    valid_from DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    valid_to DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_rag_chunk_order (document_id, kb_version_id, chunk_kind, order_no),
    INDEX idx_rag_chunks_document (document_id),
    INDEX idx_rag_chunks_parent (parent_chunk_id),
    INDEX idx_rag_chunks_version_status (kb_version_id, status),
    INDEX idx_rag_chunks_hash (content_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rag_ingestion_jobs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    document_id BIGINT NOT NULL,
    kb_version_id BIGINT NOT NULL,
    requested_by BIGINT NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'QUEUED' COMMENT 'QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELED',
    attempt_no INT NOT NULL DEFAULT 0,
    error_code VARCHAR(64) NULL,
    error_message VARCHAR(1024) NULL,
    metrics_json JSON NULL COMMENT '文件数、chunk数、复用数、失败数等',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at DATETIME NULL,
    finished_at DATETIME NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_rag_jobs_document (document_id),
    INDEX idx_rag_jobs_version_status (kb_version_id, status),
    INDEX idx_rag_jobs_requester (requested_by)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
