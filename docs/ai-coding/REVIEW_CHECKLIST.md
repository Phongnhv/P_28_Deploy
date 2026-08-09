# Code Review Checklist

Checklist này dành cho mọi reviewer. Reviewer cần đối chiếu task, diff và kết quả
chạy thử; không chỉ đọc báo cáo của agent.

## Scope và tính đúng

- [ ] Thay đổi đúng objective và acceptance criteria của task.
- [ ] Không có file không liên quan bị sửa.
- [ ] Không có feature hoặc refactor ngoài scope.
- [ ] Current behavior không bị thay đổi ngoài yêu cầu.
- [ ] Không tạo duplicate code/service/schema/utility.
- [ ] Có cách rollback rõ ràng: revert patch/migration hoặc feature flag nếu cần.

## Security và error handling

- [ ] Không có secret, credential, token hoặc dữ liệu nhạy cảm hard-code.
- [ ] Input không hợp lệ được validate.
- [ ] Empty state được xử lý.
- [ ] Lỗi API, LLM, database hoặc external service được xử lý rõ ràng.
- [ ] Log/error response không lộ secret hay raw sensitive data.
- [ ] Thao tác dữ liệu có giới hạn quyền phù hợp.

## Contract và maintainability

- [ ] API contract không đổi, hoặc thay đổi đã được duyệt và documented.
- [ ] Data model/migration tương thích và có rollback plan.
- [ ] Business logic không bị nhét vào route/UI component.
- [ ] Tên và cấu trúc code dễ hiểu; không có dead code rõ ràng.

## Test và bằng chứng

- [ ] Có test mới cho behavior mới hoặc bug fix.
- [ ] Test cũ vẫn pass.
- [ ] Agent ghi đúng lệnh và kết quả verification.
- [ ] Có screenshot trước/sau nếu sửa UI.
- [ ] Hướng dẫn chạy thử đủ rõ để một reviewer khác owner thực hiện độc lập.
- [ ] Product/QA owner đã xác nhận manual acceptance với task user-facing.

## Severity

| Mức độ | Ý nghĩa | Hành động |
|---|---|---|
| P0 | Không chạy được, lộ secret hoặc có nguy cơ mất dữ liệu | Không merge |
| P1 | Sai core flow, security boundary hoặc public contract | Không merge |
| P2 | Bug nhỏ, edge case hoặc thiếu test quan trọng | Sửa trước hoặc tạo follow-up có owner |
| P3 | Cải thiện readability, performance nhỏ hoặc polish | Có thể follow-up |

## Kết luận review

- [ ] Approve
- [ ] Request changes
- [ ] Blocked — cần quyết định từ owner

Ghi findings theo file/line, severity, impact và cách reproduce; tránh nhận xét chung
chung như “code chưa tốt”.
