# Vibe Coding Rules

## Agent must do

- Đọc `AGENTS.md`, task, contract liên quan và source/test hiện tại.
- Tóm tắt current behavior và Proposed behavior trước khi code.
- Nêu plan ngắn, file dự kiến sửa và verification command.
- Implement increment nhỏ nhất đáp ứng acceptance criteria.
- Giữ tương thích behavior/API ngoài phạm vi task.
- Tự review diff và báo cáo limitation trung thực.

## Agent must not do

- Rewrite toàn bộ project hoặc “dọn dẹp” file không liên quan.
- Sửa file ngoài phạm vi task.
- Tạo duplicate service, schema, helper hoặc config.
- Tự thêm feature, dependency, endpoint hay database ngoài scope.
- Đưa business logic vào API route khi project có service layer phù hợp.
- Dùng dữ liệu giả để che lỗi trong production flow.
- Swallow exception, hard-code kết quả hoặc bỏ qua test vì code “có vẻ đúng”.
- Xóa test hiện có, làm yếu assertion hoặc commit secret.

## Before coding

1. Chạy `git status --short`.
2. Đọc file được phép sửa và test trực tiếp liên quan.
3. Tìm implementation tương tự bằng `rg`; tái sử dụng thay vì duplicate.
4. Xác nhận current behavior bằng test hoặc reproduce.
5. Kiểm tra task có acceptance criteria và verification command.
6. Đánh dấu câu hỏi có thể làm thay đổi scope là blocker.

## During coding

- Giữ patch nhỏ, dễ rollback.
- Validate input tại API/schema boundary.
- Tách I/O, business logic và orchestration.
- Dùng typed models thay cho dict không có contract khi phù hợp.
- Ghi log đủ context nhưng không log secret/raw sensitive data.
- Thêm test cùng increment thay đổi behavior.

## After coding

1. Chạy test hẹp.
2. Chạy regression suite và lint phù hợp.
3. Review `git diff --check`, `git diff --stat` và diff nội dung.
4. Đối chiếu từng acceptance criterion.
5. Cập nhật public contract/docs nếu task thay đổi behavior công khai.
6. Viết completion report theo `AGENTS.md`.

## If tests fail

- Không sửa assertion chỉ để khớp output mới nếu contract không đổi.
- Phân loại lỗi mới, regression hay environment.
- Reproduce bằng test nhỏ nhất và đọc stack trace đầu tiên có ý nghĩa.
- Sửa root cause; thêm regression test khi đó là bug.
- Nếu không chạy được, báo chính xác lệnh, lỗi và test chưa được verify.

## If blocked

- Thu thập evidence đã loại secret.
- Nêu điều đã thử và lý do chưa thể tiếp tục.
- Hỏi đúng owner về secret, quyền, dữ liệu hoặc quyết định contract.
- Không mở rộng quyền hay scope để đi vòng blocker.

## Security rules

- Secret chỉ qua environment/secret manager; không ghi vào code, test fixture hay docs.
- Không đưa raw trip rows vào LLM request.
- LLM không sinh hoặc thực thi arbitrary SQL.
- Rule execution phải read-only và chỉ chạy rule đã được approve.
- Error response/log không được lộ credential, prompt hệ thống hoặc stack trace production.
- Dependency mới cần review license, version và nhu cầu thực tế.

## Scope control

- Một task có đúng một objective và owner chính.
- File ngoài `Files allowed to change` mặc định không được sửa.
- Refactor chỉ được làm khi cần cho acceptance criteria và phải nêu rõ.
- Feature `Proposed` không được âm thầm implement trong task khác.
- Phát hiện việc hữu ích nhưng ngoài scope thì tạo follow-up, không làm kèm.
