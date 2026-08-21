-- =====================================================================
-- 008_split_schemas.sql
-- Gom 29 bảng trong `public` thành 9 schema theo nhiệm vụ.
--
--   26 bảng  → 9 schema nghiệp vụ
--    3 bảng  → ở lại `public` với tiền tố zz_deprecated_ (chờ xoá)
--
-- CHỈ CHẠY ĐƯỢC TRÊN POSTGRESQL. SQLite không có khái niệm schema.
--
-- An toàn:
--   · Toàn bộ file nằm trong MỘT transaction — lỗi ở bất kỳ đâu là
--     rollback sạch, không để lại trạng thái nửa vời.
--   · Chạy lại nhiều lần được: bảng đã chuyển thì bỏ qua, không báo lỗi.
--   · Không DROP bảng nào. Không sửa dữ liệu. Chỉ đổi chỗ.
--   · Khoá ngoại đi theo bảng — PostgreSQL lưu ràng buộc theo OID chứ
--     không theo tên schema.
--
-- Quay lại: scripts/migrations/008_rollback_split_schemas.sql
-- Kiểm chứng: scripts/verify_schema_split.sql
--
-- Cách chạy:
--   psql "postgresql://postgres:localpassword@localhost:5432/ridepulse" \
--        -v ON_ERROR_STOP=1 -f scripts/migrations/008_split_schemas.sql
-- =====================================================================

\set ON_ERROR_STOP on

BEGIN;

-- ---------------------------------------------------------------------
-- PHẦN 0 · Chặn chạy nhầm môi trường
-- ---------------------------------------------------------------------

DO $$
BEGIN
    IF current_setting('server_version_num')::int < 100000 THEN
        RAISE EXCEPTION 'Cần PostgreSQL 10 trở lên. Phiên bản hiện tại: %',
                        current_setting('server_version');
    END IF;

    RAISE NOTICE '=== 008_split_schemas · DB=% · role=% ===',
                 current_database(), current_user;
END $$;


-- ---------------------------------------------------------------------
-- PHẦN 1 · Đánh dấu 3 bảng chết trước khi tách
--
-- Ba bảng này do migration 001 tạo, không một file Python nào tham
-- chiếu, và không có ORM class nên create_all() sẽ không dựng lại.
-- Đổi tên thay vì DROP: nếu có chỗ nào tôi bỏ sót (SQL trong notebook,
-- script ad-hoc, dashboard ngoài) thì đổi tên lại là xong, không mất
-- một dòng dữ liệu nào.
--
-- Chúng Ở LẠI `public` — cố ý không cấp schema, để nằm cuối danh sách
-- trong DBeaver như một biển báo "sắp xoá".
-- ---------------------------------------------------------------------

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['dq_rules', 'audit_logs', 'rate_limit_events']
    LOOP
        IF to_regclass('public.' || quote_ident(t)) IS NOT NULL THEN
            EXECUTE format('ALTER TABLE public.%I RENAME TO %I', t, 'zz_deprecated_' || t);
            RAISE NOTICE '[chết]     public.% -> public.zz_deprecated_%', t, t;
        ELSIF to_regclass('public.' || quote_ident('zz_deprecated_' || t)) IS NOT NULL THEN
            RAISE NOTICE '[chết]     % đã được đánh dấu từ trước — bỏ qua', t;
        ELSE
            RAISE NOTICE '[chết]     % không tồn tại — bỏ qua', t;
        END IF;
    END LOOP;
END $$;


-- ---------------------------------------------------------------------
-- PHẦN 2 · Tạo 8 schema
--
-- `analytics` đã tồn tại từ migration 002 (dbt sở hữu) — không đụng tới.
-- ---------------------------------------------------------------------

CREATE SCHEMA IF NOT EXISTS iam;
CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA IF NOT EXISTS catalog;
CREATE SCHEMA IF NOT EXISTS profiling;
CREATE SCHEMA IF NOT EXISTS rules;
CREATE SCHEMA IF NOT EXISTS execution;
CREATE SCHEMA IF NOT EXISTS anomaly;
CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS legacy_agent;

COMMENT ON SCHEMA iam          IS 'Ai là ai, đang đăng nhập bằng gì, được xem dataset nào.';
COMMENT ON SCHEMA audit        IS 'Nhật ký bất biến. Tách riêng để cấp quyền chỉ-ghi-thêm.';
COMMENT ON SCHEMA catalog      IS 'Dữ liệu nguồn và danh mục dataset — mọi schema khác tham chiếu tới.';
COMMENT ON SCHEMA profiling    IS 'Hồ sơ thống kê của dataset — đầu vào cho bước AI đề xuất rule.';
COMMENT ON SCHEMA rules        IS 'Vòng đời một luật: đề xuất -> duyệt -> đóng phiên bản -> gắn lịch.';
COMMENT ON SCHEMA execution    IS 'Mỗi lần chạy kiểm thử và kết quả từng luật.';
COMMENT ON SCHEMA anomaly      IS 'Phát hiện bất thường, giả thuyết do AI sinh, phản hồi Steward.';
COMMENT ON SCHEMA ops          IS 'Hàng đợi tác vụ nền. Mọi pipeline đều ghi vào.';
COMMENT ON SCHEMA legacy_agent IS 'Đường CLI/Agent chạy song song với đường sản phẩm. Không có khoá ngoại nào.';


-- ---------------------------------------------------------------------
-- PHẦN 3 · Chuyển bảng và view
--
-- Dùng ALTER VIEW cho view và ALTER TABLE cho bảng — phân biệt bằng
-- pg_class.relkind thay vì đoán.
--
-- Sequence do cột của bảng sở hữu (serial / identity) tự đi theo bảng.
-- Index và constraint cũng vậy. PHẦN 6 kiểm chứng lại điều này.
-- ---------------------------------------------------------------------

DO $$
DECLARE
    m         RECORD;
    kind      "char";
    moved     int := 0;
    skipped   int := 0;
BEGIN
    FOR m IN
        SELECT * FROM (VALUES
            -- iam
            ('iam',          'user_accounts'),
            ('iam',          'sessions'),
            ('iam',          'dataset_access'),
            -- audit
            ('audit',        'audit_events'),
            -- catalog
            ('catalog',      'datasets'),
            ('catalog',      'source_rows'),
            ('catalog',      'trips_raw'),
            ('catalog',      'trips_canonical'),      -- VIEW
            -- profiling
            ('profiling',    'profiles'),
            ('profiling',    'column_profiles'),
            ('profiling',    'dataset_profiles'),     -- chỉ có trong migration 003, không có ORM
            -- rules
            ('rules',        'rule_proposals'),
            ('rules',        'rule_versions'),
            ('rules',        'rule_configurations'),
            ('rules',        'ruleset_versions'),
            -- execution
            ('execution',    'dq_runs'),
            ('execution',    'dq_results'),
            -- anomaly
            ('anomaly',      'anomaly_runs'),
            ('anomaly',      'anomaly_signals'),
            ('anomaly',      'anomaly_hypotheses'),
            ('anomaly',      'anomaly_feedback'),
            -- ops
            ('ops',          'jobs'),
            -- legacy_agent
            ('legacy_agent', 'proposed_rules'),
            ('legacy_agent', 'active_rules'),
            ('legacy_agent', 'proposal_runs'),
            ('legacy_agent', 'test_runs'),
            ('legacy_agent', 'test_results')
        ) AS t(target_schema, rel)
    LOOP
        SELECT c.relkind INTO kind
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = m.rel;

        IF kind IS NULL THEN
            -- Đã chuyển ở lần chạy trước, hoặc migration tạo ra nó chưa chạy.
            skipped := skipped + 1;
            RAISE NOTICE '[bỏ qua]   % không có trong public', m.rel;

        ELSIF kind = 'v' THEN
            EXECUTE format('ALTER VIEW public.%I SET SCHEMA %I', m.rel, m.target_schema);
            moved := moved + 1;
            RAISE NOTICE '[view]     public.% -> %.%', m.rel, m.target_schema, m.rel;

        ELSIF kind = 'm' THEN
            EXECUTE format('ALTER MATERIALIZED VIEW public.%I SET SCHEMA %I', m.rel, m.target_schema);
            moved := moved + 1;
            RAISE NOTICE '[matview]  public.% -> %.%', m.rel, m.target_schema, m.rel;

        ELSE
            EXECUTE format('ALTER TABLE public.%I SET SCHEMA %I', m.rel, m.target_schema);
            moved := moved + 1;
            RAISE NOTICE '[bảng]     public.% -> %.%', m.rel, m.target_schema, m.rel;
        END IF;
    END LOOP;

    RAISE NOTICE '--- Đã chuyển % đối tượng, bỏ qua % ---', moved, skipped;
END $$;


-- ---------------------------------------------------------------------
-- PHẦN 4 · Cấp lại quyền
--
-- Bước dễ quên nhất. GRANT gắn với schema, không đi theo bảng, nên
-- không cấp lại là mọi role mất quyền truy cập ngay lập tức.
--
-- Giữ NGUYÊN mô hình quyền của migration 002 — không tự ý nới rộng:
--   ridepulse_migration : ALL
--   ridepulse_app       : SELECT, INSERT, UPDATE      (KHÔNG có DELETE)
--   ridepulse_dbt       : SELECT
--   ridepulse_runner    : SELECT
--
-- CẢNH BÁO chưa xử lý ở đây: job_runner.py:76,77,301,322,323 có gọi
-- DELETE trên profiles / column_profiles / source_rows. Với quyền hiện
-- tại, ridepulse_app sẽ bị từ chối. Hôm nay chưa lộ ra vì app kết nối
-- bằng superuser `postgres`. Nếu bạn muốn chuyển app sang đúng role của
-- nó, mở khối GRANT DELETE ở cuối phần này.
-- ---------------------------------------------------------------------

DO $$
DECLARE
    s        text;
    r        text;
    schemas  text[] := ARRAY['iam','audit','catalog','profiling','rules',
                             'execution','anomaly','ops','legacy_agent'];
BEGIN
    FOREACH s IN ARRAY schemas
    LOOP
        -- USAGE: không có thì role không "nhìn" thấy schema, dù có quyền trên bảng.
        FOREACH r IN ARRAY ARRAY['ridepulse_migration','ridepulse_app',
                                 'ridepulse_dbt','ridepulse_runner']
        LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
                EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', s, r);
            END IF;
        END LOOP;

        -- ridepulse_migration — toàn quyền, gồm cả tạo bảng mới
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ridepulse_migration') THEN
            EXECUTE format('GRANT CREATE ON SCHEMA %I TO ridepulse_migration', s);
            EXECUTE format('GRANT ALL ON ALL TABLES IN SCHEMA %I TO ridepulse_migration', s);
            EXECUTE format('GRANT ALL ON ALL SEQUENCES IN SCHEMA %I TO ridepulse_migration', s);
            EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT ALL ON TABLES TO ridepulse_migration', s);
            EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT ALL ON SEQUENCES TO ridepulse_migration', s);
        END IF;

        -- ridepulse_app — đọc/ghi/sửa, giữ đúng mức của migration 002
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ridepulse_app') THEN
            EXECUTE format('GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA %I TO ridepulse_app', s);
            EXECUTE format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA %I TO ridepulse_app', s);
            EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT SELECT, INSERT, UPDATE ON TABLES TO ridepulse_app', s);
            EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT USAGE, SELECT ON SEQUENCES TO ridepulse_app', s);
        END IF;

        -- ridepulse_dbt và ridepulse_runner — chỉ đọc
        FOREACH r IN ARRAY ARRAY['ridepulse_dbt','ridepulse_runner']
        LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
                EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO %I', s, r);
                EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT SELECT ON TABLES TO %I', s, r);
            END IF;
        END LOOP;

        RAISE NOTICE '[quyền]    đã cấp trên schema %', s;
    END LOOP;
END $$;

-- Nhật ký audit phải là bất biến: cho ghi thêm, cấm sửa và cấm xoá.
-- Đây là lý do chính để tách `audit` thành schema riêng.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ridepulse_app') THEN
        REVOKE UPDATE, DELETE ON ALL TABLES IN SCHEMA audit FROM ridepulse_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA audit REVOKE UPDATE, DELETE ON TABLES FROM ridepulse_app;
        RAISE NOTICE '[quyền]    audit: đã thu hồi UPDATE/DELETE của ridepulse_app';
    END IF;
END $$;

-- MỞ KHỐI NÀY nếu bạn chuyển app sang chạy bằng ridepulse_app thay vì postgres.
-- Xem cảnh báo ở đầu PHẦN 4.
--
-- DO $$
-- DECLARE s text;
-- BEGIN
--     FOREACH s IN ARRAY ARRAY['catalog','profiling'] LOOP
--         EXECUTE format('GRANT DELETE ON ALL TABLES IN SCHEMA %I TO ridepulse_app', s);
--         EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT DELETE ON TABLES TO ridepulse_app', s);
--     END LOOP;
-- END $$;


-- ---------------------------------------------------------------------
-- PHẦN 5 · search_path — để code không phải sửa
--
-- Cả ORM lẫn SQL thô trong repo đều dùng tên bảng trần. Khi search_path
-- liệt kê đủ các schema, PostgreSQL tự tìm ra bảng.
--
-- `public` ĐỨNG ĐẦU có chủ đích: Base.metadata.create_all() tạo bảng
-- chưa tồn tại vào schema đầu tiên của search_path. Để public đứng đầu
-- thì bảng mới rơi vào public — dễ thấy, dễ phân loại lại — thay vì lẫn
-- vào một schema nghiệp vụ. Xem ghi chú cuối file.
--
-- QUAN TRỌNG: app hiện kết nối bằng `postgres` (docker-compose.yml:65),
-- KHÔNG phải ridepulse_app. Thiếu dòng ALTER ROLE postgres là app chết
-- ngay sau migration.
--
-- ALTER ROLE chỉ có hiệu lực với KẾT NỐI MỚI. Phải restart app.
-- ---------------------------------------------------------------------

DO $$
DECLARE
    full_path text := 'public, iam, audit, catalog, profiling, rules, '
                   || 'execution, anomaly, ops, legacy_agent, analytics';
BEGIN
    -- Role mà app thật sự đang dùng
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'postgres') THEN
        EXECUTE format('ALTER ROLE postgres SET search_path = %s', full_path);
        RAISE NOTICE '[path]     postgres = %', full_path;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ridepulse_migration') THEN
        EXECUTE format('ALTER ROLE ridepulse_migration SET search_path = %s', full_path);
        RAISE NOTICE '[path]     ridepulse_migration = %', full_path;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ridepulse_app') THEN
        EXECUTE format('ALTER ROLE ridepulse_app SET search_path = %s', full_path);
        RAISE NOTICE '[path]     ridepulse_app = %', full_path;
    END IF;

    -- Runner chỉ đọc dữ liệu nguồn và ghi kết quả chạy
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ridepulse_runner') THEN
        ALTER ROLE ridepulse_runner SET search_path = public, catalog, execution, analytics;
        RAISE NOTICE '[path]     ridepulse_runner = public, catalog, execution, analytics';
    END IF;

    -- dbt đọc catalog, ghi analytics
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ridepulse_dbt') THEN
        ALTER ROLE ridepulse_dbt SET search_path = public, catalog, analytics;
        RAISE NOTICE '[path]     ridepulse_dbt = public, catalog, analytics';
    END IF;
END $$;


-- ---------------------------------------------------------------------
-- PHẦN 6 · Tự kiểm chứng trước khi COMMIT
--
-- Nếu bất kỳ kiểm tra nào thất bại, RAISE EXCEPTION làm rollback toàn bộ
-- transaction — không để lại trạng thái nửa vời.
-- ---------------------------------------------------------------------

DO $$
DECLARE
    leftover   text;
    n_leftover int;
    n_fk       int;
    n_invalid  int;
    orphan_seq int;
BEGIN
    -- 6.1 · Không còn bảng nghiệp vụ nào sót lại trong public
    SELECT count(*), string_agg(c.relname, ', ' ORDER BY c.relname)
      INTO n_leftover, leftover
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind IN ('r','p','v','m')
      AND c.relname NOT LIKE 'zz_deprecated_%';

    IF n_leftover > 0 THEN
        RAISE WARNING 'Còn % đối tượng trong public chưa được phân loại: %',
                      n_leftover, leftover;
        RAISE WARNING 'Không phải lỗi — nhưng hãy kiểm tra xem chúng là gì.';
    ELSE
        RAISE NOTICE '[kiểm tra] public sạch (chỉ còn bảng zz_deprecated_)';
    END IF;

    -- 6.2 · Khoá ngoại còn nguyên
    SELECT count(*) INTO n_fk
    FROM pg_constraint con
    JOIN pg_class c ON c.oid = con.conrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE con.contype = 'f'
      AND n.nspname IN ('iam','audit','catalog','profiling','rules',
                        'execution','anomaly','ops','legacy_agent','public');

    RAISE NOTICE '[kiểm tra] % khoá ngoại đang hoạt động', n_fk;

    SELECT count(*) INTO n_invalid
    FROM pg_constraint
    WHERE contype = 'f' AND NOT convalidated;

    IF n_invalid > 0 THEN
        RAISE EXCEPTION 'Có % khoá ngoại ở trạng thái NOT VALID — dừng và rollback.', n_invalid;
    END IF;

    -- 6.3 · Sequence không bị bỏ lại trong public
    SELECT count(*) INTO orphan_seq
    FROM pg_class s
    JOIN pg_namespace sn ON sn.oid = s.relnamespace
    JOIN pg_depend d     ON d.objid = s.oid AND d.deptype = 'a'
    JOIN pg_class t      ON t.oid = d.refobjid
    JOIN pg_namespace tn ON tn.oid = t.relnamespace
    WHERE s.relkind = 'S'
      AND sn.nspname = 'public'
      AND tn.nspname <> 'public';

    IF orphan_seq > 0 THEN
        RAISE EXCEPTION 'Có % sequence bị bỏ lại trong public trong khi bảng sở hữu đã chuyển đi. Rollback.', orphan_seq;
    END IF;

    RAISE NOTICE '[kiểm tra] sequence đi theo bảng đầy đủ';
    RAISE NOTICE '=== Sẵn sàng COMMIT ===';
END $$;

COMMIT;


-- =====================================================================
-- SAU KHI CHẠY — ba việc bắt buộc, theo đúng thứ tự
--
-- 1. RESTART app.
--    ALTER ROLE ... SET search_path chỉ áp dụng cho kết nối MỚI.
--    Kết nối đang mở vẫn dùng search_path cũ và sẽ báo "relation does
--    not exist".
--
--       docker compose restart api worker
--
-- 2. SỬA dbt — bắt buộc, và không thể hoãn.
--    dbt phân giải source TƯỜNG MINH, KHÔNG dùng search_path.
--
--       dbt_project/models/schema.yml:5
--         schema: public      ->      schema: catalog
--
--    Không sửa là `dbt run` gãy ngay ở model stg_trips.
--
-- 3. SỬA 3 dòng hardcode trong Python.
--    Bỏ hẳn tiền tố `public.` thay vì đổi thành tên schema mới — tên
--    trần chạy đúng ở CẢ HAI trạng thái (trước và sau migration), nên
--    không có cửa sổ hỏng và không phụ thuộc thứ tự triển khai.
--
--       src/services/supabase_dataset.py:108
--         FROM public.trips_canonical          ->  FROM trips_canonical
--       src/services/supabase_dataset.py:406
--         INSERT INTO public.dataset_profiles  ->  INSERT INTO dataset_profiles
--       src/services/supabase_dataset.py:425
--         UPDATE public.datasets               ->  UPDATE datasets
--
--    (Ba dòng này vẫn chạy được ngay cả khi chưa sửa, vì `public` vẫn
--     nằm trong search_path — nhưng lúc đó chúng trỏ vào một bảng không
--     còn tồn tại. Sửa trước khi restart là an toàn nhất.)
--
--
-- QUY ƯỚC CHO MIGRATION VỀ SAU
--
--    Base.metadata.create_all() tạo bảng mới vào schema ĐẦU TIÊN của
--    search_path — tức `public`. Đó là lựa chọn có chủ đích: bảng mới
--    rơi vào public thì nhìn thấy ngay và phân loại lại được, thay vì
--    lặng lẽ lẫn vào một schema nghiệp vụ.
--
--    Nhưng nếu không ai dọn, sau vài tháng public lại lộn xộn như cũ.
--    Quy ước đề xuất: mỗi migration thêm bảng mới phải kết thúc bằng
--    một dòng `ALTER TABLE public.<tên> SET SCHEMA <schema>;`.
--
--
-- BA BẢNG CHỜ XOÁ
--
--    zz_deprecated_dq_rules
--    zz_deprecated_audit_logs
--    zz_deprecated_rate_limit_events
--
--    Chạy hệ thống 1–2 tuần. Không có gì gãy thì:
--       DROP TABLE public.zz_deprecated_dq_rules;
--       DROP TABLE public.zz_deprecated_audit_logs;
--       DROP TABLE public.zz_deprecated_rate_limit_events;
--
--
-- HAI QUYẾT ĐỊNH CÒN TREO — file này tạm xử lý theo bản đồ schema đã duyệt
--
--    · trips_raw   → đang đưa vào `catalog` theo đúng sơ đồ.
--      Nhưng nó mồ côi: chỉ src/worker.py ghi, không ai đọc. dbt đọc
--      trips_canonical (view trên source_rows), không đọc trips_raw.
--      Nếu bạn chốt xoá worker.py thì chuyển trips_raw xuống nhóm
--      zz_deprecated_ ở PHẦN 1 và bỏ dòng ('catalog','trips_raw') ở
--      PHẦN 3.
--
--    · legacy_agent → đang tạo schema với 5 bảng, đúng sơ đồ.
--      Cả 5 đều 0 dòng ở mọi DB local. Nếu chốt bỏ hẳn đường CLI/Agent
--      thì phải xoá class ProposalRunModel (src/services/rule_store.py:205)
--      TRƯỚC, nếu không create_all() dựng lại proposal_runs.
-- =====================================================================
