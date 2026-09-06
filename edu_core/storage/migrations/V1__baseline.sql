-- ============================================================
-- V1__baseline：初始基线（6 张业务表）
-- ------------------------------------------------------------
-- 内容 = 初版 runtime_schema.sql 快照（2026-09-06 定格）。
-- 本目录（migrations/）自此成为表结构的唯一执行来源：
--   - 变更表结构时新增 V{n}__{描述}.sql 增量脚本，绝不修改已应用的脚本；
--   - 已应用版本记录于 schema_migrations 表，重复执行幂等；
--   - runtime_schema.sql 退役为"当前全量参考视图"，仅供阅读评审。
-- 对齐 knowforge-rag-platform：不引入 Alembic，DDL 集中管理。
-- ============================================================

-- 模型版本注册表（仿知识库版本状态机：STAGED / ACTIVE / ARCHIVED）
CREATE TABLE IF NOT EXISTS model_versions (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    version VARCHAR(64) NOT NULL UNIQUE,
    directory VARCHAR(512) NOT NULL COMMENT '版本目录（项目相对路径）',
    manifest_json JSON NULL COMMENT 'manifest 快照（backbone_ref/标签规模等）',
    metrics_json JSON NULL COMMENT '评估指标（学科acc/题型F1/知识点F1/级联acc/延迟）',
    status VARCHAR(16) NOT NULL DEFAULT 'STAGED' COMMENT 'STAGED/ACTIVE/ARCHIVED',
    description VARCHAR(512) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_model_versions_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- active 版本指针（单行表，语义与 knowforge 的 kb active 指针一致）
CREATE TABLE IF NOT EXISTS active_model_pointer (
    id TINYINT PRIMARY KEY CHECK (id = 1),
    active_version VARCHAR(64) NOT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 分类留痕：每次推理一条记录（反馈必须关联到具体推理与模型版本）
CREATE TABLE IF NOT EXISTS classifications (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    text_hash CHAR(64) NOT NULL COMMENT 'sha256(text)，用于反馈关联与查重辅助',
    text_preview VARCHAR(500) NOT NULL COMMENT '题目前 500 字符（审计可读）',
    model_version VARCHAR(64) NOT NULL,
    subject_pred VARCHAR(64) NOT NULL,
    subject_conf DECIMAL(6,4) NOT NULL,
    type_pred VARCHAR(64) NOT NULL,
    type_conf DECIMAL(6,4) NOT NULL,
    knowledge_pred VARCHAR(128) NOT NULL,
    knowledge_conf DECIMAL(6,4) NOT NULL,
    avg_confidence DECIMAL(6,4) NOT NULL,
    confidence_band VARCHAR(16) NOT NULL COMMENT 'high/medium/low',
    latency_ms DECIMAL(10,2) NOT NULL DEFAULT 0,
    cached TINYINT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_classifications_created (created_at),
    INDEX idx_classifications_version (model_version),
    INDEX idx_classifications_hash (text_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 题库（替代旧版内存 question_db，重启不丢）
CREATE TABLE IF NOT EXISTS questions (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    content TEXT NOT NULL,
    subject VARCHAR(64) NOT NULL DEFAULT '',
    question_type VARCHAR(64) NOT NULL DEFAULT '',
    knowledge_point VARCHAR(128) NOT NULL DEFAULT '',
    source VARCHAR(64) NOT NULL DEFAULT 'manual' COMMENT 'manual/ai_input/import',
    status VARCHAR(16) NOT NULL DEFAULT 'published' COMMENT 'draft/published/archived',
    text_hash CHAR(64) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_questions_subject (subject),
    INDEX idx_questions_type (question_type),
    INDEX idx_questions_hash (text_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 反馈：外键关联 classification（修复旧版反馈无关联、无法用于再训练的缺陷）
CREATE TABLE IF NOT EXISTS feedback (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    classification_id BIGINT NULL COMMENT '关联的推理记录；历史导入数据可为空',
    is_correct TINYINT NOT NULL,
    question_text VARCHAR(500) NULL COMMENT '兼容旧数据的题目文本',
    subject VARCHAR(64) NULL,
    corrected_subject VARCHAR(64) NULL,
    corrected_type VARCHAR(64) NULL,
    corrected_knowledge VARCHAR(128) NULL,
    comment VARCHAR(1024) NULL,
    model_version VARCHAR(64) NULL COMMENT '反馈时分类所用模型版本',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_feedback_classification (classification_id),
    INDEX idx_feedback_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 按日累计统计（修复旧版 stats.json 单日覆盖丢历史的缺陷）
CREATE TABLE IF NOT EXISTS daily_stats (
    stat_date DATE PRIMARY KEY,
    total_processed BIGINT NOT NULL DEFAULT 0,
    total_correct BIGINT NOT NULL DEFAULT 0,
    total_wrong BIGINT NOT NULL DEFAULT 0,
    avg_confidence DECIMAL(6,4) NOT NULL DEFAULT 0,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
