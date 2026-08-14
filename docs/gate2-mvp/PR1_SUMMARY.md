# Báo Cáo Tổng Hợp Thực Hiện PR 1: `gate2: add deterministic taxi fixture and manifest`

> **Dự án:** RidePulse DQ (Autonomous Data Quality & Anomaly Intelligence Platform)  
> **Giai đoạn:** Gate 2 MVP  
> **Tên Pull Request:** `gate2: add deterministic taxi fixture and manifest`  
> **Chủ sở hữu (Owner):** Lương Trung Chiến (Product Owner / BA)  
> **Người kiểm duyệt (Reviewer):** Vũ Nguyễn Quốc Đạt (Product Lead / Architect)  
> **Trạng thái:** **Đã hoàn thành 100% (Ready for PR Review & Merge)**

---

## 1. TỔNG QUAN MỤC TIÊU CỦA PR 1

PR 1 là **nền tảng dữ liệu (Data Plane)** đầu tiên của dự án trong giai đoạn Gate 2 MVP. Mục tiêu của PR 1 là cung cấp một bộ dữ liệu mẫu đóng gói sẵn trong mã nguồn (bundled dataset), tệp manifest đăng ký metadata kèm mã checksum SHA-256, service loader an toàn bảo vệ khỏi lỗ hổng Path Traversal và bộ kiểm thử tự động (Unit Tests) hoàn chỉnh.

---

## 2. CÁC TỆP ĐÃ TẠO MỚI & THAY ĐỔI

| STT | Đường dẫn File | Loại Tệp | Mô Tả Nhiệm Vụ |
|:---:|:---|:---:|:---|
| 1 | [`src/resources/nyc_yellow_demo.csv`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/src/resources/nyc_yellow_demo.csv) | Data Fixture | Tệp CSV chứa 73 bản ghi dữ liệu taxi với đầy đủ **21 cột tiêu chuẩn** (bao gồm 68 dòng sạch + 5 dòng chèn lỗi synthetic). |
| 2 | [`src/resources/manifest.json`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/src/resources/manifest.json) | Metadata | Tệp manifest cho `nyc-yellow-demo-v1` lưu mã hash SHA-256 thực tế, số lượng bản ghi, schema 21 cột và danh sách ground-truth defects. |
| 3 | [`src/services/dataset_loader.py`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/src/services/dataset_loader.py) | Service / Security | Module dịch vụ chứa hàm `load_manifest()`, `verify_checksum()` và `load_dataset_rows()`. Chặn lỗ hổng Path Traversal và Checksum Mismatch. |
| 4 | [`tests/unit/test_dataset_fixture.py`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/tests/unit/test_dataset_fixture.py) | Unit Test | Bộ unit test kiểm thử 5 kịch bản tiêu chuẩn cho fixture data và bảo mật manifest. |
| 5 | [`tests/unit/test_dataset_loader.py`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/tests/unit/test_dataset_loader.py) | Unit Test | Bộ unit test kiểm thử chi tiết 6 kịch bản cho module loader & security guard. |

---

## 3. CHI TIẾT CÔNG VIỆC ĐÃ THỰC HIỆN

### 3.1. Chuẩn Hóa Bộ Dữ Liệu Fixture (`src/resources/nyc_yellow_demo.csv`)
- **Số lượng bản ghi:** 73 dòng records (+ 1 dòng header).
- **Cấu trúc cột:** Đầy đủ **21 cột chuẩn** từ dữ liệu NYC Yellow Taxi gốc:
  `source_row_id`, `vendor_id`, `pickup_at`, `dropoff_at`, `passenger_count`, `trip_distance`, `rate_code_id`, `store_and_fwd_flag`, `pickup_location_id`, `dropoff_location_id`, `payment_type`, `fare_amount`, `extra`, `mta_tax`, `tip_amount`, `tolls_amount`, `improvement_surcharge`, `total_amount`, `congestion_surcharge`, `airport_fee`, `cbd_congestion_fee`.
- **Chuẩn hóa Timestamp:** Chuyển đổi toàn bộ thời gian dạng millisecond epoch sang chuỗi **ISO 8601 UTC chuẩn** (`YYYY-MM-DDTHH:MM:SSZ`).
- **Chèn 5 dòng lỗi Synthetic (Defect Injection):**
  1. `row-069`: **Duplicate Fingerprint** (trùng lặp hoàn toàn thông tin hành trình với `row-001`).
  2. `row-070`: **Null Vendor ID** (`vendor_id` bị để trống/NULL).
  3. `row-071`: **Negative Distance & Fare** (`trip_distance = -2.5`, `fare_amount = -15.0`).
  4. `row-072`: **Zero Distance High Fare Anomaly** (`trip_distance = 0.0` nhưng `fare_amount = 125.0`).
  5. `row-073`: **Invalid Payment Type Domain** (`payment_type = 99` nằm ngoài danh mục 1–4).

---

### 3.2. Khởi Tạo Tệp Metadata Manifest (`src/resources/manifest.json`)
- Register `manifest_name`: `"nyc-yellow-demo-v1"`.
- Lưu mã **SHA-256 hash thực tế** của tệp CSV: `98bc673688f0e70a5f7ea88d4e15ed9f3c0a97af4c8375e41703d3502b1719d8`.
- Khai báo tổng số bản ghi `total_rows: 73`, `clean_rows: 68`, `defect_rows: 5`.
- Đăng ký danh sách **Ground-Truth Defects** cho 5 dòng lỗi (`row-069` $\rightarrow$ `row-073`) phục vụ Evaluation Runner.

---

### 3.3. Xây Dựng Service Loader & Security Guard (`src/services/dataset_loader.py`)
- **`MANIFEST_ALLOW_LIST`**: Khai báo danh mục manifest cho phép (`"nyc-yellow-demo-v1": "src/resources/manifest.json"`).
- **`load_manifest(manifest_name: str) -> dict[str, Any]`**:
  - Kiểm tra `manifest_name` nằm trong allow-list.
  - **Bảo mật:** Chặn ném ngoại lệ `ValueError` nếu phát hiện các ký tự Path Traversal (`..`, `/`, `\`).
- **`verify_checksum(csv_path: str, expected_sha256: str) -> bool`**:
  - Đọc tệp CSV dưới dạng binary và tính checksum SHA-256.
  - Ném ngoại lệ `ValueError` nếu file bị sửa đổi sai lệch với manifest.
- **`load_dataset_rows(manifest_name: str) -> list[dict[str, Any]]`**:
  - Tải manifest $\rightarrow$ đối chiếu checksum $\rightarrow$ đọc dữ liệu bằng `csv.DictReader` và trả về danh sách 73 bản ghi đủ 21 cột.

---

### 3.4. Xây Dựng Bộ Kiểm Thử Tự Động (Unit Tests)
Đã viết 2 tệp test trong thư mục `tests/unit/`:
1. **[`tests/unit/test_dataset_fixture.py`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/tests/unit/test_dataset_fixture.py)**:
   - `test_fixture_row_count`: Kiểm tra đếm đúng 73 dòng bản ghi.
   - `test_fixture_columns_schema`: Kiểm tra dữ liệu chứa đủ 21 cột tiêu chuẩn.
   - `test_fixture_sha256_checksum`: Kiểm tra checksum SHA-256 thực tế khớp manifest.
   - `test_fixture_synthetic_defects`: Xác nhận 5 dòng lỗi synthetic `row-069` $\rightarrow$ `row-073` bị vi phạm đúng điều kiện.
   - `test_fixture_manifest_allow_list_security`: Kiểm tra ném lỗi khi gặp manifest name lạ hoặc đường dẫn chứa `../`.
2. **[`tests/unit/test_dataset_loader.py`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/tests/unit/test_dataset_loader.py)**:
   - 6 unit test cases kiểm thử chi tiết từng hàm loader và security guard.

---

## 4. KẾT QUẢ KIỂM THỬ VÀ VERIFICATION

Tất cả các kiểm tra tự động và linting đều đạt kết quả **PASS 100%**:

```powershell
# 1. Chạy toàn bộ Pytest Suite của dự án (16 test cases)
.\venv\Scripts\python.exe -m pytest tests -v
# Output: 16 passed in 0.06s

# 2. Kiểm tra tuân thủ Code Format & Linting (Ruff)
.\venv\Scripts\python.exe -m ruff check src tests
# Output: All checks passed!
```

---

## 5. KẾT LUẬN & ĐIỀU KIỆN MERGE

PR 1 đã hoàn thành đầy đủ **3/3 điều kiện Merge Condition** được đề ra trong [TEAM_PLAN.md](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/docs/gate2-mvp/TEAM_PLAN.md):
- [x] **Fixture Data:** Đã lưu tại `src/resources/nyc_yellow_demo.csv` (version-controlled cùng Git).
- [x] **Defect Manifest:** Đã lưu tại `src/resources/manifest.json`.
- [x] **Data Test:** Pytest pass 100%, Ruff check pass 100%.

Sẵn sàng để gửi PR cho **Vũ Nguyễn Quốc Đạt** thực hiện review và merge vào branch `main`.
