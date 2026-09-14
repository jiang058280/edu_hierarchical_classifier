CREATE TABLE IF NOT EXISTS rag_sessions (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT NOT NULL, role VARCHAR(16) NOT NULL,
    title VARCHAR(128) NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_rag_sessions_user (user_id, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rag_messages (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, session_id BIGINT NOT NULL, role VARCHAR(16) NOT NULL,
    content MEDIUMTEXT NOT NULL, citations_json JSON NULL, refused TINYINT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, INDEX idx_rag_messages_session (session_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rag_query_traces (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, session_id BIGINT NULL, user_id BIGINT NOT NULL,
    kb_version VARCHAR(64) NULL, query_text TEXT NOT NULL, candidates_json JSON NULL,
    latency_ms INT NULL, failure_stage VARCHAR(32) NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_rag_traces_user (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rag_feedback (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, message_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
    rating TINYINT NOT NULL, correction TEXT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_rag_feedback_user_message (message_id, user_id), INDEX idx_rag_feedback_message (message_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
