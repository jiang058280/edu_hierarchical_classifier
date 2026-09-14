CREATE TABLE IF NOT EXISTS consolidation_plan_runs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    student_id BIGINT NOT NULL,
    plan_type VARCHAR(24) NOT NULL COMMENT 'wrong_focus/weak_focus/daily',
    input_snapshot_json JSON NOT NULL,
    question_ids_json JSON NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_consolidation_plan_student (student_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
