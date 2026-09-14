CREATE TABLE IF NOT EXISTS recommendation_runs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY, student_id BIGINT NOT NULL, strategy VARCHAR(32) NOT NULL,
    input_snapshot_json JSON NOT NULL, candidates_json JSON NOT NULL, selected_json JSON NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, INDEX idx_recommendation_runs_student (student_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
