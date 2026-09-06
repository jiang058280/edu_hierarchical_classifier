-- ============================================================
-- Edu Hierarchical Classifier 运行时表结构 · 当前全量参考视图
-- ------------------------------------------------------------
-- ⚠️ 本文件自 2026-09-06 起退役为"只读参考"：代码不再执行它。
-- 表结构的唯一执行来源是 edu_core/storage/migrations/V*__*.sql：
--   - 变更表结构时新增 V{n}__{描述}.sql 增量脚本；
--   - 每次新增迁移后，手动同步本文件使其保持"应用全部迁移后的全量视图"；
--   - 已应用版本记录于 schema_migrations 表（见 bootstrap.py）。
-- 设计对齐 knowforge-rag-platform：不引入 Alembic，DDL 集中管理。
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

-- schema_migrations（迁移登记表，bootstrap.py 维护）
CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(64) PRIMARY KEY,
    applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================
-- V3__platform_base（智慧教研平台基座，2026-09-06）——以下为增量部分
-- users 扩展列：real_name / grade_band / grade / class_id / must_change_password / student_no
-- questions 扩展列：options_json / answer / analysis / difficulty / grade_band / grade / knowledge_node_id / created_by
-- ============================================================

-- 班级
CREATE TABLE IF NOT EXISTS classes (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(64) NOT NULL COMMENT '班级名，如 初三(2)班',
    grade_band VARCHAR(8) NOT NULL COMMENT '初中/高中',
    grade VARCHAR(16) NOT NULL COMMENT '年级',
    invite_code CHAR(6) NOT NULL UNIQUE COMMENT '学生入班邀请码',
    created_by BIGINT NOT NULL COMMENT '创建教师',
    is_active TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_classes_teacher (created_by)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 知识点树（先二级：学科 → 一级知识点；parent_id 预留章节细化）
CREATE TABLE IF NOT EXISTS knowledge_nodes (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    parent_id BIGINT NULL,
    subject VARCHAR(32) NOT NULL,
    grade_band VARCHAR(8) NULL COMMENT 'NULL=通用（初高中共用）',
    name VARCHAR(128) NOT NULL,
    level TINYINT NOT NULL DEFAULT 1,
    is_active TINYINT NOT NULL DEFAULT 1,
    UNIQUE KEY uq_node (subject, grade_band, name, level)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 试卷
CREATE TABLE IF NOT EXISTS papers (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    title VARCHAR(128) NOT NULL,
    subject VARCHAR(32) NOT NULL,
    grade_band VARCHAR(8) NOT NULL,
    created_by BIGINT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_papers_creator (created_by)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS paper_questions (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    paper_id BIGINT NOT NULL,
    question_id BIGINT NOT NULL,
    order_no INT NOT NULL,
    score DECIMAL(5,1) NOT NULL DEFAULT 0 COMMENT '0=按题型默认分',
    UNIQUE KEY uq_paper_question (paper_id, question_id),
    INDEX idx_paper_questions_paper (paper_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 作业/考试
CREATE TABLE IF NOT EXISTS assignments (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    paper_id BIGINT NOT NULL,
    class_id BIGINT NOT NULL,
    title VARCHAR(128) NOT NULL,
    mode VARCHAR(16) NOT NULL DEFAULT 'homework' COMMENT 'homework/exam',
    due_at DATETIME NULL,
    allow_self_check TINYINT NOT NULL DEFAULT 1 COMMENT '主观题自评开关',
    created_by BIGINT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_assignments_class (class_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 学生提交（一人一次作业一条）
CREATE TABLE IF NOT EXISTS submissions (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    assignment_id BIGINT NOT NULL,
    student_id BIGINT NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'in_progress' COMMENT 'in_progress/submitted/checked',
    auto_score DECIMAL(6,1) NULL,
    final_score DECIMAL(6,1) NULL,
    submitted_at DATETIME NULL,
    UNIQUE KEY uq_submission (assignment_id, student_id),
    INDEX idx_submissions_student (student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 作答记录（学情数据底座；练习/重练也写此表，source 区分）
CREATE TABLE IF NOT EXISTS answer_records (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    submission_id BIGINT NULL COMMENT '作业/考试作答；自主练习为 NULL',
    student_id BIGINT NOT NULL,
    question_id BIGINT NOT NULL,
    answer VARCHAR(512) NULL,
    is_correct TINYINT NULL COMMENT '客观题判分；自评主观题 1/0；NULL=未判',
    source VARCHAR(16) NOT NULL DEFAULT 'assignment' COMMENT 'assignment/practice/wrong_redo',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_records_student_q (student_id, question_id),
    INDEX idx_records_question (question_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
