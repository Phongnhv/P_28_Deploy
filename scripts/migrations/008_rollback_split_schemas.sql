-- =====================================================================
-- 008_rollback_split_schemas.sql
-- Đưa mọi thứ về đúng trạng thái trước khi chạy 008_split_schemas.sql.
--
-- Không mất một dòng dữ liệu nào: chỉ đổi chỗ bảng ngược lại, khôi phục
-- search_path, và bỏ tiền tố zz_deprecated_.
--
-- Cách chạy:
--   psql "postgresql://postgres:localpassword@localhost:5432/ridepulse" \
--        -v ON_ERROR_STOP=1 -f scripts/migrations/008_rollback_split_schemas.sql
--
-- SAU KHI CHẠY:
--   1. Trả dbt_project/models/schema.yml:5 về `schema: public`
--   2. Restart app  (search_path chỉ áp dụng cho kết nối mới)
--
--   Ba dòng trong supabase_dataset.py KHÔNG cần trả lại nếu bạn đã bỏ
--   tiền tố `public.` — tên trần vẫn đúng ở trạng thái gộp.
-- =====================================================================

\set ON_ERROR_STOP on

BEGIN;

DO $$
BEGIN
    RAISE NOTICE '=== ROLLBACK 008 · DB=% · role=% ===',
                 current_database(), current_user;
END $$;


-- ---------------------------------------------------------------------
-- PHẦN 1 · Đưa mọi bảng và view về lại `public`
--
-- Quét theo schema thay vì liệt kê tên bảng: bắt được cả những bảng
-- được thêm vào sau khi migration 008 chạy.
-- ---------------------------------------------------------------------

DO $$
DECLARE
    r        RECORD;
    moved    int := 0;
    clash    int := 0;
BEGIN
    FOR r IN
        SELECT n.nspname AS sch, c.relname AS rel, c.relkind AS kind
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname IN ('iam','audit','catalog','profiling','rules',
                            'execution','anomaly','ops','legacy_agent')
          AND c.relkind IN ('r','p','v','m')
        ORDER BY
            -- View phải chuyển sau bảng nó phụ thuộc, cho gọn nhật ký.
            CASE c.relkind WHEN 'v' THEN 2 WHEN 'm' THEN 2 ELSE 1 END,
            n.nspname, c.relname
    LOOP
        -- Chặn trường hợp public đã có bảng trùng tên (ví dụ create_all()
        -- đã dựng lại một bảng trong lúc hệ thống chạy ở trạng thái tách).
        IF to_regclass('public.' || quote_ident(r.rel)) IS NOT NULL THEN
            clash := clash + 1;
            RAISE WARNING '[XUNG ĐỘT] public.% đã tồn tại — KHÔNG chuyển %.% về. Xử lý tay.',
                          r.rel, r.sch, r.rel;
            CONTINUE;
        END IF;

        IF r.kind = 'v' THEN
            EXECUTE format('ALTER VIEW %I.%I SET SCHEMA public', r.sch, r.rel);
        ELSIF r.kind = 'm' THEN
            EXECUTE format('ALTER MATERIALIZED VIEW %I.%I SET SCHEMA public', r.sch, r.rel);
        ELSE
            EXECUTE format('ALTER TABLE %I.%I SET SCHEMA public', r.sch, r.rel);
        END IF;

        moved := moved + 1;
        RAISE NOTICE '[về]       %.% -> public.%', r.sch, r.rel, r.rel;
    END LOOP;

    RAISE NOTICE '--- Đã trả về % đối tượng, % xung đột ---', moved, clash;

    IF clash > 0 THEN
        RAISE EXCEPTION 'Có % xung đột tên. Rollback transaction để bạn xử lý thủ công.', clash;
    END IF;
END $$;


-- ---------------------------------------------------------------------
-- PHẦN 2 · Bỏ tiền tố zz_deprecated_
-- ---------------------------------------------------------------------

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['dq_rules', 'audit_logs', 'rate_limit_events']
    LOOP
        IF to_regclass('public.' || quote_ident('zz_deprecated_' || t)) IS NOT NULL
           AND to_regclass('public.' || quote_ident(t)) IS NULL THEN
            EXECUTE format('ALTER TABLE public.%I RENAME TO %I', 'zz_deprecated_' || t, t);
            RAISE NOTICE '[tên]      zz_deprecated_% -> %', t, t;
        END IF;
    END LOOP;
END $$;


-- ---------------------------------------------------------------------
-- PHẦN 3 · Khôi phục search_path về mặc định
-- ---------------------------------------------------------------------

DO $$
DECLARE
    r text;
BEGIN
    FOREACH r IN ARRAY ARRAY['postgres','ridepulse_migration','ridepulse_app',
                             'ridepulse_runner','ridepulse_dbt']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            EXECUTE format('ALTER ROLE %I RESET search_path', r);
            RAISE NOTICE '[path]     % đã reset về mặc định', r;
        END IF;
    END LOOP;
END $$;


-- ---------------------------------------------------------------------
-- PHẦN 4 · Xoá 9 schema rỗng
--
-- Dùng DROP ... RESTRICT (mặc định): nếu còn sót đối tượng nào bên trong,
-- lệnh này thất bại và cả transaction rollback. Cố ý — thà dừng lại còn
-- hơn xoá nhầm thứ ai đó vừa tạo.
--
-- `analytics` KHÔNG bị xoá: nó có từ migration 002, dbt sở hữu.
-- ---------------------------------------------------------------------

DROP SCHEMA IF EXISTS iam          RESTRICT;
DROP SCHEMA IF EXISTS audit        RESTRICT;
DROP SCHEMA IF EXISTS catalog      RESTRICT;
DROP SCHEMA IF EXISTS profiling    RESTRICT;
DROP SCHEMA IF EXISTS rules        RESTRICT;
DROP SCHEMA IF EXISTS execution    RESTRICT;
DROP SCHEMA IF EXISTS anomaly      RESTRICT;
DROP SCHEMA IF EXISTS ops          RESTRICT;
DROP SCHEMA IF EXISTS legacy_agent RESTRICT;


-- ---------------------------------------------------------------------
-- PHẦN 5 · Kiểm chứng trước khi COMMIT
-- ---------------------------------------------------------------------

DO $$
DECLARE
    n_left    int;
    n_invalid int;
BEGIN
    SELECT count(*) INTO n_left
    FROM pg_namespace
    WHERE nspname IN ('iam','audit','catalog','profiling','rules',
                      'execution','anomaly','ops','legacy_agent');

    IF n_left > 0 THEN
        RAISE EXCEPTION 'Còn % schema chưa xoá được. Rollback.', n_left;
    END IF;

    SELECT count(*) INTO n_invalid
    FROM pg_constraint WHERE contype = 'f' AND NOT convalidated;

    IF n_invalid > 0 THEN
        RAISE EXCEPTION 'Có % khoá ngoại NOT VALID. Rollback.', n_invalid;
    END IF;

    RAISE NOTICE '[kiểm tra] đã về trạng thái một schema `public`';
    RAISE NOTICE '=== Sẵn sàng COMMIT ===';
END $$;

COMMIT;

-- =====================================================================
-- ĐỪNG QUÊN
--   1. dbt_project/models/schema.yml:5   ->  schema: public
--   2. docker compose restart api worker
-- =====================================================================
