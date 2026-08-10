# BÁO CÁO TỔNG HỢP XỬ LÝ DỮ LIỆU SEMANTIC & SẢN PHẨM TRONG THƯ MỤC DATA

> **Dự án:** RidePulse DQ (Autonomous Data Quality & Anomaly Intelligence Platform)  
> **Giai đoạn:** Gate 2 MVP  
> **Nguồn Dữ Liệu Thật:** `data/yellow_tripdata_2025/raw/yellow_tripdata_2025-01.parquet` (3,475,226 bản ghi gốc)  
> **Tệp Nguồn Lookup:** `data/yellow_tripdata_2025/matchinfor/taxi_zone_lookup.csv` và `data_dictionary_trip_records_yellow.xlsx`  
> **Thư Mục Đầu Ra Chuẩn:** `data/yellow_tripdata_2025/semantic_data/`  
> **Trạng Thái:** **Đã hoàn thành 100% (Đã dọn dẹp tệp trùng thừa)**

---

## 1. DANH SÁCH CÁC TỆP SẢN PHẨM CHUẨN TRONG THƯ MỤC `DATA/`

Toàn bộ sản phẩm dữ liệu Gate 2 MVP được quy hoạch tập trung duy nhất tại thư mục **`data/yellow_tripdata_2025/semantic_data/`**:

| STT | Đường dẫn File | Loại Tệp | Nội Dung Chi Tiết |
|:---:|:---|:---:|:---|
| 1 | [`data/yellow_tripdata_2025/semantic_data/yellow_tripdata_2025_semantic_50k.parquet`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/data/yellow_tripdata_2025/semantic_data/yellow_tripdata_2025_semantic_50k.parquet) | Data Parquet | Tệp dữ liệu Parquet 50,000 bản ghi chuẩn đúng **21 cột tiêu chuẩn** đã được thay thế mã số thành chữ tiếng Anh. |
| 2 | [`data/yellow_tripdata_2025/semantic_data/yellow_tripdata_2025_semantic_50k.csv`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/data/yellow_tripdata_2025/semantic_data/yellow_tripdata_2025_semantic_50k.csv) | Data CSV Sample | Tệp mẫu CSV trực quan hóa dạng bảng của bộ dữ liệu semantic 50,000 bản ghi. |
| 3 | [`data/yellow_tripdata_2025/semantic_data/manifest.json`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/data/yellow_tripdata_2025/semantic_data/manifest.json) | Manifest Metadata | Tệp metadata lưu mã checksum SHA-256 thực tế, seeds, schema 21 cột và danh sách kỳ vọng lỗi synthetic. |
| 4 | [`data/yellow_tripdata_2025/semantic_data/SEMANTIC_PROCESSING_REPORT.md`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/data/yellow_tripdata_2025/semantic_data/SEMANTIC_PROCESSING_REPORT.md) | Documentation | Báo cáo chi tiết các thao tác xử lý semantic dữ liệu nằm trực tiếp trong thư mục `data`. |

> **Lưu ý dọn dẹp:** Tệp trùng thừa `data/nyc_yellow_50k.parquet` nằm ngoài root `data/` đã được **xóa bỏ hoàn toàn** để tránh nhân bản dữ liệu rác.

---

## 2. NỘI DUNG CHI TIẾT ĐÃ THỰC HIỆN (WHAT WAS CHANGED)

### 2.1. Cấu Trúc Dữ Liệu 50,000 Bản Ghi
- **48,750 dòng sạch (97.5%):** Dữ liệu mẫu nguyên bản trích xuất từ `yellow_tripdata_2025-01.parquet` với `SAMPLE_SEED = 42`.
- **1,250 dòng chèn lỗi Synthetic (2.5%):** Đột biến cố định với `MUTATION_SEED = 1337` (chia đều 250 dòng cho 5 loại lỗi: `negative_fare_amount`, `negative_trip_distance`, `null_vendor_id`, `invalid_payment_type`, `duplicate_fingerprint`).
- **Khóa nhận dạng:** Gán `source_row_id` duy nhất từ `row-00001` đến `row-50000`.

### 2.2. Xử Lý Chuyển Đổi Giá Trị Trực Tiếp (In-Place Semantic Replacement — Phương Án 1)
Giữ nguyên **đúng 21 cột tiêu chuẩn** (không tạo thêm cột thừa), thực hiện giải mã trực tiếp các mã số category thành chuỗi chữ tiếng Anh có nghĩa nghiệp vụ rõ ràng:

1. **`vendor_id`**: 
   - `1` $\rightarrow$ `"Creative Mobile Technologies, LLC"`
   - `2` $\rightarrow$ `"Curb Mobility, LLC"`
   - `6` $\rightarrow$ `"Myle Technologies Inc"`
   - `7` $\rightarrow$ `"Helix"`
2. **`rate_code_id`**: 
   - `1` $\rightarrow$ `"Standard rate"`
   - `2` $\rightarrow$ `"JFK"`
   - `3` $\rightarrow$ `"Newark"`
   - `4` $\rightarrow$ `"Nassau or Westchester"`
   - `5` $\rightarrow$ `"Negotiated fare"`
   - `6` $\rightarrow$ `"Group ride"`
   - `99` $\rightarrow$ `"Null/Unknown"`
3. **`payment_type`**: 
   - `0` $\rightarrow$ `"Flex Fare trip"`
   - `1` $\rightarrow$ `"Credit card"`
   - `2` $\rightarrow$ `"Cash"`
   - `3` $\rightarrow$ `"No charge"`
   - `4` $\rightarrow$ `"Dispute"`
   - `5` $\rightarrow$ `"Unknown"`
   - `6` $\rightarrow$ `"Voided trip"`
   - `99` $\rightarrow$ `"Invalid Payment (Dispute/Test)"`
4. **`pickup_location_id`**: Ghép nối `taxi_zone_lookup.csv` qua `LocationID`, thay bằng nhãn chữ: `"{Borough} ({Zone})"`, ví dụ: `"Manhattan (Upper West Side North)"`.
5. **`dropoff_location_id`**: Ghép nối `taxi_zone_lookup.csv` qua `LocationID`, thay bằng nhãn chữ: `"{Borough} ({Zone})"`, ví dụ: `"Manhattan (Morningside Heights)"`.

---

## 3. KẾT QUẢ ĐẠT ĐƯỢC (RESULTS & VERIFICATION)

### 3.1. Metadata Manifest (`data/yellow_tripdata_2025/semantic_data/manifest.json`)
- **Checksum SHA-256 Hash thực tế:** `b1549ceb43dee8e083e34d81b22db37c3afa401737e831c7ed63fb83a5baeff7`

### 3.2. Kiểm Thử Tự Động (Automated Testing)
- **Pytest Suite ([`tests/unit/test_semantic_data.py`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/tests/unit/test_semantic_data.py)):** `4 passed in 0.68s` (Đúng 21 cột, thay thế 100% mã số bằng chữ)
- **Ruff Code Style Check:** `All checks passed!` (0 warning, 0 error).
