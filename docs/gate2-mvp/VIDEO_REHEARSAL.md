# Gate 2 MVP — Three-Minute Video Demo Rehearsal Script

> **Dự án:** RidePulse DQ (Autonomous Data Quality & Anomaly Intelligence Platform)  
> **Thời lượng video:** Đúng 3 phút (180 giây)  
> **Môi trường Trình chiếu:** Public Production Deployment (Vercel Frontend & GCP Cloud Run API)  
> **Quy tắc an toàn bảo mật:** **KHÔNG** hiển thị `localhost`, `API Keys / Secrets`, dòng dữ liệu thô (raw rows), `database connection strings` hay `stack traces`.

---

## ⏱️ KỊCH BẢN CHI TIẾT THEO 6 MỐC THỜI GIAN (0:00 – 3:00)

### Mốc 1: 0:00 – 0:20 | Giới thiệu Public URL & Architecture Diagram
- **Hình ảnh trình chiếu:** Trình duyệt truy cập domain công khai Vercel (`https://ridepulse-dq.vercel.app`), chuyển nhanh sang tab Sơ đồ Kiến trúc (`ARCHITECTURE.md`).
- **Lời thoại (Voiceover):**  
  > "Xin chào các bạn và Hội đồng giám khảo! Đây là sản phẩm RidePulse DQ — Nền tảng tự động hóa kiểm định chất lượng dữ liệu và phát hiện bất thường bằng Multi-Agent. Ứng dụng hiện đang chạy live công khai trên Vercel Frontend kết nối với Cloud Run API Service. Kiến trúc hệ thống phân tách nghiêm ngặt giữa Vercel UI, FastAPI Service, Supabase PostgreSQL và Cloud Run Job xử lý dbt Core và AI Agent."

---

### Mốc 2: 0:20 – 0:45 | Chọn Dataset & Tiến độ Cloud Run Job Bất Đồng Bộ
- **Hình ảnh trình chiếu:** Đăng nhập mật khẩu Steward demo, truy cập danh sách dataset, chọn `NYC Yellow Taxi 50k Semantic Dataset`, bấm nút kích hoạt `Ingest & Profile`. Màn hình hiển thị trạng thái `PENDING` $\rightarrow$ `RUNNING` với thanh tiến trình polling.
- **Lời thoại (Voiceover):**  
  > "Steward thực hiện đăng nhập và chọn tập dữ liệu 50.000 bản ghi taxi NYC. Ngay khi bấm kích hoạt, API trả về HTTP 202 Accepted và khởi tạo một Cloud Run Job chạy bất đồng bộ trong background. Trình duyệt liên tục polling trạng thái job mà không làm treo giao diện người dùng."

---

### Mốc 3: 0:45 – 1:20 | Aggregate Profile & AI Guarded Proposal (LangGraph Agent)
- **Hình ảnh trình chiếu:** Màn hình Aggregate Profile hiển thị các chỉ số thống kê nén (`null_count`, `min_value`, `max_value`, `distinct_count`). Bấm nút `Generate AI Proposals`, màn hình hiển thị danh sách 5 quy tắc do OpenAI GPT-4o-mini đề xuất dạng Pydantic Structured Output.
- **Lời thoại (Voiceover):**  
  > "Sau khi dbt build xong, hệ thống trích xuất Aggregate Profile — tóm tắt thống kê nén của tập dữ liệu. Guarded AI Agent nhận thông tin nén này — hoàn toàn không nhận dữ liệu thô hay thông tin nhạy cảm — và tự động đề xuất 5 quy tắc chất lượng dữ liệu chuẩn hóa như cước phí âm, thiếu mã nhà cung cấp, và trùng lặp bản ghi."

---

### Mốc 4: 1:20 – 1:55 | Màn hình HITL Rule Review (Approve/Edit/Reject) & Audit Log
- **Hình ảnh trình chiếu:** Steward thao tác trên Bảng kiểm duyệt HITL (Human-in-the-Loop): Bấm `APPROVE` quy tắc cước âm, bấm `EDIT` chỉnh tham số quy tắc loại thanh toán, bấm `REJECT` quy tắc không phù hợp. Mở tab Audit Logs hiển thị ngay các sự kiện kiểm duyệt được ghi lại.
- **Lời thoại (Voiceover):**  
  > "Với triết lý Human-in-the-Loop, AI chỉ giữ vai trò đề xuất. Data Steward trực tiếp kiểm duyệt trên UI: chấp nhận, chỉnh sửa tham số hoặc từ chối rule. Mọi hành động của Steward đều được ghi nhật ký audit log bất biến để phục vụ truy xuất trách nhiệm."

---

### Mốc 5: 1:55 – 2:30 | Thực thi DQ Run, Báo cáo Số lượng & Bảng Kết quả (Capped 20 IDs)
- **Hình ảnh trình chiếu:** Steward bấm `Execute DQ Run`. Hệ thống biên dịch SQL parameterized có bind variables, thực thi qua Read-Only Runner (`RUNNER_DATABASE_URL`). Kết quả hiển thị tổng số rules pass/fail và bảng danh sách 20 IDs vi phạm tiêu biểu.
- **Lời thoại (Voiceover):**  
  > "Khi bấm chạy DQ Run, các rule đã duyệt được SQL Compiler biên dịch thành duy nhất lệnh SELECT parameterized an toàn, thực thi qua role Read-Only của PostgreSQL. Kết quả ghi nhận chính xác 250 bản ghi vi phạm mỗi loại lỗi và hiển thị danh sách tối đa 20 IDs bị phạt để bảo vệ hiệu năng trình duyệt."

---

### Mốc 6: 2:30 – 3:00 | Bằng chứng dbt Core, 5 Kịch bản E1–E5 & Giới hạn v1
- **Hình ảnh trình chiếu:** Mở tab dbt Evidence hiển thị kết quả `dbt build` trên schema `analytics` (`stg_trips`, `profile_input`), bảng đối chiếu 5 kịch bản E1–E5, và tóm tắt giới hạn phiên bản MVP Gate 2.
- **Lời thoại (Voiceover):**  
  > "Toàn bộ lớp dữ liệu đầu vào đều được dbt Core biến đổi và kiểm thử data contract tự động. Hệ thống đã vượt qua 100% bằng chứng kiểm thử trên 5 kịch bản thực tế E1 đến E5. Cảm ơn Hội đồng giám khảo đã theo dõi!"

---

## 🛡️ CHECKLIST AN TOÀN BẢO MẬT KHI QUAY VIDEO

- [x] Không xuất hiện `localhost` hay IP nội bộ trong thanh địa chỉ trình duyệt (Chỉ dùng Public Vercel URL).
- [x] Không hiển thị file `.env`, `OPENAI_API_KEY`, `DATABASE_URL` hay Secret tokens.
- [x] Không quay các dòng dữ liệu thô (raw rows) hay thông tin cá nhân.
- [x] Không để lộ thông tin kết nối Database PostgreSQL hay log lỗi internal stack trace.
