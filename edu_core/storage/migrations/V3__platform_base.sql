-- ============================================================
-- V3__platform_base：智慧教研平台基座（平台计划 M0，2026-09-06）
-- users/questions 扩展 + 班级/知识点树/试卷/作业/作答 7 张新表
-- ============================================================

-- 用户扩展：学生角色与归属
ALTER TABLE users
    ADD COLUMN real_name VARCHAR(64) NULL COMMENT '真实姓名（学生必填）',
    ADD COLUMN grade_band VARCHAR(8) NULL COMMENT '初中/高中',
    ADD COLUMN grade VARCHAR(16) NULL COMMENT '年级，如 初二/高一',
    ADD COLUMN class_id BIGINT NULL COMMENT '所属班级',
    ADD COLUMN must_change_password TINYINT NOT NULL DEFAULT 0 COMMENT '首登改密标记',
    ADD COLUMN student_no VARCHAR(32) NULL COMMENT '学号（班内唯一）';

-- 题库扩展：从"分类样本"升级为"完整题目"
ALTER TABLE questions
    ADD COLUMN options_json JSON NULL COMMENT '结构化选项 [{key:"A",text:"..."}]',
    ADD COLUMN answer VARCHAR(512) NULL COMMENT '参考答案（客观题为选项键）',
    ADD COLUMN analysis TEXT NULL COMMENT '解析',
    ADD COLUMN difficulty TINYINT NULL COMMENT '难度 1~5',
    ADD COLUMN grade_band VARCHAR(8) NULL COMMENT '初中/高中',
    ADD COLUMN grade VARCHAR(16) NULL COMMENT '年级',
    ADD COLUMN knowledge_node_id BIGINT NULL COMMENT '挂接知识点树节点（迁移期与 knowledge_point 文本并存）',
    ADD COLUMN created_by BIGINT NULL COMMENT '录入教师';

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
