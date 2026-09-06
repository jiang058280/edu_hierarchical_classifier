-- ============================================================
-- V2__users_auth：认证与权限（改进计划 WP-D）
-- 新增 users（登录账号 + RBAC 角色）与 audit_logs（治理动作审计）。
-- ============================================================

-- 用户表：角色 admin（治理操作）/ teacher（日常分类、题库、组卷）
CREATE TABLE IF NOT EXISTS users (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(64) NOT NULL UNIQUE,
    password_hash VARCHAR(128) NOT NULL COMMENT 'bcrypt 哈希，绝不存明文',
    role VARCHAR(16) NOT NULL DEFAULT 'teacher' COMMENT 'admin/teacher',
    is_active TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 审计日志：治理动作（版本激活/回滚/删题等）必须留痕（谁/何时/做了什么/来源 IP）
CREATE TABLE IF NOT EXISTS audit_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NULL,
    username VARCHAR(64) NULL COMMENT '冗余用户名，防用户删除后审计断链',
    action VARCHAR(64) NOT NULL,
    resource VARCHAR(128) NULL,
    detail_json JSON NULL,
    client_ip VARCHAR(64) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_audit_logs_user (user_id),
    INDEX idx_audit_logs_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
