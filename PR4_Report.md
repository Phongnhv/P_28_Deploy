# Báo Cáo Chi Tiết Thực Hiện PR 4: `dbt Core project`

> **Dự án:** RidePulse DQ (Autonomous Data Quality & Anomaly Intelligence Platform)  
> **Giai đoạn:** Gate 2 MVP  
> **Tên Pull Request:** `gate2: dbt core project setup, staging/profile models and data contract tests`  
> **Chủ sở hữu (Owner):** Lương Trung Chiến (Product Owner / Data Lead)  
> **Người kiểm duyệt (Reviewer):** Nguyễn Hữu Kiên (Technical Lead / AI Backend)  
> **Trạng thái:** **Đã hoàn thành 100% (Ready for PR Review & Merge)**

---

## 1. TỔNG QUAN VỀ PR 4 VÀ MỤC ĐÍCH THỰC HIỆN

PR 4 là **Lớp biến đổi và kiểm thử dữ liệu (Data Transformation & Contract Testing Layer)** trong kiến trúc Gate 2 MVP của RidePulse DQ.

### Mục đích chính của PR 4:
1. Đóng gói một dự án **dbt Core** (`ridepulse_dbt`) nằm trong thư mục `dbt_project/`.
2. Chuẩn hóa dữ liệu thô từ bảng `public.trips_raw` sang schema `analytics` thông qua model **`stg_trips`**.
3. Trích xuất tập cột cố định phục vụ trực tiếp cho Profiler Agent (`db_profiler_tool.py`) thông qua model **`profile_input`**.
4. Khai báo các dbt Data Contract Tests (`not_null`, `unique`) trong file `schema.yml` để kiểm tra tự động tính toàn vẹn dữ liệu.
5. Cung cấp bộ unit tests kiểm thử tự động `tests/unit/test_dbt_project.py` và cập nhật thư viện `dbt-core`, `dbt-postgres` vào `requirements.txt`.

---

## 2. BẢNG TỔNG HỢP CHI TIẾT CÁC FILE ĐÃ SỬA VÀ THÊM MỚI

| STT | Tên / Đường dẫn File | Loại Thao Tác | Nội Dung Đã Thêm / Sửa Chi Tiết | Lý Do & Mục Đích Thực Hiện |
|:---:|:---|:---:|:---|:---|
| 1 | [`requirements.txt`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/requirements.txt) | **SỬA (MODIFY)** | Bổ sung `dbt-core>=1.8.0` và `dbt-postgres>=1.8.0` dưới mục `# DE / dbt`. | Khai báo thư viện dbt CLI bắt buộc cho dự án Python. |
| 2 | [`dbt_project/dbt_project.yml`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/dbt_project/dbt_project.yml) | **THÊM MỚI (NEW)** | Khởi tạo cấu hình project dbt `ridepulse_dbt`, profile `ridepulse`, định tuyến models lưu tự động vào schema `analytics`. | Cấu hình dự án dbt Core theo chuẩn official dbt specification. |
| 3 | [`dbt_project/profiles.yml`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/dbt_project/profiles.yml) | **THÊM MỚI (NEW)** | Cấu hình profile kết nối PostgreSQL cho dbt CLI sử dụng các biến môi trường (`DBT_HOST`, `DBT_PORT`, `DBT_USER`, `DBT_PASSWORD`, `DBT_DBNAME`, `DBT_SCHEMA`). | Cho phép dbt CLI kết nối linh hoạt tới Database local hoặc Google Cloud Run mà không bị hardcode credentials. |
| 4 | [`dbt_project/models/staging/stg_trips.sql`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/dbt_project/models/staging/stg_trips.sql) | **THÊM MỚI (NEW)** | Staging dbt Model (`view`): Đọc từ `public.trips_raw`, ép kiểu dữ liệu an toàn chuẩn xác cho 21 cột tiêu chuẩn NYC Yellow Taxi. | Chuẩn hóa toàn bộ kiểu dữ liệu (timestamp, double precision, integer, text) từ bảng dữ liệu thô. |
| 5 | [`dbt_project/models/analytics/profile_input.sql`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/dbt_project/models/analytics/profile_input.sql) | **THÊM MỚI (NEW)** | Analytics dbt Model (`view`): Đọc từ `{{ ref('stg_trips') }}`, trích xuất 12 cột cố định quan trọng. | Cung cấp đúng giao diện bảng `profile_input` phục vụ trực tiếp cho Profiler Agent quét thống kê. |
| 6 | [`dbt_project/models/schema.yml`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/dbt_project/models/schema.yml) | **THÊM MỚI (NEW)** | Khai báo metadata source `public.trips_raw` và đăng ký dbt tests (`not_null`, `unique` trên `source_row_id`). | Đảm bảo tính toàn vẹn dữ liệu (Data Contract) bằng dbt schema tests tự động. |
| 7 | [`tests/unit/test_dbt_project.py`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/tests/unit/test_dbt_project.py) | **THÊM MỚI (NEW)** | Bộ unit test suite (5 test cases) kiểm thử tính tồn tại của file, cấu hình YAML, cú pháp Jinja `ref()` / `source()`, và lệnh `dbt parse`. | Đảm bảo dbt project được kiểm thử tự động 100% trong CI/CD pipeline. |
| 8 | [`docs/gate2-mvp/PR4_SUMMARY.md`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/docs/gate2-mvp/PR4_SUMMARY.md) | **THÊM MỚI (NEW)** | Tệp báo cáo tổng hợp nghiệm thu PR 4 trong thư mục `docs/gate2-mvp/`. | Lưu trữ tài liệu nghiệm thu chuẩn hóa cho dự án Gate 2 MVP. |
| 9 | [`PR4_Report.md`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/PR4_Report.md) | **THÊM MỚI (NEW)** | Tệp báo cáo chi tiết này tại gốc repository giúp đồng đội dễ dàng xem lại. | Cung cấp tài liệu tổng quan dễ tiếp cận ngay tại gốc dự án cho các thành viên trong đội. |
| 10 | [`scripts/log_hook.py`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/scripts/log_hook.py) & [`scripts/log_antigravity.py`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/scripts/log_antigravity.py) | **SỬA (MODIFY)** | Nâng cấp logic lấy tên repository bằng `git rev-parse --show-toplevel`. | Tránh lỗi sai tên repo khi đứng từ thư mục con để thực thi scripts. |

---

## 3. LUỒNG DỮ LIỆU VÀ KIẾN TRÚC BIẾN ĐỔI (DATA PIPELINE)

```text
[Bảng Thô: public.trips_raw] 
          │
          ▼  (dbt source)
[Staging Model: analytics.stg_trips]  <-- Ép kiểu dữ liệu 21 cột tiêu chuẩn
          │
          ▼  (dbt ref)
[Analytics Model: analytics.profile_input]  <-- Trích xuất 12 cột cho Profiler Agent
          │
          ▼  (Data Contract Tests)
[dbt Test Suite: not_null & unique checks]
```

---

## 4. KẾT QUẢ KIỂM THỬ VÀ HƯỚNG DẪN TEST DÀNH CHO ĐỒNG ĐỘI

Tất cả các kiểm thử tự động đều đạt **PASS 100%**:

```powershell
# 1. Chạy bộ unit tests kiểm thử dbt project (5 passed)
.\venv\Scripts\python.exe -m pytest tests/unit/test_dbt_project.py -v --basetemp=.pytest_tmp

# 2. Kiểm tra tuân thủ linter (All checks passed)
.\venv\Scripts\python.exe -m ruff check tests/unit/test_dbt_project.py
```

### Hướng dẫn dành cho Reviewer (Nguyễn Hữu Kiên):
1. Kiểm tra cấu trúc thư mục `dbt_project/` và các file models.
2. Chạy lệnh pytest kiểm thử ở trên.
3. Phê duyệt (Approve) và Merge PR #4 từ nhánh `chien` vào `main`.
