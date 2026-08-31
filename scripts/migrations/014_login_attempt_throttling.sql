CREATE TABLE IF NOT EXISTS login_attempts (
    id VARCHAR(64) PRIMARY KEY,
    scope VARCHAR(32) NOT NULL,
    key_hash VARCHAR(64) NOT NULL,
    attempted_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_login_attempts_scope_key_time
    ON login_attempts (scope, key_hash, attempted_at);

CREATE INDEX IF NOT EXISTS ix_login_attempts_attempted_at
    ON login_attempts (attempted_at);

-- Đưa bảng vào schema nghiệp vụ đúng chỗ.
-- `008_split_schemas.sql` đánh số trước file này nên không biết `login_attempts`
-- tồn tại; không có bước dưới đây, một bảng dữ liệu xác thực sẽ nằm lại `public`
-- ngay sau khi vừa dọn xong. Bọc trong DO để chạy được cả khi 008 chưa chạy.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'iam')
       AND to_regclass('public.login_attempts') IS NOT NULL THEN
        ALTER TABLE public.login_attempts SET SCHEMA iam;
    END IF;
END
$$;
