CREATE TABLE IF NOT EXISTS student_knowledge_mastery (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, student_id BIGINT NOT NULL, subject VARCHAR(64) NOT NULL,
    knowledge_point VARCHAR(128) NOT NULL, attempt_count INT NOT NULL DEFAULT 0,
    correct_rate DECIMAL(6,4) NOT NULL DEFAULT 0, recent_correct_rate DECIMAL(6,4) NOT NULL DEFAULT 0,
    mastery_score DECIMAL(6,4) NOT NULL DEFAULT 0, profile_confidence DECIMAL(6,4) NOT NULL DEFAULT 0,
    consecutive_wrong INT NOT NULL DEFAULT 0, redo_success_rate DECIMAL(6,4) NULL,
    last_practiced_at DATETIME NULL, recommended_difficulty TINYINT NOT NULL DEFAULT 1,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_mastery_student_kp (student_id, subject, knowledge_point),
    INDEX idx_mastery_student_score (student_id, mastery_score)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS student_profile_snapshots (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, student_id BIGINT NOT NULL, snapshot_json JSON NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, INDEX idx_profile_snapshots_student (student_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
