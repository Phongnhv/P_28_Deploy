# PR Description - Branch `kien-01033`

## 📝 Tổng quan (Overview)
Pull Request này tập trung vào 3 nâng cấp lớn của hệ thống **Data Quality Agent**:
1. **Tích hợp Object Storage (MinIO)** làm kho lưu trữ tập trung cho các file cấu hình kiểm thử dbt YML sinh ra từ Test Generator Node.
2. **Loại bỏ hoàn toàn các giới hạn cứng về số lượng luật** (`min_length`/`max_length` và giới hạn trong System Prompt) để LLM tự do sinh đầy đủ các rule cần thiết nhằm đảm bảo chất lượng dữ liệu.
3. **Khắc phục các lỗi nghiêm trọng về cơ sở dữ liệu**: lỗi khóa ngoại (`ForeignKeyViolation`) khi dọn dẹp các rule đề xuất cũ và lỗi bulk insert giá trị `NaN` của pandas vào cột số nguyên/thực trong PostgreSQL.

---

## 🛠️ Các thay đổi chính (Key Changes)

### 1. Tích hợp Object Storage (MinIO/S3) cho dbt Artifacts
* **Cấu hình & Docker**:
  * Cập nhật [`docker-compose.yml`](file:///d:/ai_thuc_chien/P-028/docker-compose.yml) để bổ sung service `minio` (môi trường Object Storage cục bộ) chạy trên cổng `9000` (API) và `9001` (Console UI).
  * Bổ sung các biến môi trường Object Storage vào [`.env.example`](file:///d:/ai_thuc_chien/P-028/.env.example) và [`.env.local.example`](file:///d:/ai_thuc_chien/P-028/.env.local.example).
  * Cập nhật cấu hình settings trong [`src/config.py`](file:///d:/ai_thuc_chien/P-028/src/config.py) để tải các cấu hình S3/MinIO.
* **Service mới & Logic nghiệp vụ**:
  * Tạo mới [`src/services/dbt_artifact_store.py`](file:///d:/ai_thuc_chien/P-028/src/services/dbt_artifact_store.py) (`DbtArtifactStore`) sử dụng thư viện `boto3` để thực hiện upload/download các file cấu hình dbt test (`.yml`) lên/xuống bucket được chỉ định.
  * Tích hợp kho lưu trữ này vào [`test_generator_node.py`](file:///d:/ai_thuc_chien/P-028/src/agents/nodes/test_generator_node.py) và [`test_runner_node.py`](file:///d:/ai_thuc_chien/P-028/src/agents/nodes/test_runner_node.py) để tự động đẩy file YML kiểm thử lên MinIO sau khi sinh và tải về chạy kiểm thử.
* **Kiểm thử (Tests)**:
  * Viết unit test [`tests/unit/test_dbt_artifact_store.py`](file:///d:/ai_thuc_chien/P-028/tests/unit/test_dbt_artifact_store.py) giả lập S3 client.
  * Viết integration test [`tests/integration/test_dbt_artifact_minio.py`](file:///d:/ai_thuc_chien/P-028/tests/integration/test_dbt_artifact_minio.py) kết nối trực tiếp với MinIO Container.

### 2. Tối ưu hóa Sinh luật Chất lượng Dữ liệu (Không giới hạn số lượng rules)
* **Schema**: Sửa [`src/models/rule_schemas.py`](file:///d:/ai_thuc_chien/P-028/src/models/rule_schemas.py) loại bỏ ràng buộc `min_length=2` và `max_length=5` trong lớp `TableRuleProposal`.
* **LLM Prompts**: Cập nhật chỉ thị trong [`src/agents/nodes/templates.py`](file:///d:/ai_thuc_chien/P-028/src/agents/nodes/templates.py) xóa bỏ yêu cầu *"chỉ chọn lọc 2 đến 5 rule có bằng chứng mạnh nhất"*, thay vào đó yêu cầu LLM đề xuất đầy đủ tất cả các quy tắc cần thiết bảo vệ chất lượng dữ liệu. (Sinh được **29 rules** so với **5 rules** như trước đây).

### 3. Sửa lỗi Cơ sở dữ liệu (Bug Fixes)
* **Lỗi khóa ngoại (`ForeignKeyViolation`)**:
  * Khi chạy lại Graph, việc xóa các bản ghi cũ trong bảng `rule_proposals` bị lỗi do vẫn còn các tham chiếu ngoại từ `rule_versions` và `rule_configurations`.
  * Khắc phục tại [`src/services/rule_store.py`](file:///d:/ai_thuc_chien/P-028/src/services/rule_store.py) bằng cách xóa dữ liệu ở bảng con phụ thuộc trước khi xóa ở bảng mẹ.
* **Lỗi Bulk Insert `NaN` của Pandas**:
  * Sửa lỗi tại [`src/services/job_runner.py`](file:///d:/ai_thuc_chien/P-028/src/services/job_runner.py) để chuyển đổi các giá trị `nan` (từ pandas) thành `None` trước khi nạp dữ liệu vào cơ sở dữ liệu PostgreSQL.

### 4. Công cụ & Tài liệu (Tooling & Docs)
* Tạo script [`scripts/insert_source_rows_parquet.py`](file:///d:/ai_thuc_chien/P-028/scripts/insert_source_rows_parquet.py) để hỗ trợ import dữ liệu hàng loạt từ file parquet vào database.
* Viết tài liệu hướng dẫn cơ chế phát hiện dị thường dễ hiểu tại [`docs/anomaly_detection_mechanism.md`](file:///d:/ai_thuc_chien/P-028/docs/anomaly_detection_mechanism.md).

---

## 🧪 Kịch bản kiểm thử & Xác minh (Verification Plan)

### Kiểm thử Tác nhân & Luồng Graph (End-to-End)
Chạy lệnh kiểm tra đồ thị tác nhân LangGraph:
```bash
docker compose exec worker python -m src.agents.graph
```
* **Xác nhận kết quả**:
  * Không còn gặp lỗi `ForeignKeyViolation` khi chạy lại.
  * Số lượng rule đề xuất tăng lên vượt trội (từ 5 rule lên **29 rule** cho tập dữ liệu taxi 50k).
  * File dbt test YML được tạo và upload thành công lên MinIO Bucket.
  * dbt chạy CLI kiểm thử trên worker kéo file từ MinIO thành công và trả về báo cáo dị thường chính xác.

### Chạy Unit Test & Integration Test
```bash
pytest tests/unit/test_dbt_artifact_store.py -v
pytest tests/integration/test_dbt_artifact_minio.py -v
```
