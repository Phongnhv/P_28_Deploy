# Tên đề tài
AI Agent xây dựng & kiểm tra Data Quality và phát hiện bất thường	

# Mô tả bài toán
- 📍 Thực trạng: Dữ liệu vận hành Dịch vụ gọi xe X/Xe X có null, sai định dạng, giá trị ngoại lai nhưng đội data phải viết tay hàng trăm test dbt.

- 🎯 Vấn đề: Xây agent tự khảo sát dataset, đề xuất bộ rule chất lượng (uniqueness, not-null, range, format, freshness), sinh test tự động, chạy định kỳ, phát hiện anomaly bằng thống kê/ML và gửi cảnh báo kèm chẩn đoán nguyên nhân.

- 🔒 Ràng buộc: HITL data steward duyệt rule trước khi áp dụng production; governance chỉ đọc metadata không lộ dữ liệu nhạy cảm; độ chính xác cảnh báo (giảm false positive); hiệu năng chạy check trên bảng lớn theo lịch, giới hạn tài nguyên.	

# Tech stack gợi ý
- Tech stack: LLM sinh rule & diễn giải
- LangGraph agent (profiler → rule proposer → test generator → anomaly detector)
- Great Expectations/dbt tests
- scikit-learn (Isolation Forest/z-score) cho anomaly
- Airflow/Dagster scheduler
- warehouse Snowflake/BigQuery
- vector DB lưu lịch sử rule
- backend FastAPI
- frontend React + Ant Design
- deploy Docker + Cloud Run.	

# Yêu cầu đầu ra + gợi ý
## Cơ bản:
- web deploy, 2 vai trò (Steward/Viewer), agent profiling dataset → đề xuất rule → HITL duyệt → sinh & chạy test → dashboard kết quả + cảnh báo.

## Nâng cao:
- multi-agent kết hợp phát hiện anomaly ML, eval đo precision/recall cảnh báo trên dữ liệu có lỗi gán nhãn, tự đề xuất ngưỡng tối ưu, phân tích xu hướng chất lượng theo thời gian."