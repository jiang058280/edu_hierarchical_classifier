-- 本地题库知识源：只镜像已发布题目，不调用 Embedding 或聊天模型。
CREATE TABLE IF NOT EXISTS rag_question_knowledge (
    question_id BIGINT PRIMARY KEY,
    question_text MEDIUMTEXT NOT NULL,
    options_json JSON NULL,
    answer TEXT NULL,
    analysis TEXT NULL,
    subject VARCHAR(64) NOT NULL DEFAULT '',
    question_type VARCHAR(64) NOT NULL DEFAULT '',
    knowledge_point VARCHAR(128) NOT NULL DEFAULT '',
    grade_band VARCHAR(16) NULL,
    grade VARCHAR(32) NULL,
    source_hash CHAR(64) NOT NULL,
    synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_rag_question_knowledge_filter (subject, grade_band, knowledge_point)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
