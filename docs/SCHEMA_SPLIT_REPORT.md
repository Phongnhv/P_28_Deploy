# Nhật ký tách schema — 29 bảng vào 9 schema

> **Trạng thái:** đã thi hành trên PostgreSQL thật, đã kiểm chứng.
> **Ngày:** 21·08·2026 · **Nhánh:** `feature/general-agent` · **Thao tác Git:** 0
> **Test suite:** 201 passed / 0 failed / 2 skipped

---

## Mục lục

1. [Tóm tắt](#1--tóm-tắt)
2. [Trước và sau](#2--trước-và-sau)
3. [Ba file đã tạo](#3--ba-file-đã-tạo)
4. [Bảy quyết định thiết kế khác với bản đồ ban đầu](#4--bảy-quyết-định-thiết-kế-khác-với-bản-đồ-ban-đầu)
5. [Nhật ký thi hành](#5--nhật-ký-thi-hành)
6. [Bảy đính chính phát sinh khi chạy thật](#6--bảy-đính-chính-phát-sinh-khi-chạy-thật)
7. [Sửa 4 lỗi test](#7--sửa-4-lỗi-test)
8. [Bằng chứng kiểm chứng](#8--bằng-chứng-kiểm-chứng)
9. [Còn lại và quyết định treo](#9--còn-lại-và-quyết-định-treo)
10. [Cách kiểm chứng lại và cách quay lui](#10--cách-kiểm-chứng-lại-và-cách-quay-lui)

---

## 1 · Tóm tắt

| Hạng mục | Kết quả |
|---|---|
| Bảng đã chuyển | 26 bảng + 1 view → 9 schema |
| Bảng đánh dấu chờ xoá | 3, ở lại `public` với tiền tố `zz_deprecated_` |
| Khoá ngoại | **19/19 còn sống**, 0 khoá `NOT VALID` |
| Dữ liệu | **50 273 dòng trước — 50 273 dòng sau** |
| Sequence | 2, đi theo bảng đúng schema |
| Test suite | 4 failed → **0 failed**, 197 passed → **201 passed** |
| File nguồn đã sửa | 2 file Python + `.env` |
| File mới | 3 file SQL |
| Thao tác Git | 0 |

Ba việc được thực hiện trong đợt này:

1. **Tách schema** — viết migration, chạy thật trên PostgreSQL 15.18, kiểm chứng đầy đủ.
2. **Sửa 4 lỗi test** đang treo từ trước — suite lần đầu xanh hoàn toàn.
3. **Đính chính 7 kết luận sai** của chính tôi, phát hiện khi chạy thật thay vì đọc code.

---

## 2 · Trước và sau

### Trước

```
ridepulse (PostgreSQL 15.18)
└─ Schemas
   ├─ analytics                    (rỗng — dbt chưa chạy)
   └─ public
      ├─ active_rules              ┐
      ├─ audit_events              │
      ├─ column_profiles           │
      ├─ dataset_access            │
      ├─ datasets                  │
      ├─ dq_results                │  19 bảng, tất cả do
      ├─ dq_runs                   │  create_all() tạo.
      ├─ jobs                      │  Migration 001–007
      ├─ profiles                  │  CHƯA TỪNG CHẠY.
      ├─ proposal_runs             │
      ├─ proposed_rules            │
      ├─ rule_configurations       │
      ├─ rule_proposals            │
      ├─ rule_versions             │
      ├─ sessions                  │
      ├─ source_rows               │
      ├─ test_results              │
      ├─ test_runs                 │
      └─ user_accounts             ┘
```

### Sau

```
ridepulse (PostgreSQL 15.18)
└─ Schemas
   ├─ analytics                    (rỗng — dbt chưa chạy)
   ├─ anomaly            4 bảng    96 kB
   │  ├─ anomaly_feedback
   │  ├─ anomaly_hypotheses
   │  ├─ anomaly_runs
   │  └─ anomaly_signals
   ├─ audit              1 bảng    32 kB
   │  └─ audit_events               ← đã thu hồi UPDATE/DELETE
   ├─ catalog            3 bảng + 1 view    34 MB
   │  ├─ datasets
   │  ├─ source_rows                ← 50 000 dòng
   │  ├─ trips_raw
   │  └─ trips_canonical  (view)
   ├─ execution          2 bảng + 1 sequence
   │  ├─ dq_results
   │  └─ dq_runs
   ├─ iam                3 bảng    128 kB
   │  ├─ dataset_access
   │  ├─ sessions
   │  └─ user_accounts
   ├─ legacy_agent       5 bảng    752 kB
   │  ├─ active_rules               ← 31 dòng
   │  ├─ proposal_runs
   │  ├─ proposed_rules             ← 129 dòng
   │  ├─ test_results               ← 35 dòng
   │  └─ test_runs                  ← 4 dòng
   ├─ ops                1 bảng    64 kB
   │  └─ jobs
   ├─ profiling          3 bảng + 1 sequence
   │  ├─ column_profiles
   │  ├─ dataset_profiles           ← bảng bản đồ cũ BỎ SÓT
   │  └─ profiles
   ├─ rules              4 bảng    232 kB
   │  ├─ rule_configurations
   │  ├─ rule_proposals
   │  ├─ rule_versions
   │  └─ ruleset_versions
   └─ public             3 bảng    48 kB
      ├─ zz_deprecated_audit_logs
      ├─ zz_deprecated_dq_rules
      └─ zz_deprecated_rate_limit_events
```

**11 schema** thay vì 2. `public` không rỗng có chủ đích — ba bảng chết nằm đó để theo dõi 1–2 tuần trước khi `DROP`, thay vì xoá ngay và mất đường lùi.

---

## 3 · Ba file đã tạo

| File | Dòng | Vai trò |
|---|---|---|
| `scripts/migrations/008_split_schemas.sql` | 330 | Tách schema |
| `scripts/migrations/008_rollback_split_schemas.sql` | 165 | Quay lui |
| `scripts/verify_schema_split.sql` | 200 | Kiểm chứng, chỉ đọc |

### `008_split_schemas.sql` có 6 phần

```
PHẦN 0 · Chặn chạy nhầm môi trường (kiểm tra phiên bản PostgreSQL)
PHẦN 1 · Đánh dấu 3 bảng chết  → zz_deprecated_
PHẦN 2 · Tạo 9 schema + COMMENT mô tả nhiệm vụ
PHẦN 3 · Chuyển 27 đối tượng, phân biệt bảng/view/matview
PHẦN 4 · Cấp lại quyền cho 4 role + thu hồi UPDATE/DELETE trên audit
PHẦN 5 · Đặt search_path cho 5 role
PHẦN 6 · Tự kiểm chứng — RAISE EXCEPTION làm rollback nếu sai
```

**Toàn bộ nằm trong một transaction.** Lỗi ở bất kỳ đâu là rollback sạch, không để lại trạng thái nửa vời.

**Chạy lại nhiều lần được.** `CREATE SCHEMA IF NOT EXISTS`, và mỗi lần chuyển bảng đều kiểm tra tồn tại trước.

---

## 4 · Bảy quyết định thiết kế khác với bản đồ ban đầu

Bản đồ schema (artifact) là bản thiết kế. Khi viết migration thật, bảy chỗ phải làm khác — mỗi chỗ đều có lý do cụ thể.

### 4.1 · `ALTER ROLE postgres SET search_path` — thiếu là app chết

Bản đồ chỉ đặt `search_path` cho `ridepulse_app` và `ridepulse_runner`. Nhưng:

```yaml
# docker-compose.yml:65
DATABASE_URL=postgresql+psycopg2://postgres:localpassword@db:5432/ridepulse
                                   ^^^^^^^^
```

**App kết nối bằng `postgres`, không phải `ridepulse_app`.** Làm đúng theo bản đồ là app không tìm thấy bảng nào ngay sau migration.

`008` đặt `search_path` cho cả 5 role: `postgres`, `ridepulse_migration`, `ridepulse_app`, `ridepulse_runner`, `ridepulse_dbt`.

### 4.2 · `dataset_profiles` — bảng sống bị bản đồ bỏ sót

Bản đồ liệt kê 24 bảng, lấy từ file SQLite. Nhưng SQLite chỉ chứa bảng có ORM class. PostgreSQL thật có **29 bảng** — thêm 5 bảng chỉ tồn tại trong migration SQL:

| Bảng | Trạng thái |
|---|---|
| `audit_logs`, `dq_rules`, `rate_limit_events` | chết — 0 tham chiếu Python |
| `trips_raw` | mồ côi — chỉ `worker.py` ghi |
| **`dataset_profiles`** | **SỐNG** — `supabase_dataset.py:406` đang INSERT |

`dataset_profiles` được đưa vào `profiling`. Không có bước này, một bảng nghiệp vụ đang hoạt động sẽ nằm lại `public` ngay sau khi vừa dọn xong.

### 4.3 · Phân biệt bảng và view bằng `relkind`

`trips_canonical` là VIEW. `ALTER TABLE ... SET SCHEMA` không phải cú pháp đúng cho view.

```sql
SELECT c.relkind INTO kind FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = m.rel;

IF kind = 'v' THEN
    EXECUTE format('ALTER VIEW public.%I SET SCHEMA %I', m.rel, m.target_schema);
ELSIF kind = 'm' THEN
    EXECUTE format('ALTER MATERIALIZED VIEW public.%I SET SCHEMA %I', ...);
ELSE
    EXECUTE format('ALTER TABLE public.%I SET SCHEMA %I', ...);
END IF;
```

Phát hiện bằng `pg_class.relkind` thay vì đoán theo tên.

### 4.4 · Giữ nguyên mô hình quyền của `migration 002`

Bản đồ đề xuất cấp `DELETE` cho `ridepulse_app`. `migration 002:8` cố ý chỉ cấp `SELECT, INSERT, UPDATE`.

`008` **giữ nguyên** mức quyền cũ, không tự nới rộng. Khối `GRANT DELETE` để sẵn dạng comment kèm giải thích:

> `job_runner.py:76,77,301,322,323` **có** gọi DELETE trên `profiles`, `column_profiles`, `source_rows`. Hôm nay chưa lộ ra vì app kết nối bằng superuser `postgres`. Nếu chuyển app sang chạy bằng `ridepulse_app`, phải mở khối này.

Nới quyền là quyết định bảo mật, không phải chi tiết kỹ thuật — không tự làm thay.

### 4.5 · `audit` thật sự bất biến

Đây mới là lý do có giá trị nhất để tách `audit` thành schema riêng — không chỉ để cây thư mục gọn:

```sql
REVOKE UPDATE, DELETE ON ALL TABLES IN SCHEMA audit FROM ridepulse_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA audit REVOKE UPDATE, DELETE ON TABLES FROM ridepulse_app;
```

Kết quả kiểm chứng: `doc=t · ghi_them=t · sua=f · xoa=f`.

### 4.6 · `public` đứng đầu `search_path` — có chủ đích

`Base.metadata.create_all()` tạo bảng chưa tồn tại vào schema **đầu tiên** của `search_path`.

Để `public` đứng đầu thì bảng mới rơi vào `public` — nhìn thấy ngay và phân loại lại được, thay vì lặng lẽ lẫn vào một schema nghiệp vụ. Đổi lại, cần một quy ước: mỗi migration thêm bảng mới phải kết thúc bằng `ALTER TABLE public.<tên> SET SCHEMA <schema>;`. Quy ước này ghi ở cuối `008`.

### 4.7 · Tự kiểm chứng trước `COMMIT`

`PHẦN 6` kiểm ba thứ và `RAISE EXCEPTION` nếu sai — làm rollback cả transaction:

```sql
-- 6.1 Không còn bảng nghiệp vụ nào sót trong public
-- 6.2 Không có khoá ngoại nào ở trạng thái NOT VALID
-- 6.3 Không sequence nào bị bỏ lại trong public khi bảng sở hữu đã chuyển đi
```

Kiểm tra 6.3 quan trọng nhất và dễ bị bỏ qua nhất: `SET SCHEMA` có mang theo sequence do cột sở hữu, nhưng nếu có sequence được tạo tay thì không.

---

## 5 · Nhật ký thi hành

### Bước 1 — Tìm và khởi động Docker

`docker ps` báo daemon không chạy. Trước đó tôi đã kết luận **sai** rằng Docker Desktop chưa cài — vì tìm ở `C:\Program Files\Docker`. Nó nằm ở thư mục user:

```
C:\Users\<user>\AppData\Local\Programs\DockerDesktop\Docker Desktop.exe
docker CLI: ...\DockerDesktop\resources\bin\docker      (Docker 29.6.2)
```

### Bước 2 — Dựng PostgreSQL

```bash
docker compose up -d db
# → Container ridepulse-db Started
# → PostgreSQL 15.18 on x86_64-pc-linux-musl (Alpine)
# → 0.0.0.0:5432->5432/tcp, healthy
```

### Bước 3 — Khảo sát trạng thái thật

```
public: 19 bảng, 0 view
role ridepulse*: (rỗng — chưa có role nào)
```

19 bảng này là bộ do `create_all()` tạo, **không** phải bộ của migration. Nghĩa là 7 migration nền chưa từng chạy trên DB này.

### Bước 4 — Đếm dữ liệu thật

`pg_stat_user_tables.n_live_tup` trả về rỗng — dễ khiến kết luận nhầm là DB trống. Đếm thật bằng `count(*)`:

```
active_rules      31        profiles             1
audit_events       5        proposed_rules     129
column_profiles   21        rule_proposals      31
dataset_access     2        source_rows      50000
datasets           1        test_results        35
jobs              10        test_runs            4
user_accounts      3
                            >>> TỔNG: 50 273 dòng
```

**DB có dữ liệu thật.** Kế hoạch chuyển sang chế độ cẩn trọng.

### Bước 5 — Rà migration tìm lệnh phá huỷ

```bash
grep -niE "^\s*(DROP|TRUNCATE|DELETE FROM|ALTER TABLE .* DROP)" scripts/migrations/00[1-7]_*.sql
```

Chỉ khớp các tên cột chứa chuỗi `dropoff`. **Không có lệnh phá huỷ nào.**

### Bước 6 — Sao lưu

```bash
docker compose exec -T db pg_dump -U postgres -d ridepulse --clean --if-exists > <scratchpad>/ridepulse_backup_pre008.sql
# → 11 810 002 bytes, 19 khối COPY đầy đủ dữ liệu
```

File dump để ở scratchpad **ngoài repo**, không thêm file nào vào project.

### Bước 7 — Chạy 7 migration nền

Lần đầu thất bại vì Git Bash trên Windows dịch `/scripts/migrations/...` thành `C:/Program Files/Git/scripts/...`. Chuyển sang đưa file qua stdin:

```bash
for f in 001_schema 002_roles 003_gate2_schema 004_fix_audit_schema \
         005_canonical_dataset_contract 006_rule_proposal_core_evidence 007_graph2_3_models; do
  docker compose exec -T db psql -U postgres -d ridepulse -v ON_ERROR_STOP=1 -q < "scripts/migrations/${f}.sql"
done
```

**Cả 7 file chạy sạch, không lỗi.** Kết quả: 29 bảng + 1 view, 4 role được tạo.

### Bước 8 — Chạy `008`

```
[chết]     public.dq_rules -> public.zz_deprecated_dq_rules
[chết]     public.audit_logs -> public.zz_deprecated_audit_logs
[chết]     public.rate_limit_events -> public.zz_deprecated_rate_limit_events
CREATE SCHEMA × 9
[bảng]     public.user_accounts -> iam.user_accounts
...
[view]     public.trips_canonical -> catalog.trips_canonical
...
--- Đã chuyển 27 đối tượng, bỏ qua 0 ---
[quyền]    đã cấp trên schema × 9
[quyền]    audit: đã thu hồi UPDATE/DELETE của ridepulse_app
[path]     postgres = public, iam, audit, catalog, profiling, rules, execution, anomaly, ops, legacy_agent, analytics
[path]     ridepulse_runner = public, catalog, execution, analytics
[path]     ridepulse_dbt = public, catalog, analytics
[kiểm tra] public sạch (chỉ còn bảng zz_deprecated_)
[kiểm tra] 19 khoá ngoại đang hoạt động
[kiểm tra] sequence đi theo bảng đầy đủ
=== Sẵn sàng COMMIT ===
COMMIT
```

### Bước 9 — Kiểm chứng độc lập

`verify_schema_split.sql` — 27/27 đối tượng `OK`, chi tiết ở [mục 8](#8--bằng-chứng-kiểm-chứng).

---

## 6 · Bảy đính chính phát sinh khi chạy thật

Đây là phần quan trọng nhất của báo cáo này. Bảy kết luận trước đó của tôi **sai**, và chỉ lộ ra khi chạy thật thay vì đọc code.

### 6.1 · "4 dòng code phải sửa" → thực tế **16 dòng**

Con số 4 lấy từ bản đồ cũ, mà bản đồ chỉ đếm 3 *bảng* khác nhau, không đếm số lần xuất hiện.

```
src/services/supabase_dataset.py
  public.trips_canonical   → 13 dòng: 108, 156, 159, 184, 251, 256,
                                      281, 293, 313, 328, 341, 350, 360
  public.dataset_profiles  →  1 dòng: 406
  public.datasets          →  1 dòng: 425
                              ────────────
                              15 dòng

dbt_project/models/schema.yml:5   schema: public → catalog
                              ────────────
                              16 dòng tổng cộng
```

Cả ba tham chiếu đã được xác nhận gãy thật, không phải suy đoán:

```
GAY   supabase_dataset.py:108 → relation "public.trips_canonical" does not exist
GAY   supabase_dataset.py:406 → relation "public.dataset_profiles" does not exist
GAY   supabase_dataset.py:425 → relation "public.datasets" does not exist
```

### 6.2 · "DB rỗng" → **50 273 dòng**

Tôi đọc `pg_stat_user_tables.n_live_tup`, thấy rỗng, và suýt kết luận DB trống. Đó là **thống kê ước lượng**, cần `ANALYZE` mới có giá trị. Đếm thật cho ra 50 273 dòng.

Bài học: trước khi chạy DDL, luôn `count(*)`, không tin thống kê.

### 6.3 · "5 bảng legacy_agent 0 dòng ở mọi DB local" → sai với PostgreSQL

Đúng với SQLite. **Sai với Postgres:**

```
proposed_rules  129 dòng
active_rules     31 dòng
test_results     35 dòng
test_runs         4 dòng
```

Đường CLI/Agent đã từng chạy thật ở đây. Điều này ảnh hưởng trực tiếp tới quyết định "có bỏ hẳn `legacy_agent` không" — không còn là bảng rỗng vô hại nữa.

### 6.4 · Điểm đứt gãy của chuỗi `worker.py` nằm ở chỗ khác

Tôi nói *"`routes.py` không gọi `local_worker_api`"*. Thực tế đứt sớm hơn một tầng: `dispatch_cloud_run_job` **có** caller là `jobs.py:32`, nhưng **router đó không bao giờ được mount** — `main.py:153-154` chỉ include `router` và `dq_router`.

### 6.5 · Container `worker` vẫn đang chạy

`docker-compose.yml:87` dựng service `worker` trên cổng 8001. Nó sống và tốn tài nguyên, chỉ là không ai gọi tới — không phải "file chết nằm trên đĩa" như tôi mô tả.

### 6.6 · Docker Desktop **có** cài

Kết luận "không thấy Docker Desktop" là do tìm sai chỗ. Nó nằm ở `%LOCALAPPDATA%\Programs\DockerDesktop`, không phải `C:\Program Files\Docker`.

### 6.7 · Một quả mìn ngủ trong cách nhận diện URL

Đây là thứ nguy hiểm nhất, và chỉ tìm ra vì thử chạy thật.

```python
# src/services/supabase_dataset.py:65
return bool(database_url and database_url.startswith(("postgres://", "postgresql://")))
```

`postgresql+psycopg2://` **không khớp** — nó bắt đầu bằng `postgresql+`.

| `DATABASE_URL` | Nhánh chạy | Hậu quả sau khi tách schema |
|---|---|---|
| `postgresql+psycopg2://...` *(dạng docker-compose)* | local | Chạy bình thường, 15 dòng nằm im |
| `postgresql://...` *(dạng trần)* | **Supabase** | **Profiling gãy ngay** |

Đã chứng minh bằng chạy thật:

```
postgresql+psycopg2://  →  _supabase_source_url() = None  →  OK
postgresql://           →  _supabase_source_url() = CÓ    →  profile_dataset() GÃY
                                                              relation "public.trips_canonical" does not exist
```

Hai chuỗi kết nối cùng trỏ về một database, chỉ khác cách viết, mà đi hai đường code hoàn toàn khác nhau, không cảnh báo gì.

**Đây là lỗi có sẵn từ trước, không do đợt tách schema.** Nhưng đợt tách biến nó từ vô hại thành gây sự cố.

---

## 7 · Sửa 4 lỗi test

Bốn test này treo từ trước đợt tách. Suite giờ xanh hoàn toàn.

```
Trước:  4 failed, 197 passed, 2 skipped
Sau:    0 failed, 201 passed, 2 skipped
```

### 7.1 · `src/models/rule_schemas.py` — hai lỗi cùng một chỗ

Sửa hai test: `test_empty_optional_parameters_do_not_require_provenance` và `test_parameter_provenance_rejects_duplicate_entries`.

**Trước:**

```python
active_parameters = {name for name, value in p.model_dump().items() if value is not None}
provenance_parameters = {item.parameter_name for item in self.parameter_provenance}
if active_parameters != provenance_parameters:
    raise ValueError("parameter_provenance phải chứa đúng một entry cho mỗi parameter đang sử dụng")

return self
```

**Sau:**

```python
# Một tham số chỉ được coi là "đang sử dụng" khi nó thực sự ràng buộc điều gì.
# `None` là chưa khai; collection rỗng (`[]`, `{}`, `""`) là có khai nhưng không
# ràng buộc gì — cả hai đều không cần chứng cứ đi kèm.
#
# KHÔNG dùng phép kiểm falsy ở đây: `min=0`, `max_null_pct=0.0` hay
# `threshold=False` đều là ràng buộc thật và bắt buộc phải có provenance.
active_parameters = {
    name
    for name, value in p.model_dump().items()
    if value is not None and not (isinstance(value, (list, dict, set, tuple, str)) and len(value) == 0)
}

provenance_names = [item.parameter_name for item in self.parameter_provenance]
provenance_parameters = set(provenance_names)

# So sánh hai `set` sẽ nuốt mất entry trùng tên (hai entry "min" gộp thành một),
# nên phải bắt trùng lặp trước khi so khớp.
if len(provenance_names) != len(provenance_parameters):
    duplicates = sorted({n for n in provenance_names if provenance_names.count(n) > 1})
    raise ValueError("parameter_provenance có entry trùng tên cho cùng một parameter: " + ", ".join(duplicates))

if active_parameters != provenance_parameters:
    raise ValueError("parameter_provenance phải chứa đúng một entry cho mỗi parameter đang sử dụng")

return self
```

**Lỗi 1 — danh sách rỗng bị coi là đang dùng.** `if value is not None` khiến `accepted_values = []` bị tính vào `active_parameters` và đòi phải có chứng cứ. Định nghĩa được chốt: *một tham số là "đang sử dụng" khi nó thực sự ràng buộc điều gì đó.*

**Chỗ dễ sai mà bản sửa này tránh:** không dùng phép kiểm falsy. `min=0`, `max_null_pct=0.0`, `threshold=False` đều falsy nhưng **là ràng buộc thật**. Chỉ loại đúng collection rỗng.

**Lỗi 2 — `set` nuốt entry trùng.** Hai entry cùng tên `min` gộp thành một phần tử, `{'min'} == {'min'}` cho kết quả hợp lệ. Thông tin trùng lặp bị mất **trước khi kịp kiểm tra**. Nay đếm trước, và báo rõ tên nào bị trùng.

### 7.2 · `src/agents/nodes/rule_proposer_node.py` — `_stamp_rule`

Sửa test `test_stamp_normalizes_evidence_reference_to_matched_candidate`.

Test chỉ bắt được `KeyError: 'parameter_provenance'`. Nhưng vấn đề thật nặng hơn: khi `selected_evidence_refs` được chuẩn hoá, `parameter_provenance` **vẫn trỏ vào id cũ đã không còn tồn tại**. Rule được lưu với chứng cứ trỏ vào hư không, và mọi truy vết sau này đều gãy — âm thầm.

**Trước:**

```python
    evidence_by_id = {item["id"]: item for item in evidence_items}
    selected_refs = list(rule.selected_evidence_refs)
    allowed_refs = list(evidence_by_id)
    invalid_refs = [ref for ref in selected_refs if ref not in evidence_by_id]
    if invalid_refs:
        logger.warning(
            "Rule %s tham chiếu evidence không thuộc candidate; tự động chuẩn hóa %s",
            rule_id,
            invalid_refs,
        )
        selected_refs = allowed_refs
    if not selected_refs:
        logger.warning("Rule %s không còn evidence hợp lệ sau chuẩn hóa", rule_id)
        return {}
```

**Sau:**

```python
    evidence_by_id = {item["id"]: item for item in evidence_items}
    selected_refs = list(rule.selected_evidence_refs)
    allowed_refs = list(evidence_by_id)
    invalid_refs = [ref for ref in selected_refs if ref not in evidence_by_id]
    if invalid_refs:
        logger.warning(
            "Rule %s tham chiếu evidence không thuộc candidate; tự động chuẩn hóa %s",
            rule_id,
            invalid_refs,
        )
        selected_refs = allowed_refs
    if not selected_refs:
        logger.warning("Rule %s không còn evidence hợp lệ sau chuẩn hóa", rule_id)
        return {}

    # parameter_provenance trỏ vào cùng tập evidence với selected_evidence_refs.
    # Khi ở trên đã chuẩn hóa refs, provenance phải đi theo — nếu không, rule được
    # lưu với chứng cứ trỏ vào một id không tồn tại và mọi truy vết sau này đều gãy.
    provenance = [item.model_dump() for item in rule.parameter_provenance]
    if invalid_refs:
        for entry in provenance:
            if entry["source_ref"] in evidence_by_id:
                continue
            # Ưu tiên ref cùng loại nguồn để không đổi ý nghĩa của chứng cứ.
            original_type = str(entry["source_type"])
            replacement = next(
                (ref for ref in selected_refs if _evidence_source_type(ref) == original_type),
                selected_refs[0],
            )
            logger.warning(
                "Rule %s: provenance của tham số %s trỏ vào %s không hợp lệ — chuyển sang %s",
                rule_id,
                entry["parameter_name"],
                entry["source_ref"],
                replacement,
            )
            entry["source_ref"] = replacement
            entry["source_type"] = _evidence_source_type(replacement)
```

Và thêm hai khoá vào dict trả về:

```python
        "proposal_basis": rule.proposal_basis.value,
        "selected_evidence_refs": selected_refs,
        "parameter_provenance": provenance,      # ← mới
        "assumptions": list(rule.assumptions),   # ← mới
        "confidence": rule.confidence.model_dump(),
```

**Kiểm tra rủi ro trước khi thêm khoá:** `save_proposed_rules` (`rule_store.py:572`) map tường minh bằng `rule.get(...)`, không dùng `Model(**dict)`. Thêm khoá mới hoàn toàn an toàn.

### 7.3 · `.env` — gộp thay vì xoá

Sửa test `test_loopback_cors_accepts_127_origin`. **Đây không phải lỗi code.**

**Trước:**

```
30: CORS_ORIGINS=http://localhost:3000,http://localhost:5173
31: CORS_ORIGINS=http://localhost:8000
```

`dotenv` lấy dòng cuối. Toàn bộ danh sách origin thu về còn đúng `localhost:8000`.

**Sau:**

```
30: CORS_ORIGINS=http://localhost:3000,http://localhost:5173,http://localhost:8000
```

**Gộp** chứ không xoá dòng 31 — ai đó thêm `:8000` có thể có lý do, gộp thì vừa sửa lỗi đè vừa giữ nguyên ý định đó. Backup `.env` để ở scratchpad. Không chạm dòng nào khác trong file.

**Chứng minh đây thuần tuý là cấu hình** — giữ nguyên code, chỉ đổi giá trị biến môi trường:

```
CORS_ORIGINS = localhost:8000                    →  1 failed
CORS_ORIGINS = localhost:3000,localhost:5173     →  1 passed
```

**Hệ quả thật ngoài test:** frontend Vite ở cổng 5173 đang bị CORS chặn. Sửa cái này gỡ chặn thật, không chỉ làm test xanh.

---

## 8 · Bằng chứng kiểm chứng

### 8.1 · Đối chiếu 27 đối tượng

`verify_schema_split.sql` mục 2 — **27/27 `OK`**, không dòng nào `CAN_XEM`.

### 8.2 · Khoá ngoại

**19/19 hợp lệ, 0 `NOT VALID`.** Tám khoá giờ cắt ngang ranh giới schema và vẫn hoạt động:

| Bảng con | Bảng cha | Phạm vi |
|---|---|---|
| `execution.dq_runs` | `ops.jobs` | vượt schema |
| `execution.dq_runs` | `catalog.datasets` | vượt schema |
| `execution.dq_runs` | `rules.ruleset_versions` | vượt schema |
| `anomaly.anomaly_runs` | `execution.dq_runs` | vượt schema |
| `anomaly.anomaly_feedback` | `iam.user_accounts` | vượt schema |
| `iam.dataset_access` | `catalog.datasets` | vượt schema |
| `profiling.profiles` | `catalog.datasets` | vượt schema |
| `rules.rule_proposals` | `catalog.datasets` | vượt schema |

PostgreSQL lưu ràng buộc khoá ngoại theo **OID của bảng**, không theo tên schema — nên `SET SCHEMA` không đụng tới chúng.

### 8.3 · Dữ liệu

```
>>> TỔNG SAU KHI TÁCH: 50 273   (trước: 50 273)
```

Khớp tuyệt đối.

### 8.4 · `create_all()` không dựng lại bảng

Đây là rủi ro lớn nhất: nếu SQLAlchemy không "nhìn thấy" bảng qua `search_path`, `create_all()` sẽ dựng lại 26 bảng rỗng trong `public` và che mất dữ liệu thật.

Dò trước bằng `has_table`:

```
source_rows        visible=True        dq_runs           visible=True
datasets           visible=True        proposed_rules    visible=True
jobs               visible=True        anomaly_runs      visible=True
audit_events       visible=True
```

Rồi chạy thật:

```
public TRƯỚC init_db(): 3  ['zz_deprecated_audit_logs', 'zz_deprecated_dq_rules', 'zz_deprecated_rate_limit_events']
public SAU   init_db(): 3  ['zz_deprecated_audit_logs', 'zz_deprecated_dq_rules', 'zz_deprecated_rate_limit_events']

>>> BẢNG MỚI BỊ DỰNG TRONG public: KHÔNG CÓ — an toàn
```

### 8.5 · Các đường đi thật của app

```
--- ORM đọc qua nhiều schema ---
  OK    datasets (catalog)              -> 1
  OK    source_rows (catalog)           -> 50000
  OK    jobs (ops)                      -> 10
  OK    audit_events (audit)            -> 5
  OK    profiles (profiling)            -> 1

--- JOIN vượt schema ---
  OK    profiles JOIN datasets          -> 1
  OK    relationship profiles.columns   -> 21

--- SQL thô tên trần của job_runner ---
  OK    SELECT ... FROM source_rows     -> 50000
  OK    view trips_canonical            -> 50000
```

**9/9 OK.** Truy vấn tên trần chạy đúng dù bảng đã sang schema khác — xác nhận cách sửa được đề xuất (bỏ tiền tố `public.`, không đổi thành `catalog.`) là đúng.

### 8.6 · Nhật ký audit bất biến

```
audit.audit_events    doc=t   ghi_them=t   sua=f   xoa=f    → OK · chỉ ghi thêm
```

### 8.7 · Sequence

```
execution.dq_results_id_seq        → execution.dq_results        OK
profiling.column_profiles_id_seq   → profiling.column_profiles   OK
```

### 8.8 · Test suite

```
201 passed, 2 skipped, 12 warnings in 137.57s
```

197 → 201, đúng bằng số test được sửa. **Không test nào đang xanh bị hỏng.**

### 8.9 · Một `CAN_XEM` — có sẵn từ trước

```
analytics | ridepulse_app       | CAN_XEM · thiếu USAGE
analytics | ridepulse_migration | CAN_XEM · thiếu USAGE
analytics | ridepulse_runner    | CAN_XEM · thiếu USAGE
```

`migration 002:14` chỉ cấp `analytics` cho `ridepulse_dbt`. `008` cố ý không đụng tới `analytics`.

Đáng lưu ý: `ridepulse_runner` có `analytics` trong `search_path` nhưng **không có quyền USAGE** — nếu runner cần đọc view dbt (`stg_trips`, `profile_input`) sẽ bị từ chối. Sửa bằng hai dòng khi thấy cần:

```sql
GRANT USAGE ON SCHEMA analytics TO ridepulse_runner;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO ridepulse_runner;
```

---

## 9 · Còn lại và quyết định treo

### 9.1 · Chặn việc chuyển app sang PostgreSQL

`.env` hiện vẫn trỏ SQLite nên chưa có gì hỏng ngoài thực tế. Hai việc này phải xong **trước** khi chuyển:

| # | Việc | Chi tiết |
|---|---|---|
| A | 15 dòng hardcode `public.` | `sed` một lệnh, xem bên dưới |
| B | `dbt_project/models/schema.yml:5` | `schema: public` → `schema: catalog` |

```bash
sed -i 's/public\.trips_canonical/trips_canonical/g;
        s/public\.dataset_profiles/dataset_profiles/g;
        s/public\.datasets/datasets/g' src/services/supabase_dataset.py
```

**Vì sao bỏ hẳn tiền tố thay vì đổi thành `catalog.`:** tên trần chạy đúng ở **cả hai** trạng thái — trước và sau migration. Không có cửa sổ hỏng, không phụ thuộc thứ tự triển khai.

**`schema.yml` là ngoại lệ:** dbt phân giải source **tường minh, không dùng `search_path`**. Dòng này phải đổi **đúng lúc** chạy migration, không sớm hơn.

Sau đó **restart app** — `ALTER ROLE ... SET search_path` chỉ áp dụng cho kết nối mới.

### 9.2 · Cần quyết định

| Vấn đề | Bản chất |
|---|---|
| `is_postgres_database_url:65` không nhận `postgresql+psycopg2://` | Sửa là **đổi hành vi** — bật nhánh Supabase cho các URL xưa nay đi nhánh local |
| Số phận `worker.py` / `trips_raw` | `worker.py` là bản sao cũ kém hơn `job_runner.py`; `run_dq` chỉ `time.sleep(1)` |
| Có bỏ hẳn `legacy_agent` không | **Không còn rỗng** — 199 dòng trong Postgres. Nếu xoá, phải xoá `ProposalRunModel` (`rule_store.py:205`) trước, nếu không `create_all()` dựng lại `proposal_runs` |
| 3 bảng `zz_deprecated_` | Chạy 1–2 tuần rồi `DROP` nếu không có gì gãy |
| Nối dây `parameter_provenance` + `assumptions` | Hai cột đã có trong `RuleProposalModel`, `_stamp_rule` giờ đã xuất dữ liệu, `save_proposed_rules` chưa ghi |
| `analytics` thiếu `USAGE` cho 3 role | Có sẵn từ `migration 002` |

### 9.3 · File rollback chưa được kiểm chứng

`008_rollback_split_schemas.sql` đã viết nhưng **chưa từng chạy thử**. Một đường lùi chưa kiểm chứng còn tệ hơn không có — vì lúc sự cố sẽ tin là có lối thoát, chạy nó, rồi phát hiện nó cũng hỏng.

Nên chạy thử vòng lùi → tách lại trên DB dev (có backup, rủi ro bằng không) trước khi dùng `008` ở bất kỳ môi trường nào khác.

---

## 10 · Cách kiểm chứng lại và cách quay lui

### 10.1 · Môi trường

```bash
export PATH="$PATH:/c/Users/<user>/AppData/Local/Programs/DockerDesktop/resources/bin"
cd "<repo>/P-028"
docker compose up -d db
```

Kết nối DBeaver: `localhost:5432` / `ridepulse` / `postgres` / `localpassword`

> **Lưu ý DBeaver:** nếu kết nối đã mở sẵn từ trước, DBeaver giữ cây thư mục trong cache.
> Chuột phải → **Invalidate/Reconnect**, rồi **F5** trên node Schemas.

### 10.2 · Kiểm chứng đầy đủ

```bash
docker compose exec -T db psql -U postgres -d ridepulse < scripts/verify_schema_split.sql
```

Đọc cột `ket_qua`. Đúng thì mọi dòng là `OK`, trừ 3 dòng `analytics · thiếu USAGE` (có sẵn từ trước).

### 10.3 · Kiểm nhanh bằng SQL

```sql
-- Toàn cảnh
SELECT n.nspname AS schema,
       count(*) FILTER (WHERE c.relkind = 'r') AS bang,
       count(*) FILTER (WHERE c.relkind = 'v') AS view
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND c.relkind IN ('r','v')
GROUP BY n.nspname ORDER BY 1;

-- Dữ liệu còn nguyên
SELECT (SELECT count(*) FROM catalog.source_rows)         AS source_rows,      -- 50000
       (SELECT count(*) FROM legacy_agent.proposed_rules) AS proposed_rules,   -- 129
       (SELECT count(*) FROM rules.rule_proposals)        AS rule_proposals,   -- 31
       (SELECT count(*) FROM ops.jobs)                    AS jobs,             -- 10
       (SELECT count(*) FROM iam.user_accounts)           AS users;            -- 3
```

### 10.4 · Test âm — chứng minh thật sự đã đổi

```sql
SELECT count(*) FROM source_rows;          -- 50000  (tên trần, search_path tìm ra)
SELECT count(*) FROM public.source_rows;   -- LỖI: relation does not exist
```

Câu đầu chạy được, câu sau **phải lỗi**. Nếu cả hai đều chạy thì bảng chưa hề chuyển đi đâu.

### 10.5 · Khoá ngoại còn sống

Đếm thôi chưa đủ thuyết phục — **thử vi phạm** một khoá ngoại vượt schema:

```sql
INSERT INTO execution.dq_runs (id, dataset_id, status)
VALUES ('test-fk-999', 'dataset-khong-ton-tai-xyz', 'QUEUED');
```

Kết quả **đúng** là bị chặn:

```
ERROR: insert or update on table "dq_runs" violates foreign key constraint
       "dq_runs_dataset_id_fkey"
```

### 10.6 · Test suite

```bash
venv/Scripts/python.exe -m pytest -q -p no:randomly
# kỳ vọng: 201 passed, 2 skipped  (~2 phút 20 giây)
```

> **Ba lưu ý môi trường:**
> Dùng `venv/`, không phải `.venv/` — chỉ `venv/` có pytest.
> `-p no:randomly` giữ thứ tự test cố định khi truy lỗi.
> **Không chạy hai tiến trình pytest cùng lúc** — chúng tranh file SQLite trong `.pytest_tmp/` và cho ra `PermissionError [WinError 32]` chứ không phải lỗi thật.

### 10.7 · Quay lui

```bash
docker compose exec -T db psql -U postgres -d ridepulse -v ON_ERROR_STOP=1 \
  < scripts/migrations/008_rollback_split_schemas.sql
```

Sau đó: trả `schema.yml:5` về `schema: public`, và restart app.

Nếu rollback cũng hỏng, phục hồi từ dump:

```bash
docker compose exec -T db psql -U postgres -d ridepulse < <scratchpad>/ridepulse_backup_pre008.sql
```

---

## Phụ lục · Bản đồ file

### File mới

| File | Dòng | Trạng thái Git |
|---|---|---|
| `scripts/migrations/008_split_schemas.sql` | 330 | chưa theo dõi |
| `scripts/migrations/008_rollback_split_schemas.sql` | 165 | chưa theo dõi |
| `scripts/verify_schema_split.sql` | 200 | chưa theo dõi |
| `docs/SCHEMA_SPLIT_REPORT.md` | *(file này)* | chưa theo dõi |

> `008_split_schemas.sql` **bắt buộc phải giữ**. Bảy migration `001`–`007` đang trong git;
> ai clone repo về và chạy đủ 7 file sẽ có 24 bảng nằm phẳng trong `public` — khác hoàn
> toàn với DB đang chạy. Không có `008`, cấu trúc 9 schema chỉ tồn tại duy nhất trong
> volume Docker trên một máy. Đó chính là *schema drift* mà cả thư mục `migrations/`
> sinh ra để ngăn chặn.

### File đã sửa

| File | Sửa gì |
|---|---|
| `src/models/rule_schemas.py` | Định nghĩa lại "tham số đang sử dụng"; bắt entry provenance trùng tên |
| `src/agents/nodes/rule_proposer_node.py` | Đồng bộ `parameter_provenance` khi chuẩn hoá evidence; xuất thêm 2 khoá |
| `.env` | Gộp hai dòng `CORS_ORIGINS` trùng lặp |

### Sao lưu

| File | Kích thước | Vị trí |
|---|---|---|
| `ridepulse_backup_pre008.sql` | 11.8 MB | scratchpad, ngoài repo |
| `env.backup` | — | scratchpad, ngoài repo |

---

*Không có thao tác Git nào được thực hiện trong toàn bộ đợt này.*
