-- Migration 004: Fix Audit and Job schemas
-- 1. Add missing columns to sessions table
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS role VARCHAR(64) NOT NULL DEFAULT 'USER';
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS csrf_token VARCHAR(256) NOT NULL DEFAULT '';

-- 2. Add missing columns to jobs table
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;
