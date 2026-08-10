# Báo Cáo Thiết Kế Pipeline Xử Lý Semantic Dữ Liệu & Derived Features (Phương Án 1)

> **Dự án:** RidePulse DQ (Autonomous Data Quality & Anomaly Intelligence Platform)  
> **Giai đoạn:** Gate 2 MVP  
> **Tệp Nguồn Dữ Liệu Thật:** `data/yellow_tripdata_2025/raw/yellow_tripdata_2025-01.parquet`  
> **Tệp Nguồn Lookup:** `data/yellow_tripdata_2025/matchinfor/taxi_zone_lookup.csv` và `data_dictionary_trip_records_yellow.xlsx`  
> **Thư Mục Đầu Ra:** `data/yellow_tripdata_2025/semantic_data/`  
> **Trạng Thái:** **Đã hoàn thành 100%**

---

## 1. TỔNG QUAN THIẾT KẾ PHƯƠNG ÁN 1

Thay vì ép kiểu hay normalize dữ liệu thành dạng số cho Machine Learning, Pipeline thực hiện **Semantic Processing (Giải mã ý nghĩa nghiệp vụ)** theo **Phương án 1 (Direct Value Replacement)**:
- Thay thế trực tiếp các mã số category bất hợp lý đối với LLM (ví dụ: `VendorID: 1`, `payment_type: 1`, `PULocationID: 236`) bằng các nhãn chuỗi chữ tiếng Anh có nghĩa nghiệp vụ rõ ràng (`"Creative Mobile Technologies, LLC"`, `"Credit card"`, `"Manhattan (Upper East Side North)"`).
- Giúp LLM Agent suy luận chính xác các quy tắc Data Quality (như: phí sân bay chỉ áp dụng cho khu vực sân bay, cước giao dịch Cash không có tip tự động, thời gian di chuyển không được âm, v.v.).

---

## 2. QUY TẮC MAPPING GIẢI MÃ CATEGORY TRỰC TIẾP (IN-PLACE REPLACEMENT)

### 2.1. VendorID (`vendor_id`)
- `1` $\rightarrow$ `"Creative Mobile Technologies, LLC"`
- `2` $\rightarrow$ `"Curb Mobility, LLC"`
- `6` $\rightarrow$ `"Myle Technologies Inc"`
- `7` $\rightarrow$ `"Helix"`
- Khác / `NULL` $\rightarrow$ `"Unknown Vendor"`

### 2.2. RatecodeID (`rate_code_id`)
- `1` $\rightarrow$ `"Standard rate"`
- `2` $\rightarrow$ `"JFK"`
- `3` $\rightarrow$ `"Newark"`
- `4` $\rightarrow$ `"Nassau or Westchester"`
- `5` $\rightarrow$ `"Negotiated fare"`
- `6` $\rightarrow$ `"Group ride"`
- `99` $\rightarrow$ `"Null/Unknown"`
- Khác / `NULL` $\rightarrow$ `"Unknown Ratecode"`

### 2.3. payment_type (`payment_type`)
- `0` $\rightarrow$ `"Flex Fare trip"`
- `1` $\rightarrow$ `"Credit card"`
- `2` $\rightarrow$ `"Cash"`
- `3` $\rightarrow$ `"No charge"`
- `4` $\rightarrow$ `"Dispute"`
- `5` $\rightarrow$ `"Unknown"`
- `6` $\rightarrow$ `"Voided trip"`
- `99` $\rightarrow$ `"Invalid Payment (Dispute/Test)"`
- Khác / `NULL` $\rightarrow$ `"Unknown Payment"`

### 2.4. Location IDs (`pickup_location_id` & `dropoff_location_id`)
- JOIN với `taxi_zone_lookup.csv` qua `LocationID`.
- Thay thế trực tiếp mã số bằng chuỗi nhãn: `"{Borough} ({Zone})"`, ví dụ: `"Manhattan (Upper West Side North)"` hoặc `"Queens (LaGuardia Airport)"`.
- Thêm 6 cột thông tin vị trí phụ trợ: `pu_borough`, `pu_zone`, `pu_service_zone`, `do_borough`, `do_zone`, `do_service_zone`.

---

## 3. DANH MỤC BIẾN LIÊN KẾT ĐƯỢC TẠO MỚI (DERIVED FEATURES)

| Tên Biến Mới | Công Thức / Nguồn | Ý Nghĩa Nghiệp Vụ | Quy Tắc Data Quality (DQ Rule Candidate) |
|:---|:---|:---|:---|
| **`trip_duration_minutes`** | `dropoff_at` - `pickup_at` (phút) | Thời gian thực hiện chuyến xe | `trip_duration_minutes >= 0` |
| **`average_speed_mph`** | `trip_distance` / (`trip_duration_minutes` / 60) | Tốc độ trung bình (mph) | `average_speed_mph <= 100` |
| **`fare_per_mile`** | `fare_amount` / `trip_distance` | Đơn giá cước trên mỗi mile | Kiểm tra giá trị bất thường (outlier) |
| **`calculated_total_amount`** | Sum các phụ phí cấu thành cước | Tổng cước tự tính toán lại | So sánh với `total_amount` thực tế |
| **`amount_difference`** | `total_amount` - `calculated_total_amount` | Chênh lệch cước tổng | `abs(amount_difference) < 0.01` |
| **`is_airport_pickup`** | `pu_service_zone` chứa "Airport" | Cờ báo đón tại sân bay | `airport_fee > 0` $\leftrightarrow$ Airport pickup |
| **`cbd_fee_date_validity`** | Cước CBD > 0 $\rightarrow$ ngày $\ge$ 2025-01-05 | Kiểm tra ngày áp dụng cước CBD Relief Zone | Phí CBD chỉ hợp lệ từ ngày 05/01/2025 |
| **`pickup_date`** | Chuỗi `YYYY-MM-DD` từ `pickup_at` | Ngày pickup | Phân tích xu hướng theo ngày |
| **`pickup_hour`** | Giờ từ 0 đến 23 từ `pickup_at` | Giờ pickup | Kiểm tra phụ phí đêm/giờ cao điểm |
| **`pickup_day_of_week`** | Tên thứ trong tuần (Monday...Sunday) | Thứ trong tuần | Kiểm tra phụ phí rush-hour ngày thường |

---

## 4. MA TRẬN MỐI QUAN HỆ GIỮA CÁC CỘT (FEATURE RELATIONSHIP MATRIX FOR LLM)

```text
[pickup_at] ──────┐
                  ├─> trip_duration_minutes ──┐
[dropoff_at] ─────┘                           │
                                              ├─> average_speed_mph (DQ Check: <= 100 mph)
[trip_distance] ──────────────────────────────┘

[PULocationID] ──> pu_zone/service_zone ──> is_airport_pickup ──> airport_fee validation

[fare_amount + extra + mta_tax + tip + tolls + surcharges] ──> calculated_total_amount ──> amount_difference
```

---

## 5. CÁC TỆP SẢN PHẨM ĐẦU RA

1. **[`data/yellow_tripdata_2025/semantic_data/yellow_tripdata_2025_semantic_50k.parquet`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/data/yellow_tripdata_2025/semantic_data/yellow_tripdata_2025_semantic_50k.parquet)**: Tệp dữ liệu làm giàu Parquet 50,000 dòng.
2. **[`data/yellow_tripdata_2025/semantic_data/yellow_tripdata_2025_semantic_50k.csv`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/data/yellow_tripdata_2025/semantic_data/yellow_tripdata_2025_semantic_50k.csv)**: Tệp mẫu CSV trực quan hóa.
3. **[`data/yellow_tripdata_2025/semantic_data/manifest.json`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/data/yellow_tripdata_2025/semantic_data/manifest.json)**: Tệp manifest metadata ghi SHA-256 hash `fe88a5cee6677c540290054322ba77e5aa6ed677bc81a332da751a61fe98b01f`.
