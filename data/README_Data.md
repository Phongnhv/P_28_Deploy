
# Ride Service Data Quality Simulation

## 1. Giới thiệu

Dự án này xây dựng một bộ dữ liệu mô phỏng cho hệ thống dịch vụ đặt xe, được chuyển đổi từ dataset gốc `rideshare_kaggle.csv`.

Dataset sau khi xử lý được tách thành bốn bảng nghiệp vụ:

1. `dich_vu_xe_trips`
2. `drivers`
3. `customers`
4. `payments`

Bộ dữ liệu được cố ý chèn các lỗi chất lượng dữ liệu để phục vụ việc:

* Kiểm thử Data Quality.
* Xây dựng Data Quality Agent.
* Viết các quy tắc kiểm tra dữ liệu.
* Kiểm thử SQL validation.
* Kiểm thử pipeline ETL.
* Đánh giá khả năng phát hiện dữ liệu bất thường.
* Thực hành làm sạch dữ liệu trước khi đưa vào hệ thống production.

Các lỗi được chèn bao gồm:

* `NULL` trong khóa chính.
* Giá cước âm.
* Số tiền thanh toán âm.
* Email sai định dạng.
* Số điện thoại sai định dạng.
* Mã giấy phép tài xế sai định dạng.
* Mã giao dịch sai định dạng.
* Thời gian sai định dạng.
* Dữ liệu cập nhật trễ, hay còn gọi là `freshness lag`.
* Khóa ngoại không tồn tại.

---

# 2. Dataset nguồn

## 2.1. File đầu vào

```text
rideshare_kaggle.csv
```

Dataset nguồn chứa thông tin về các dịch vụ xe Uber và Lyft, bao gồm:

* Thời gian báo giá.
* Điểm đón.
* Điểm đến.
* Hãng xe.
* Loại dịch vụ.
* Giá cước.
* Khoảng cách.
* Hệ số tăng giá.
* Vị trí địa lý.
* Thông tin thời tiết.

## 2.2. Thống kê tổng quan

| Thuộc tính                 |         Giá trị |
| ---------------------------- | ----------------: |
| Số dòng                    |           693.071 |
| Số cột                     |                57 |
| Tổng số ô dữ liệu       |        39.505.047 |
| Dung lượng file            | Khoảng 350,36 MB |
| Bộ nhớ DataFrame           | Khoảng 725,08 MB |
| Số dòng trùng lặp        |                 0 |
| Số bản ghi thiếu`price` |            55.095 |
| Tỷ lệ thiếu`price`      |             7,95% |

## 2.3. Một số nhận xét về dữ liệu nguồn

### Cột `id`

* Có 693.071 giá trị duy nhất.
* Không có giá trị thiếu.
* Có thể sử dụng làm mã định danh ban đầu của mỗi bản ghi chuyến xe.

### Cột `price`

* Có 55.095 giá trị thiếu.
* Chiếm khoảng 7,95% tổng số bản ghi.
* Phần lớn giá trị thiếu liên quan đến dịch vụ `Taxi`.
* Đây có thể là dữ liệu thiếu có tính hệ thống thay vì thiếu ngẫu nhiên.

### Cột `timezone`

* Chỉ có một giá trị:

```text
America/New_York
```

* Đây là cột hằng và không mang nhiều giá trị phân biệt dữ liệu.

### Cột `visibility` và `visibility.1`

Hai cột có thống kê giống nhau, cho thấy chúng có khả năng là dữ liệu bị lặp.

### Các cột thời gian

Dataset chỉ chứa dữ liệu trong:

* Tháng 11 năm 2018.
* Tháng 12 năm 2018.

Do dữ liệu nguồn là dữ liệu cũ, việc kiểm tra `freshness` không nên so sánh trực tiếp với ngày hiện tại.

Trong dự án này, `freshness lag` được tính theo công thức:

```text
freshness_lag = ingested_at - requested_at
```

---

# 3. Mục tiêu chuyển đổi dữ liệu

Dataset nguồn là một bảng phẳng gồm 57 cột.

Dự án chuyển đổi dataset này thành mô hình dữ liệu quan hệ gồm bốn bảng:

```text
customers
    customer_id
          │
          ├─────────────────────┐
          ▼                     ▼
dich_vu_xe_trips            payments
    customer_id                 customer_id
    driver_id                   trip_id
    trip_id
          ▲
          │
drivers
    driver_id
```

Mục tiêu của việc tách bảng:

* Giảm dữ liệu lặp.
* Phân tách rõ từng đối tượng nghiệp vụ.
* Tạo quan hệ khóa chính và khóa ngoại.
* Mô phỏng hệ thống cơ sở dữ liệu thực tế.
* Hỗ trợ kiểm tra tính toàn vẹn dữ liệu.
* Hỗ trợ chạy các quy tắc Data Quality độc lập trên từng bảng.

---

# 4. Cấu trúc thư mục đầu ra

Sau khi chạy notebook trên Google Colab, hệ thống tạo thư mục:

```text
ride_database_dataset/
├── dich_vu_xe_trips.csv
├── drivers.csv
├── customers.csv
├── payments.csv
├── dirty_issue_log.csv
├── data_quality_report.csv
├── 01_raw_schema.sql
├── 02_data_quality_checks.sql
├── 03_constraints_after_cleaning.sql
└── 04_load_csv_postgresql.sql
```

---

# 5. Mô tả các bảng dữ liệu

## 5.1. Bảng `dich_vu_xe_trips`

### Mục đích

Lưu thông tin chính của từng chuyến xe hoặc từng yêu cầu báo giá dịch vụ xe.

### Các cột chính

| Cột                    | Kiểu dữ liệu đề xuất | Mô tả                                 |
| ----------------------- | -------------------------- | --------------------------------------- |
| `trip_id`             | VARCHAR                    | Khóa chính của chuyến xe            |
| `source_record_id`    | VARCHAR                    | ID gốc từ dataset nguồn              |
| `customer_id`         | VARCHAR                    | Khóa ngoại tham chiếu khách hàng   |
| `driver_id`           | VARCHAR                    | Khóa ngoại tham chiếu tài xế       |
| `cab_type`            | VARCHAR                    | Hãng xe, ví dụ Uber hoặc Lyft       |
| `product_id`          | VARCHAR                    | Mã sản phẩm hoặc loại dịch vụ    |
| `service_name`        | VARCHAR                    | Tên dịch vụ như UberX, UberXL, Lyft |
| `pickup_location`     | VARCHAR                    | Điểm đón                            |
| `dropoff_location`    | VARCHAR                    | Điểm đến                            |
| `requested_at`        | TEXT hoặc TIMESTAMP       | Thời điểm đặt xe                   |
| `timezone`            | VARCHAR                    | Múi giờ                               |
| `distance_miles`      | NUMERIC                    | Khoảng cách chuyến đi               |
| `surge_multiplier`    | NUMERIC                    | Hệ số tăng giá                      |
| `fare_amount`         | NUMERIC                    | Giá cước                             |
| `trip_status`         | VARCHAR                    | Trạng thái chuyến xe                 |
| `pickup_latitude`     | NUMERIC                    | Vĩ độ điểm đón                   |
| `pickup_longitude`    | NUMERIC                    | Kinh độ điểm đón                  |
| `temperature_f`       | NUMERIC                    | Nhiệt độ tại thời điểm đặt xe  |
| `weather_summary`     | VARCHAR                    | Mô tả thời tiết                     |
| `weather_icon`        | VARCHAR                    | Mã biểu tượng thời tiết           |
| `ingested_at`         | TEXT hoặc TIMESTAMP       | Thời điểm dữ liệu được nạp     |
| `freshness_lag_hours` | NUMERIC                    | Độ trễ dữ liệu tính theo giờ     |

### Vai trò trong Data Quality

Bảng này được sử dụng để kiểm tra:

* `trip_id` bị NULL.
* `trip_id` bị trùng.
* `customer_id` không tồn tại.
* `driver_id` không tồn tại.
* `fare_amount` nhỏ hơn 0.
* `fare_amount` bị thiếu.
* `requested_at` sai định dạng.
* `distance_miles` không hợp lệ.
* `surge_multiplier` không hợp lệ.
* `freshness_lag_hours` lớn hơn ngưỡng cho phép.

---

## 5.2. Bảng `drivers`

### Mục đích

Lưu thông tin tài xế và phương tiện đang hoạt động trong hệ thống.

Dữ liệu tài xế không có trong dataset gốc nên được sinh mô phỏng.

### Các cột chính

| Cột               | Kiểu dữ liệu đề xuất | Mô tả                            |
| ------------------ | -------------------------- | ---------------------------------- |
| `driver_id`      | VARCHAR                    | Khóa chính tài xế              |
| `full_name`      | VARCHAR                    | Họ tên tài xế                  |
| `phone`          | VARCHAR                    | Số điện thoại                  |
| `license_number` | VARCHAR                    | Mã giấy phép lái xe            |
| `vehicle_plate`  | VARCHAR                    | Biển số xe                       |
| `cab_type`       | VARCHAR                    | Hãng xe mà tài xế phục vụ    |
| `rating`         | NUMERIC                    | Điểm đánh giá tài xế        |
| `driver_status`  | VARCHAR                    | Trạng thái tài xế              |
| `joined_at`      | TEXT hoặc TIMESTAMP       | Thời điểm tham gia hệ thống   |
| `updated_at`     | TEXT hoặc TIMESTAMP       | Thời điểm cập nhật gần nhất |

### Giá trị trạng thái đề xuất

```text
ACTIVE
OFFLINE
SUSPENDED
```

### Vai trò trong Data Quality

Bảng này được sử dụng để kiểm tra:

* `driver_id` bị NULL.
* `driver_id` bị trùng.
* Số điện thoại sai định dạng.
* Mã giấy phép sai định dạng.
* Biển số xe sai định dạng.
* `rating` nhỏ hơn 0 hoặc lớn hơn 5.
* `driver_status` ngoài danh sách cho phép.
* `updated_at` nhỏ hơn `joined_at`.
* Tài xế không tồn tại nhưng được tham chiếu bởi bảng chuyến xe.

---

## 5.3. Bảng `customers`

### Mục đích

Lưu thông tin khách hàng sử dụng dịch vụ đặt xe.

Dữ liệu khách hàng không có trong dataset nguồn nên được sinh mô phỏng.

### Các cột chính

| Cột                | Kiểu dữ liệu đề xuất | Mô tả                     |
| ------------------- | -------------------------- | --------------------------- |
| `customer_id`     | VARCHAR                    | Khóa chính khách hàng   |
| `full_name`       | VARCHAR                    | Họ tên khách hàng       |
| `email`           | VARCHAR                    | Địa chỉ email            |
| `phone`           | VARCHAR                    | Số điện thoại           |
| `city`            | VARCHAR                    | Thành phố                 |
| `customer_status` | VARCHAR                    | Trạng thái tài khoản    |
| `created_at`      | TEXT hoặc TIMESTAMP       | Ngày tạo tài khoản      |
| `updated_at`      | TEXT hoặc TIMESTAMP       | Ngày cập nhật gần nhất |

### Giá trị trạng thái đề xuất

```text
ACTIVE
INACTIVE
SUSPENDED
```

### Vai trò trong Data Quality

Bảng này được sử dụng để kiểm tra:

* `customer_id` bị NULL.
* `customer_id` bị trùng.
* Email sai định dạng.
* Số điện thoại sai định dạng.
* Trạng thái khách hàng không hợp lệ.
* `updated_at` nhỏ hơn `created_at`.
* Khách hàng không tồn tại nhưng được sử dụng trong bảng chuyến xe.
* Khách hàng không tồn tại nhưng được sử dụng trong bảng thanh toán.

---

## 5.4. Bảng `payments`

### Mục đích

Lưu thông tin thanh toán của từng chuyến xe.

Dữ liệu thanh toán được mô phỏng dựa trên giá cước và trạng thái chuyến xe.

### Các cột chính

| Cột                | Kiểu dữ liệu đề xuất | Mô tả                               |
| ------------------- | -------------------------- | ------------------------------------- |
| `payment_id`      | VARCHAR                    | Khóa chính giao dịch               |
| `trip_id`         | VARCHAR                    | Khóa ngoại tham chiếu chuyến xe   |
| `customer_id`     | VARCHAR                    | Khóa ngoại tham chiếu khách hàng |
| `amount`          | NUMERIC                    | Số tiền thanh toán                 |
| `currency`        | VARCHAR                    | Đơn vị tiền tệ                   |
| `payment_method`  | VARCHAR                    | Phương thức thanh toán            |
| `payment_status`  | VARCHAR                    | Trạng thái giao dịch               |
| `paid_at`         | TEXT hoặc TIMESTAMP       | Thời điểm thanh toán              |
| `transaction_ref` | VARCHAR                    | Mã tham chiếu giao dịch            |
| `created_at`      | TEXT hoặc TIMESTAMP       | Thời điểm tạo giao dịch          |
| `updated_at`      | TEXT hoặc TIMESTAMP       | Thời điểm cập nhật giao dịch    |

### Phương thức thanh toán

```text
CARD
CASH
WALLET
BANK_TRANSFER
```

### Trạng thái thanh toán

```text
PAID
PENDING
FAILED
REFUNDED
CANCELLED
```

### Vai trò trong Data Quality

Bảng này được sử dụng để kiểm tra:

* `payment_id` bị NULL.
* `payment_id` bị trùng.
* `trip_id` không tồn tại.
* `customer_id` không tồn tại.
* `amount` nhỏ hơn 0.
* Mã giao dịch sai định dạng.
* Trạng thái `PAID` nhưng `paid_at` bị NULL.
* Trạng thái `PENDING` nhưng có `paid_at`.
* Trạng thái `CANCELLED` nhưng số tiền lớn hơn 0.
* Giá trị thanh toán không khớp với giá cước chuyến xe.
* Đồng tiền không nằm trong danh sách cho phép.

---

# 6. Các file hỗ trợ Data Quality

## 6.1. File `dirty_issue_log.csv`

### Mục đích

Ghi lại toàn bộ lỗi đã được cố ý chèn vào dữ liệu.

File này đóng vai trò là `ground truth` để đánh giá hệ thống phát hiện lỗi.

### Cấu trúc

| Cột            | Mô tả                         |
| --------------- | ------------------------------- |
| `table_name`  | Tên bảng bị chèn lỗi       |
| `row_index`   | Vị trí dòng bị thay đổi   |
| `column_name` | Tên cột bị làm bẩn         |
| `issue_type`  | Loại lỗi                      |
| `old_value`   | Giá trị trước khi làm bẩn |
| `new_value`   | Giá trị sau khi làm bẩn     |

### Ví dụ

| table_name           | column_name         | issue_type                     |
| -------------------- | ------------------- | ------------------------------ |
| `dich_vu_xe_trips` | `fare_amount`     | `negative_fare`              |
| `customers`        | `email`           | `invalid_email_format`       |
| `drivers`          | `driver_id`       | `null_primary_key`           |
| `payments`         | `transaction_ref` | `invalid_transaction_format` |

---

## 6.2. File `data_quality_report.csv`

### Mục đích

Lưu kết quả tổng hợp sau khi chạy các quy tắc kiểm tra chất lượng dữ liệu.

### Cấu trúc

| Cột            | Mô tả                      |
| --------------- | ---------------------------- |
| `table_name`  | Tên bảng được kiểm tra |
| `rule`        | Quy tắc Data Quality        |
| `severity`    | Mức độ nghiêm trọng     |
| `failed_rows` | Số dòng vi phạm           |

### Các mức độ lỗi

| Severity    | Ý nghĩa                                          |
| ----------- | -------------------------------------------------- |
| `ERROR`   | Lỗi nghiêm trọng cần xử lý                   |
| `WARNING` | Dữ liệu đáng nghi hoặc quá hạn              |
| `INFO`    | Thông tin cần theo dõi nhưng có thể hợp lệ |

---

# 7. Các lỗi bẩn được cố ý chèn

## 7.1. NULL khóa chính

Một số dòng trong bốn bảng được đặt khóa chính thành NULL:

```text
dich_vu_xe_trips.trip_id
drivers.driver_id
customers.customer_id
payments.payment_id
```

Mục đích:

* Kiểm tra quy tắc `NOT NULL`.
* Kiểm tra tính hợp lệ của khóa chính.
* Kiểm tra ảnh hưởng đến các khóa ngoại liên quan.

---

## 7.2. Giá cước âm

Một số giá trị được chuyển thành số âm:

```text
dich_vu_xe_trips.fare_amount < 0
payments.amount < 0
```

Mục đích:

* Kiểm tra quy tắc miền giá trị.
* Kiểm tra tính nhất quán giữa chuyến xe và thanh toán.
* Kiểm tra constraint `CHECK amount >= 0`.

---

## 7.3. Sai định dạng

Các lỗi định dạng bao gồm:

* Email không có dấu `@`.
* Email có nhiều dấu `@`.
* Số điện thoại chứa chữ.
* Số điện thoại quá ngắn.
* Giấy phép lái xe sai mẫu.
* Mã giao dịch chứa khoảng trắng hoặc ký tự đặc biệt.
* Thời gian như `not_a_datetime`.
* Thời gian như `2026-99-99`.

Mục đích:

* Kiểm tra Regex validation.
* Kiểm tra parser thời gian.
* Kiểm tra schema validation.

---

## 7.4. Freshness lag

Freshness được tính bằng:

```text
freshness_lag_hours =
    ingested_at - requested_at
```

Một số bản ghi được cố ý đặt độ trễ từ 72 đến 240 giờ.

Quy tắc kiểm tra:

```text
freshness_lag_hours > 24
```

Các bản ghi vi phạm được đánh dấu là `WARNING`.

---

## 7.5. Orphan foreign key

Khi một số khóa chính trong bảng `drivers` hoặc `customers` bị đặt thành NULL, các bản ghi chuyến xe đang tham chiếu đến mã đó có thể trở thành khóa ngoại không tồn tại.

Ví dụ:

```text
dich_vu_xe_trips.driver_id
```

có giá trị nhưng không tìm thấy:

```text
drivers.driver_id
```

Mục đích:

* Kiểm tra tính toàn vẹn tham chiếu.
* Kiểm tra các phép `LEFT JOIN`.
* Kiểm tra constraint `FOREIGN KEY`.

---

# 8. Tỷ lệ lỗi mặc định

Các tỷ lệ có thể được điều chỉnh trong biến:

```python
DIRTY_RATES
```

Cấu hình mặc định:

| Loại lỗi                         | Tỷ lệ |
| ---------------------------------- | ------: |
| NULL khóa chính chuyến xe       |   0,05% |
| NULL khóa chính tài xế         |   0,20% |
| NULL khóa chính khách hàng     |   0,10% |
| NULL khóa chính thanh toán      |   0,05% |
| Giá cước âm                    |   0,30% |
| Thời gian chuyến xe sai format   |   0,20% |
| Email khách hàng sai format      |   1,00% |
| Số điện thoại khách hàng sai |   1,00% |
| Số điện thoại tài xế sai     |   1,00% |
| Giấy phép tài xế sai           |   1,00% |
| Mã giao dịch sai                 |   0,30% |
| Freshness lag                      |   1,00% |

Ví dụ tăng tỷ lệ cước âm lên 1%:

```python
DIRTY_RATES["negative_fare"] = 0.01
```

---

# 9. Cách chạy trên Google Colab

## 9.1. Bước 1: Upload dataset

```python
from google.colab import files

uploaded = files.upload()

file_path = next(iter(uploaded))

print("Dataset đã upload:", file_path)
```

---

## 9.2. Bước 2: Đọc dataset

```python
import pandas as pd

df = pd.read_csv(
    file_path,
    low_memory=False
)

print("Đọc dataset thành công.")
print("Kích thước:", df.shape)
```

Kết quả dự kiến:

```text
Đọc dataset thành công.
Kích thước dataset: (693071, 57)
```

---

## 9.3. Bước 3: Chạy code sinh dữ liệu

Chạy cell chứa code:

* Tạo bảng `customers`.
* Tạo bảng `drivers`.
* Tạo bảng `dich_vu_xe_trips`.
* Tạo bảng `payments`.
* Chèn lỗi bẩn.
* Kiểm tra chất lượng.
* Xuất file CSV.
* Sinh các file SQL.
* Nén kết quả thành ZIP.

---

## 9.4. Bước 4: Tải kết quả

Sau khi chạy xong, Colab tạo:

```text
/content/ride_database_dataset.zip
```

File được tải bằng:

```python
from google.colab import files

files.download(
    "/content/ride_database_dataset.zip"
)
```

---

# 10. Mô tả các file SQL

## 10.1. `01_raw_schema.sql`

### Nhiệm vụ

Tạo cấu trúc bốn bảng raw trong PostgreSQL.

Các bảng raw chưa có:

* `PRIMARY KEY`.
* `FOREIGN KEY`.
* `NOT NULL`.
* `CHECK CONSTRAINT`.

Mục đích là cho phép import dữ liệu bẩn vào cơ sở dữ liệu để kiểm tra.

Các cột thời gian được khai báo dưới dạng `TEXT`, vì dataset có chứa thời gian sai format.

---

## 10.2. `02_data_quality_checks.sql`

### Nhiệm vụ

Chứa các truy vấn SQL dùng để tìm lỗi dữ liệu.

Các nhóm kiểm tra:

* NULL khóa chính.
* Khóa chính trùng.
* Giá cước âm.
* Số tiền thanh toán âm.
* Email sai định dạng.
* Số điện thoại sai định dạng.
* Giấy phép sai định dạng.
* Mã giao dịch sai định dạng.
* Freshness lag.
* Khóa ngoại không tồn tại.

Ví dụ:

```sql
SELECT *
FROM dich_vu_xe_trips
WHERE fare_amount < 0;
```

---

## 10.3. `03_constraints_after_cleaning.sql`

### Nhiệm vụ

Thêm các ràng buộc dữ liệu sau khi dữ liệu đã được làm sạch.

Các ràng buộc gồm:

* `NOT NULL`.
* `PRIMARY KEY`.
* `FOREIGN KEY`.
* `CHECK fare_amount >= 0`.
* `CHECK amount >= 0`.

Không nên chạy file này khi dữ liệu vẫn còn lỗi.

Nếu chạy trên dữ liệu bẩn, PostgreSQL có thể trả về lỗi do:

* Khóa chính bị NULL.
* Khóa ngoại không tồn tại.
* Giá trị âm.
* Khóa chính bị trùng.

---

## 10.4. `04_load_csv_postgresql.sql`

### Nhiệm vụ

Nạp bốn file CSV vào PostgreSQL bằng lệnh `\copy`.

Thứ tự import đề xuất:

```text
customers
drivers
dich_vu_xe_trips
payments
```

Các bảng cha được import trước các bảng chứa khóa ngoại.

---

# 11. Luồng xử lý dữ liệu

```text
rideshare_kaggle.csv
        │
        ▼
Đọc và phân tích dữ liệu nguồn
        │
        ▼
Chọn các cột cần thiết
        │
        ▼
Sinh customers và drivers
        │
        ▼
Tạo dich_vu_xe_trips
        │
        ▼
Tạo payments
        │
        ▼
Chèn lỗi bẩn có kiểm soát
        │
        ▼
Xuất dirty_issue_log.csv
        │
        ▼
Chạy quy tắc Data Quality
        │
        ▼
Xuất data_quality_report.csv
        │
        ▼
Xuất 4 file CSV và các file SQL
```

---

# 12. Luồng sử dụng với PostgreSQL

```text
01_raw_schema.sql
        │
        ▼
Tạo bảng raw
        │
        ▼
04_load_csv_postgresql.sql
        │
        ▼
Import dữ liệu CSV
        │
        ▼
02_data_quality_checks.sql
        │
        ▼
Phát hiện lỗi dữ liệu
        │
        ▼
Đối chiếu với dirty_issue_log.csv
        │
        ▼
Làm sạch dữ liệu
        │
        ▼
03_constraints_after_cleaning.sql
        │
        ▼
Áp dụng khóa chính, khóa ngoại và constraints
```

---

# 13. Một số quy tắc Data Quality quan trọng

## 13.1. Completeness

Kiểm tra dữ liệu bắt buộc không bị thiếu:

```text
trip_id IS NOT NULL
driver_id IS NOT NULL
customer_id IS NOT NULL
payment_id IS NOT NULL
```

---

## 13.2. Uniqueness

Kiểm tra khóa chính không bị trùng:

```text
COUNT(primary_key) = COUNT(DISTINCT primary_key)
```

---

## 13.3. Validity

Kiểm tra dữ liệu đúng định dạng:

```text
email đúng Regex
phone đúng Regex
license_number đúng Regex
transaction_ref đúng Regex
datetime có thể parse
```

---

## 13.4. Accuracy

Kiểm tra dữ liệu hợp lý:

```text
fare_amount >= 0
payment.amount >= 0
rating BETWEEN 0 AND 5
distance_miles >= 0
```

---

## 13.5. Consistency

Kiểm tra tính nhất quán giữa các bảng:

```text
payments.amount = dich_vu_xe_trips.fare_amount
payments.customer_id = dich_vu_xe_trips.customer_id
```

---

## 13.6. Referential Integrity

Kiểm tra khóa ngoại tồn tại:

```text
trips.customer_id tồn tại trong customers
trips.driver_id tồn tại trong drivers
payments.trip_id tồn tại trong trips
payments.customer_id tồn tại trong customers
```

---

## 13.7. Timeliness

Kiểm tra độ trễ dữ liệu:

```text
freshness_lag_hours <= 24
```

---

# 14. Ví dụ truy vấn kiểm tra

## Kiểm tra khóa chính bị NULL

```sql
SELECT COUNT(*) AS failed_rows
FROM dich_vu_xe_trips
WHERE trip_id IS NULL
   OR TRIM(trip_id) = '';
```

## Kiểm tra giá cước âm

```sql
SELECT *
FROM dich_vu_xe_trips
WHERE fare_amount < 0;
```

## Kiểm tra email sai định dạng

```sql
SELECT *
FROM customers
WHERE email IS NULL
   OR email !~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$';
```

## Kiểm tra tài xế không tồn tại

```sql
SELECT t.*
FROM dich_vu_xe_trips AS t
LEFT JOIN drivers AS d
    ON t.driver_id = d.driver_id
WHERE d.driver_id IS NULL;
```

## Kiểm tra chuyến xe cập nhật trễ

```sql
SELECT *
FROM dich_vu_xe_trips
WHERE freshness_lag_hours > 24;
```

## Kiểm tra số tiền thanh toán không khớp giá cước

```sql
SELECT
    p.payment_id,
    p.trip_id,
    p.amount,
    t.fare_amount
FROM payments AS p
JOIN dich_vu_xe_trips AS t
    ON p.trip_id = t.trip_id
WHERE p.amount <> t.fare_amount;
```

---

# 15. Kết quả mong đợi

Sau khi chạy thành công, hệ thống phải:

* Tạo đủ bốn bảng dữ liệu.
* Giữ được quan hệ giữa khách hàng, tài xế, chuyến xe và thanh toán.
* Chèn được các lỗi bẩn theo tỷ lệ cấu hình.
* Ghi lại chính xác các lỗi đã chèn.
* Phát hiện được lỗi bằng Python.
* Có thể import dữ liệu vào PostgreSQL.
* Có thể phát hiện lỗi bằng SQL.
* Không thể áp dụng các constraints khi dữ liệu chưa được làm sạch.
* Có thể áp dụng constraints sau khi dữ liệu đã được sửa.

---

# 16. Hạn chế của bộ dữ liệu mô phỏng

Dataset nguồn chủ yếu là dữ liệu báo giá dịch vụ Uber và Lyft, không phải dữ liệu vận hành hoàn chỉnh của các chuyến xe thực tế.

Do đó:

* Khách hàng được sinh mô phỏng.
* Tài xế được sinh mô phỏng.
* Thanh toán được sinh mô phỏng.
* Trạng thái chuyến xe được sinh ngẫu nhiên.
* Quan hệ giữa khách hàng và tài xế không phải dữ liệu thực.
* Thời gian thanh toán được tính dựa trên thời gian báo giá.
* `fare_amount` thiếu trong dữ liệu nguồn vẫn được giữ lại để phục vụ kiểm thử.

Bộ dữ liệu chỉ nên được sử dụng cho:

* Học tập.
* Nghiên cứu.
* Demo.
* Kiểm thử Data Quality.
* Kiểm thử ETL.
* Kiểm thử AI Agent.

Không nên sử dụng bộ dữ liệu này để đưa ra kết luận thực tế về hành vi của khách hàng, tài xế hoặc hệ thống Uber và Lyft.

---

# 17. Kết luận

Dự án đã chuyển đổi một dataset phẳng gồm 693.071 bản ghi thành mô hình dữ liệu quan hệ gồm bốn bảng nghiệp vụ:

```text
dich_vu_xe_trips
drivers
customers
payments
```

Bộ dữ liệu chứa cả dữ liệu tương đối hợp lệ và các lỗi được chèn có chủ đích.

Điều này giúp tạo ra môi trường phù hợp để xây dựng và đánh giá:

* Hệ thống kiểm tra chất lượng dữ liệu.
* Data Quality Agent.
* SQL rule engine.
* ETL pipeline.
* Dashboard chất lượng dữ liệu.
* dbt tests.
* Great Expectations tests.
* AI Agent đề xuất quy tắc và phát hiện bất thường.
