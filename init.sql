-- 이 파일은 MySQL root 권한으로 실행해야 합니다.
-- 예) sudo docker exec -it vue_personal_project-backend-db mysql -uroot -p

CREATE DATABASE IF NOT EXISTS kakao_db
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

GRANT ALL PRIVILEGES ON kakao_db.* TO 'hyunchang88'@'%';
FLUSH PRIVILEGES;

USE kakao_db;

CREATE TABLE IF NOT EXISTS kakao_messages (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id     VARCHAR(128),
  utterance   TEXT,
  intent_name VARCHAR(255),
  block_name  VARCHAR(255),
  raw_payload JSON,
  created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_user (user_id),
  INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS kakao_users (
  user_id       VARCHAR(128) PRIMARY KEY,
  display_name  VARCHAR(64),
  state         VARCHAR(32) NOT NULL DEFAULT 'awaiting_name',
  message_count INT NOT NULL DEFAULT 0,
  first_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
