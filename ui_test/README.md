# DataPulse – UI Test & Interactive HTML Prototype

Thư mục này chứa bản **Interactive UI Prototype (Web Application)** trực quan hóa đầy đủ 11 màn hình wireframe theo đúng thiết kế Ant Design Admin Dashboard cho dự án **DataPulse – Autonomous Data Quality & Anomaly Intelligence Platform**.

---

## 📁 Cấu trúc Thư mục UI Test

```
ui_test/
├── index.html     # Web App prototype chính (bao gồm cả 11 màn hình & Modals)
├── styles.css     # Ant Design 5.0 Dark Slate Design System & Utilities
├── app.js         # Logic chuyển màn hình, Role Switcher, HITL Rule Actions & Chart.js
└── README.md      # Hướng dẫn chạy và test UI
```

---

## 🚀 Hướng Dẫn Khởi Chạy & Kiểm Thử UI

### Cách 1: Mở trực tiếp trong Trình duyệt (Đơn giản nhất)
1. Mở file `index.html` trong bất kỳ trình duyệt web nào (Chrome, Edge, Firefox, Safari).
2. Bạn có thể kéo thả file `ui_test/index.html` trực tiếp vào tab trình duyệt.

### Cách 2: Sử dụng VS Code Live Server hoặc Python HTTP Server
Chạy lệnh sau tại thư mục dự án:
```bash
python -m http.server 8000
```
Sau đó truy cập: `http://localhost:8000/ui_test/index.html`

---

## 🎮 Các Tính Năng Có Thể Thao Tác (Interactive Features)

1. **Role Switcher (Screen 1 - Login):**
   - Đăng nhập với vai trò **Data Steward**: Hiển thị đầy đủ các nút Approve/Reject/Edit rules, Run Test, Start Profiling.
   - Đăng nhập với vai trò **Viewer**: Tự động chuyển sang Screen 11 (Executive Dashboard), ẩn toàn bộ các nút thao tác chỉnh sửa hệ thống (Read-Only Mode).

2. **Hành trình Workflow đầy đủ 11 màn hình:**
   - **Screen 1:** Login & Role Selection
   - **Screen 2:** Data Steward Dashboard (Health Score 87.4%, Quick Actions)
   - **Screen 3:** Dataset Catalog (`trips`, `drivers`, `payments`, `customers`)
   - **Screen 4:** Dataset Profiling & Metadata (tỷ lệ Null, Unique, Outlier)
   - **Screen 5:** AI Rule Proposals (Bảng HITL review: Nút Approve, Reject, Edit, Approve All)
   - **Screen 6:** Rule Edit Modal (Chỉnh sửa threshold, severity, description)
   - **Screen 7:** Test Execution & Streaming Console Log (Stepper 4 bước + Log dbt/Great Expectations)
   - **Screen 8:** Anomaly Dashboard (Biểu đồ Time-Series với điểm đỏ bứt phá + Bảng cảnh báo)
   - **Screen 9:** AI Diagnosis Modal (Phân tích Root Cause, Business Impact & Gợi ý xử lý dbt)
   - **Screen 10:** Trend Analysis (Chart Precision, Recall, F1 & Trends 30 ngày)
   - **Screen 11:** Viewer Dashboard (Giao diện Read-only cho cấp quản lý)

---
*Tài liệu kiểm thử giao diện người dùng DataPulse Platform.*
