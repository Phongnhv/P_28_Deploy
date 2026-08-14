# Báo Cáo Tổng Hợp Thực Hiện PR 4: `gate2: dbt core project setup, staging/profile models and data contract tests`

> **Dự án:** RidePulse DQ (Autonomous Data Quality & Anomaly Intelligence Platform)  
> **Giai đoạn:** Gate 2 MVP  
> **Tên Pull Request:** `gate2: dbt core project setup, staging/profile models and data contract tests`  
> **Chủ sở hữu (Owner):** Lương Trung Chiến (Product Owner / BA / Data Lead)  
> **Người kiểm duyệt (Reviewer):** Nguyễn Hữu Kiên (Technical Lead / AI Backend)  
> **Trạng thái:** **Đã hoàn thành 100% (Ready for PR Review & Merge)**

---

## 1. TỔNG QUAN MỤC TIÊU CỦA PR 4

PR 4 là **lớp biến đổi và kiểm thử dữ liệu (Data Transformation & Contract Testing Layer)** của dự án trong giai đoạn Gate 2 MVP. 

Mục tiêu chính của PR 4:
1. Đóng gói một dbt Core project (`ridepulse_dbt`) độc lập nằm trong thư mục `dbt_project/`.
2. Tạo model **`stg_trips`** biến đổi và chuẩn hóa dữ liệu từ bảng thô `public.trips_raw` sang schema `analytics`.
3. Tạo model **`profile_input`** trích xuất tập cột cố định phục vụ cho Profiler Agent (`src/agents/tools/db_profiler_tool.py`).
4. Khai báo dbt Data Contract Tests (`not_null`, `unique`) trong file `schema.yml` để kiểm thử tự động tính toàn vẹn dữ liệu.
5. Cung cấp bộ unit test kiểm thử tự động (`tests/unit/test_dbt_project.py`) và bổ sung thư viện dbt vào `requirements.txt`.

---

## 2. DANH SÁCH CHI TIẾT CÁC TỆP ĐÃ THAY ĐỔI & THÊM MỚI

| STT | Đường dẫn File | Loại Thao Tác | Nội Dung & Mục Đích Chi Tiết |
|:---:|:---|:---:|:---|
| 1 | [`requirements.txt`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/requirements.txt) | **SỬA (MODIFY)** | Bổ sung `dbt-core>=1.8.0` và `dbt-postgres>=1.8.0` dưới section `# DE / dbt` để cài đặt công cụ dbt CLI vào môi trường Python. |
| 2 | [`dbt_project/dbt_project.yml`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/dbt_project/dbt_project.yml) | **THÊM MỚI (NEW)** | Khai báo cấu hình dự án dbt `ridepulse_dbt`, profile `ridepulse`, định tuyến các models `staging` và `analytics` tự động lưu vào schema `analytics`. |
| 3 | [`dbt_project/profiles.yml`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/dbt_project/profiles.yml) | **THÊM MỚI (NEW)** | Cấu hình profile kết nối cơ sở dữ liệu `ridepulse` cho dbt CLI, nhận các biến môi trường (`DBT_HOST`, `DBT_PORT`, `DBT_USER`, `DBT_PASSWORD`, `DBT_DBNAME`, `DBT_SCHEMA`). |
| 4 | [`dbt_project/models/staging/stg_trips.sql`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/dbt_project/models/staging/stg_trips.sql) | **THÊM MỚI (NEW)** | Model Staging dạng `view`: Đọc dữ liệu từ `public.trips_raw`, chuẩn hóa ép kiểu chuẩn xác cho 21 cột dữ liệu NYC Yellow Taxi. |
| 5 | [`dbt_project/models/analytics/profile_input.sql`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/dbt_project/models/analytics/profile_input.sql) | **THÊM MỚI (NEW)** | Model Analytics dạng `view`: Đọc từ `{{ ref('stg_trips') }}`, trích xuất 12 cột cố định phục vụ trực tiếp cho Profiler Agent quét thống kê. |
| 6 | [`dbt_project/models/schema.yml`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/dbt_project/models/schema.yml) | **THÊM MỚI (NEW)** | Tệp khai báo Metadata & Data Contract: Đăng ký source `public.trips_raw` và bổ sung các dbt test cases (`not_null`, `unique` trên `source_row_id`). |
| 7 | [`tests/unit/test_dbt_project.py`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/tests/unit/test_dbt_project.py) | **THÊM MỚI (NEW)** | Bộ unit test kiểm thử tự động 5 kịch bản: kiểm tra file tồn tại, validate YAML syntax, kiểm tra Jinja `ref()` & `source()`, và thực thi `dbt parse`. |
| 8 | [`docs/gate2-mvp/PR4_SUMMARY.md`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/docs/gate2-mvp/PR4_SUMMARY.md) | **THÊM MỚI (NEW)** | Tệp báo cáo chi tiết này giúp đồng đội (Đạt, Phong, Kiên) hiểu rõ toàn bộ kiến trúc và thay đổi trong PR 4. |

---

## 3. CHI TIẾT KỸ THUẬT VÀ NGUYÊN LÝ HOẠT ĐỘNG

### 3.1. Luồng Dữ Liệu Transform (Data Transformation Pipeline)

```text
[Bảng Thô: public.trips_raw] 
          │
          ▼  (dbt ref/source)
[Staging Model: analytics.stg_trips]  <-- Ép kiểu 21 cột tiêu chuẩn
          │
          ▼  (dbt ref)
[Analytics Model: analytics.profile_input]  <-- Cung cấp 12 cột cho Profiler Agent
          │
          ▼  (Data Contract Tests)
[dbt Test Suite: not_null & unique checks]
```

### 3.2. Mapping Chi Tiết Các Models dbt

1. **`stg_trips.sql` (Staging Layer):**
   - Ép kiểu dữ liệu an toàn cho 21 cột tiêu chuẩn:
     - Timestamp: `pickup_at`, `dropoff_at`
     - Double precision: `trip_distance`, `fare_amount`, `extra`, `mta_tax`, `tip_amount`, `tolls_amount`, `improvement_surcharge`, `total_amount`, `congestion_surcharge`, `airport_fee`, `cbd_congestion_fee`
     - Integer: `vendor_id`, `passenger_count`, `rate_code_id`, `pickup_location_id`, `dropoff_location_id`, `payment_type`
     - Text: `source_row_id`, `store_and_fwd_flag`

2. **`profile_input.sql` (Analytics Layer):**
   - Lọc chính xác 12 cột quan trọng nhất cho Profiler Agent (`db_profiler_tool.py`):
     `source_row_id`, `vendor_id`, `pickup_at`, `dropoff_at`, `passenger_count`, `trip_distance`, `rate_code_id`, `payment_type`, `fare_amount`, `total_amount`, `pickup_location_id`, `dropoff_location_id`.

3. **`schema.yml` (Data Contract Layer):**
   - Ràng buộc Data Contract: `source_row_id` bắt buộc phải `not_null` và `unique` trên cả `stg_trips` và `profile_input`.

---

## 4. KẾT QUẢ KIỂM THỬ VÀ VERIFICATION

Tất cả các kiểm thử tự động và kiểm tra định dạng code đều đạt **PASS 100%**:

```powershell
# 1. Chạy bộ kiểm thử tự động Pytest cho dbt project (5 test cases)
.\venv\Scripts\python.exe -m pytest tests/unit/test_dbt_project.py -v --basetemp=.pytest_tmp
# Output: 5 passed in 0.52s

# 2. Kiểm tra tuân thủ Code Format & Linting (Ruff)
.\venv\Scripts\python.exe -m ruff check src tests
# Output: All checks passed!
```

---

## 5. ĐIỀU KIỆN MERGE & HƯỚNG DẪN REVIEW CHO ĐỒNG ĐỘI

PR 4 đã đáp ứng 100% **Done When Condition** ghi trong [TEAM_PLAN.md](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/docs/gate2-mvp/TEAM_PLAN.md):
- [x] **Project Structure:** Tạo xong `dbt_project/` với `dbt_project.yml` và `profiles.yml`.
- [x] **Models:** Tạo xong `stg_trips.sql` và `profile_input.sql` trong schema `analytics`.
- [x] **Tests:** Tạo xong `models/schema.yml` và `tests/unit/test_dbt_project.py` pass 100%.

**Hướng dẫn dành cho Reviewer (Nguyễn Hữu Kiên):**
1. Kiểm tra cấu trúc thư mục `dbt_project/` và các models trong `models/staging/` & `models/analytics/`.
2. Chạy lệnh `.\venv\Scripts\python.exe -m pytest tests/unit/test_dbt_project.py -v --basetemp=.pytest_tmp` để verify.
3. Approve và merge PR 4 vào branch `main`.
