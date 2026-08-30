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
