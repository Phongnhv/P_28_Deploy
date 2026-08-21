-- =====================================================================
-- verify_schema_split.sql
-- Kiểm chứng sau khi chạy 008_split_schemas.sql.
--
-- CHỈ ĐỌC — toàn bộ file là SELECT, không sửa gì. Chạy bao nhiêu lần
-- cũng được, kể cả trên production.
--
-- Cách chạy:
--   psql "postgresql://postgres:localpassword@localhost:5432/ridepulse" \
--        -f scripts/verify_schema_split.sql
--
-- Đọc kết quả: mỗi mục có cột `ket_qua` ghi OK hoặc CAN_XEM.
-- Chỉ cần một dòng CAN_XEM là dừng lại xem kỹ trước khi restart app.
-- =====================================================================

\pset border 2
\pset null '—'

\echo ''
\echo '======================================================================'
\echo ' 1 · Số đối tượng theo từng schema'
\echo '======================================================================'

SELECT
    n.nspname                                              AS schema,
    count(*) FILTER (WHERE c.relkind IN ('r','p'))         AS bang,
    count(*) FILTER (WHERE c.relkind IN ('v','m'))         AS view,
    count(*) FILTER (WHERE c.relkind = 'S')                AS sequence,
    pg_size_pretty(sum(pg_total_relation_size(c.oid)))     AS dung_luong
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname IN ('public','analytics','iam','audit','catalog','profiling',
                    'rules','execution','anomaly','ops','legacy_agent')
  AND c.relkind IN ('r','p','v','m','S')
GROUP BY n.nspname
ORDER BY n.nspname;


\echo ''
\echo '======================================================================'
\echo ' 2 · Đối chiếu với bản đồ schema đã duyệt'
\echo '    CAN_XEM = bảng nằm sai chỗ hoặc chưa được chuyển'
\echo '======================================================================'

WITH mong_doi(rel, schema_dung) AS (VALUES
    ('user_accounts','iam'),        ('sessions','iam'),          ('dataset_access','iam'),
    ('audit_events','audit'),
    ('datasets','catalog'),         ('source_rows','catalog'),
    ('trips_raw','catalog'),        ('trips_canonical','catalog'),
    ('profiles','profiling'),       ('column_profiles','profiling'),
    ('dataset_profiles','profiling'),
    ('rule_proposals','rules'),     ('rule_versions','rules'),
    ('rule_configurations','rules'),('ruleset_versions','rules'),
    ('dq_runs','execution'),        ('dq_results','execution'),
    ('anomaly_runs','anomaly'),     ('anomaly_signals','anomaly'),
    ('anomaly_hypotheses','anomaly'),('anomaly_feedback','anomaly'),
    ('jobs','ops'),
    ('proposed_rules','legacy_agent'),  ('active_rules','legacy_agent'),
    ('proposal_runs','legacy_agent'),   ('test_runs','legacy_agent'),
    ('test_results','legacy_agent')
),
thuc_te AS (
    SELECT c.relname AS rel, n.nspname AS schema_that
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind IN ('r','p','v','m')
)
SELECT
    m.rel                       AS doi_tuong,
    m.schema_dung               AS mong_doi,
    coalesce(t.schema_that, '(khong ton tai)') AS thuc_te,
    CASE
        WHEN t.schema_that IS NULL            THEN 'CAN_XEM · chưa tồn tại'
        WHEN t.schema_that = m.schema_dung    THEN 'OK'
        ELSE                                       'CAN_XEM · sai schema'
    END                         AS ket_qua
FROM mong_doi m
LEFT JOIN thuc_te t ON t.rel = m.rel
ORDER BY (t.schema_that IS DISTINCT FROM m.schema_dung) DESC, m.schema_dung, m.rel;


\echo ''
\echo '======================================================================'
\echo ' 3 · Còn gì sót lại trong public?'
\echo '    Chỉ nên thấy các bảng zz_deprecated_'
\echo '======================================================================'

SELECT
    c.relname   AS doi_tuong,
    CASE c.relkind WHEN 'r' THEN 'bảng' WHEN 'p' THEN 'bảng phân mảnh'
                   WHEN 'v' THEN 'view' WHEN 'm' THEN 'matview'
                   WHEN 'S' THEN 'sequence' END AS loai,
    pg_size_pretty(pg_total_relation_size(c.oid)) AS dung_luong,
    CASE
        WHEN c.relname LIKE 'zz_deprecated_%' THEN 'OK · chờ xoá'
        WHEN c.relkind = 'S'                  THEN 'CAN_XEM · sequence mồ côi'
        ELSE                                       'CAN_XEM · chưa phân loại'
    END AS ket_qua
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind IN ('r','p','v','m','S')
ORDER BY ket_qua, c.relname;


\echo ''
\echo '======================================================================'
\echo ' 4 · Khoá ngoại — không mũi nào được đứt'
\echo '======================================================================'

SELECT
    cn.nspname || '.' || cl.relname    AS bang_con,
    con.conname                        AS ten_khoa,
    pn.nspname || '.' || pl.relname    AS bang_cha,
    CASE WHEN cn.nspname = pn.nspname
         THEN 'trong cùng schema' ELSE 'vượt schema' END AS pham_vi,
    CASE WHEN con.convalidated THEN 'OK' ELSE 'CAN_XEM · NOT VALID' END AS ket_qua
FROM pg_constraint con
JOIN pg_class     cl ON cl.oid = con.conrelid
JOIN pg_namespace cn ON cn.oid = cl.relnamespace
JOIN pg_class     pl ON pl.oid = con.confrelid
JOIN pg_namespace pn ON pn.oid = pl.relnamespace
WHERE con.contype = 'f'
ORDER BY con.convalidated, cn.nspname, cl.relname;

\echo '--- Tổng số khoá ngoại ---'
SELECT count(*) AS tong_khoa_ngoai,
       count(*) FILTER (WHERE convalidated)     AS hop_le,
       count(*) FILTER (WHERE NOT convalidated) AS not_valid
FROM pg_constraint WHERE contype = 'f';


\echo ''
\echo '======================================================================'
\echo ' 5 · search_path của từng role'
\echo '    Thiếu role `postgres` là app sẽ chết — app kết nối bằng role này'
\echo '======================================================================'

SELECT
    r.rolname AS role,
    coalesce(
        (SELECT s FROM unnest(r.rolconfig) AS s WHERE s LIKE 'search\_path=%'),
        '(chưa đặt — dùng mặc định)'
    ) AS search_path,
    CASE
        WHEN r.rolname = 'postgres'
             AND NOT EXISTS (SELECT 1 FROM unnest(r.rolconfig) AS s WHERE s LIKE 'search\_path=%')
            THEN 'CAN_XEM · app dùng role này'
        WHEN EXISTS (SELECT 1 FROM unnest(r.rolconfig) AS s WHERE s LIKE 'search\_path=%')
            THEN 'OK'
        ELSE 'CAN_XEM · chưa đặt'
    END AS ket_qua
FROM pg_roles r
WHERE r.rolname IN ('postgres','ridepulse_migration','ridepulse_app',
                    'ridepulse_runner','ridepulse_dbt')
ORDER BY r.rolname;


\echo ''
\echo '======================================================================'
\echo ' 6 · Quyền USAGE trên schema'
\echo '    Không có USAGE thì role không nhìn thấy schema, dù có quyền bảng'
\echo '======================================================================'

SELECT
    s.nspname AS schema,
    r.rolname AS role,
    CASE WHEN has_schema_privilege(r.rolname, s.nspname, 'USAGE')
         THEN 'OK' ELSE 'CAN_XEM · thiếu USAGE' END AS ket_qua
FROM pg_namespace s
CROSS JOIN pg_roles r
WHERE s.nspname IN ('iam','audit','catalog','profiling','rules',
                    'execution','anomaly','ops','legacy_agent','analytics')
  AND r.rolname IN ('ridepulse_migration','ridepulse_app',
                    'ridepulse_runner','ridepulse_dbt')
ORDER BY (has_schema_privilege(r.rolname, s.nspname, 'USAGE')), s.nspname, r.rolname;


\echo ''
\echo '======================================================================'
\echo ' 7 · Nhật ký audit có thật sự bất biến không?'
\echo '    ridepulse_app phải ghi thêm được, nhưng KHÔNG sửa và KHÔNG xoá'
\echo '======================================================================'

SELECT
    'audit.' || c.relname AS bang,
    has_table_privilege('ridepulse_app', c.oid, 'SELECT') AS doc,
    has_table_privilege('ridepulse_app', c.oid, 'INSERT') AS ghi_them,
    has_table_privilege('ridepulse_app', c.oid, 'UPDATE') AS sua,
    has_table_privilege('ridepulse_app', c.oid, 'DELETE') AS xoa,
    CASE
        WHEN has_table_privilege('ridepulse_app', c.oid, 'INSERT')
         AND NOT has_table_privilege('ridepulse_app', c.oid, 'UPDATE')
         AND NOT has_table_privilege('ridepulse_app', c.oid, 'DELETE')
        THEN 'OK · chỉ ghi thêm'
        ELSE 'CAN_XEM · chưa bất biến'
    END AS ket_qua
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'audit' AND c.relkind = 'r'
  AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ridepulse_app');


\echo ''
\echo '======================================================================'
\echo ' 8 · Sequence có đi theo bảng không?'
\echo '======================================================================'

SELECT
    sn.nspname || '.' || s.relname AS sequence,
    tn.nspname || '.' || t.relname AS bang_so_huu,
    CASE WHEN sn.nspname = tn.nspname
         THEN 'OK' ELSE 'CAN_XEM · lạc schema' END AS ket_qua
FROM pg_class s
JOIN pg_namespace sn ON sn.oid = s.relnamespace
JOIN pg_depend d     ON d.objid = s.oid AND d.deptype = 'a'
JOIN pg_class t      ON t.oid = d.refobjid
JOIN pg_namespace tn ON tn.oid = t.relnamespace
WHERE s.relkind = 'S'
ORDER BY (sn.nspname = tn.nspname), sn.nspname, s.relname;


\echo ''
\echo '======================================================================'
\echo ' 9 · Thử truy vấn bằng tên trần — đúng cách code đang viết'
\echo '    Nếu mục này chạy được, code không cần sửa (trừ dbt)'
\echo '======================================================================'

SHOW search_path;

SELECT count(*) AS so_dataset      FROM datasets;
SELECT count(*) AS so_source_row   FROM source_rows;
SELECT count(*) AS so_job          FROM jobs;
SELECT count(*) AS so_audit_event  FROM audit_events;

\echo ''
\echo '=== Hết. Rà lại mọi dòng có CAN_XEM trước khi restart app. ==='
\echo ''
