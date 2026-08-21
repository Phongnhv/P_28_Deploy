# Hướng dẫn Chạy Đồ thị Run 1 (Proposal Graph) trên Dữ liệu Taxi

Tài liệu này tổng hợp toàn bộ các câu lệnh cần thiết để chạy thử nghiệm **Run 1: Proposal Graph** trên dữ liệu thực tế taxi NYC của bạn (`yellow_tripdata_2025_semantic_50k.parquet`).

---

## Cách 1: Chạy trực tiếp bằng Script Python (Khuyên dùng - Nhanh nhất)
Cách này chạy hoàn toàn cục bộ bằng script offline, **không cần** đăng nhập session, gọi API, hay cấu hình Docker mạng phức tạp. 

Mở terminal PowerShell của bạn và chạy lệnh sau:

```powershell
# Chạy script offline tự động đọc file Parquet và sinh Rules
.venv\Scripts\python.exe scripts/run_taxi_proposal_test.py
```
*(Nếu là cmd hoặc git bash)*:
```bash
.venv/bin/python scripts/run_taxi_proposal_test.py
```

*   **Logic chạy ngầm**: Script tự động tải tệp parquet taxi của bạn, nạp 1,000 dòng mẫu vào SQLite cục bộ, chạy qua toàn bộ Proposal Graph LangGraph (tự động bypass qua cổng duyệt HITL) và in trực tiếp kết quả Rules sinh ra trên màn hình.

---

## Cách 2: Chạy đầy đủ thông qua Docker Compose (API + Worker)
Cách này chạy đúng theo luồng tích hợp hệ thống thực tế trên giao diện Web UI.

### Bước 1: Khởi động hệ thống Docker
```powershell
# Bật các container trong nền (DB, MinIO, API, Worker)
docker compose up -d
```

### Bước 2: Nạp dữ liệu Parquet vào Database
```powershell
# Gửi tác vụ INGEST_PROFILE để worker nạp tệp parquet taxi vào Postgres
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/v1/jobs" -Headers @{"Content-Type"="application/json"; "Idempotency-Key"="taxi-ingest-1"} -Body '{"type": "INGEST_PROFILE", "linked_entity": "dataset-nyc-yellow-taxi-50k"}'
```

### Bước 3: Xem log của Worker (Mở terminal riêng)
```powershell
# Theo dõi tiến trình chạy đồ thị LangGraph thời gian thực
docker compose logs -f worker
```

### Bước 4: Đăng nhập lấy Session và CSRF Token
Do các API sinh luật yêu cầu bảo mật kiểm tra quyền Steward, bạn cần đăng nhập trước:
```powershell
# Đăng nhập bằng tài khoản steward mặc định
$loginRes = Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/v1/session" -Headers @{"Content-Type"="application/json"} -Body '{"username": "steward", "password": "steward"}' -SessionVariable mySession
```

### Bước 5: Yêu cầu Đề xuất Rules (Tạm dừng ở chốt chặn 1)
```powershell
# Gọi API chạy Proposal Graph (sử dụng session & csrf token vừa lấy)
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/v1/datasets/dataset-nyc-yellow-taxi-50k/rule-proposals" -WebSession $mySession -Headers @{"X-CSRF-Token" = $loginRes.csrf_token}
```
*   *Lưu ý*: Quan sát log ở Bước 3, bạn sẽ thấy đồ thị chạy đến node `hitl_semantic_gate` và **tạm dừng** (`WAITING_FOR_SEMANTIC_REVIEW`).

### Bước 6: Phê duyệt Semantic Contract (Đánh thức đồ thị sinh rules)
```powershell
# Steward duyệt xác nhận contract nháp
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/v1/datasets/dataset-nyc-yellow-taxi-50k/semantic-contract/confirm" -WebSession $mySession -Headers @{"X-CSRF-Token" = $loginRes.csrf_token} -Body '{}'
```
*   *Lưu ý*: Đồ thị sẽ thức dậy và chạy nốt các node còn lại (viết lại prompt nghiệp vụ taxi và sinh rules).

### Bước 7: Xem kết quả Rules đề xuất
```powershell
# Lấy danh sách rules đã sinh ra lưu trong DB Postgres
Invoke-RestMethod -Method Get -Uri "http://localhost:8000/api/v1/rule-proposals?dataset_id=dataset-nyc-yellow-taxi-50k" -WebSession $mySession
```
