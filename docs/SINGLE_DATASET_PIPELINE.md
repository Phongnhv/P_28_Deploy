# Pipeline theo dataset, một nguồn upload

Thay đổi local ngày 2026-09-03 theo yêu cầu tối giản.

- Người dùng chọn dataset rồi tiếp tục pipeline. Không cần chọn hoặc truyền version để tạo fresh profile hay Graph 1.
- Mỗi file khác nội dung được upload thành dataset riêng, có một source version nội bộ. Upload lại cùng nội dung vẫn dùng cơ chế idempotency hiện có.
- Giữ nguyên bảng version, checksum, lịch sử profile/artifact/ruleset. Không có migration hoặc xóa dữ liệu lịch sử.
- Với dataset cũ có nhiều version, checksum đã lưu trên dataset xác định nguồn sử dụng. Không tự chọn version mới nhất. Nguồn mơ hồ hoặc yêu cầu không khớp bị từ chối.
- Profiler, proposal tools, API profile, dictionary và row explorer dùng nguồn của dataset đó. Materializer lấy đúng artifact ID.
- Workflow Graph 2 lưu source/profile trên execution. Graph 3 dùng profile upload cho số dòng và giới hạn investigation tools vào dataset/execution hiện tại.
- CLI từ chối execution chưa có rule-review snapshot hợp lệ thay vì chạy đường SQL legacy cho dataset upload. Các review snapshot của Graph1 Studio được giữ nguyên.

## Kiểm thử

Test mới ở tests/unit/test_single_dataset_source.py:

- Chỉ truyền dataset ID qua API vẫn chuẩn bị được A/B/C với 3/2/4 dòng.
- Chạy understanding, proposal mock, publish, quality checks thực trên file và detector cho cả ba dataset.
- Profile legacy 999 dòng không ghi đè evidence upload 2 dòng.
- Thêm historical version không làm tool đổi nguồn.
- Đọc đúng source artifact khi tồn tại artifact cũ trỏ file thiếu.
- Analysis tool và standalone anomaly từ chối dataset/execution khác.

tests/test_versioned_dataset_contract.py kiểm chứng hai file khác nội dung thành hai dataset, mỗi dataset giữ một version và profile riêng. Graph 1 được tạo bằng dataset ID, không cần chọn version/profile.

Kết quả: bộ routing/workflow/API 112 passed; bộ anomaly/proposer tools liên quan 25 passed, tổng 137 test. Frontend build, Ruff và git diff --check đã pass. Không đổi model, timeout hay validator. Chưa kiểm thử Luna thật hoặc browser E2E trong lượt sửa này; kiểm thử routing không phải bằng chứng Luna hết fallback. Không commit, push, deploy hoặc sửa cloud.


Cập nhật 2026-09-03: đã chạy UI E2E với Luna/DeepAgent thật trên Supabase. Bằng chứng, lỗi được tìm thêm và giới hạn nằm trong [REAL_UI_DATASET_E2E_20260903.md](REAL_UI_DATASET_E2E_20260903.md).
