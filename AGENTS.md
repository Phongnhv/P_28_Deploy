# Hướng dẫn cho coding agent

## Project overview

RidePulse DQ đang ở trạng thái **starter template**. Code hiện có là FastAPI +
LangGraph demo tối giản; pipeline dữ liệu, PostgreSQL, HITL, rule engine, Dagster và
React MVP đều **chưa được implement**. Mục tiêu hiện tại là vertical slice trên NYC
TLC Yellow Taxi với 100k–1M dòng.

## Phải đọc trước khi code

1. `docs/PRODUCT_SPEC.md`
2. `docs/IMPLEMENTATION_PLAN.md`
3. `docs/BACKLOG.md` và task được giao
4. Contract liên quan: `docs/API_CONTRACT.md`, `docs/DATA_MODEL.md`
5. `docs/ai-coding/VIBE_CODING_RULES.md`
6. File source và test trực tiếp liên quan
7. `.agents/rules/ai-log-hook.md` nếu thao tác AI logging

Nếu tài liệu và code khác nhau, code mô tả **current behavior**; tài liệu mô tả
**Proposed** chỉ là mục tiêu. Báo xung đột trước khi thay đổi contract.

## Quy tắc làm việc

- Inspect code và `git status` trước khi sửa; giữ nguyên thay đổi không thuộc task.
- Chỉ sửa các file được task cho phép. Muốn mở rộng scope phải xin reviewer.
- Không rewrite toàn bộ project, không tạo duplicate service/schema/utility.
- Không tự thêm dependency, database, endpoint hoặc đổi API contract.
- Route chỉ validate/điều phối; business logic đặt trong service khi layer đó tồn tại.
- Giữ behavior cũ trừ khi acceptance criteria yêu cầu thay đổi.
- Dùng type hints, tên tiếng Anh rõ nghĩa, function nhỏ và import tuyệt đối từ `src`.
- Validate input ở boundary; lỗi phải có thông điệp ổn định và không lộ secret.
- Không swallow exception, không dùng dữ liệu giả để che lỗi production flow.
- Không hard-code API key, credential, token hoặc dữ liệu nhạy cảm.
- Không xóa, skip hay làm yếu test hiện có để khiến CI pass.
- Không sửa `.ai-log/`; không chạy manual logging cho tool đã được auto-log.

## Testing

- Thay đổi behavior phải có test happy path và failure path liên quan.
- Bug fix phải có regression test chứng minh lỗi cũ.
- Mock LLM/external service trong unit test; không gọi API trả phí trong test.
- Chạy test hẹp trước, sau đó chạy `pytest tests/ -v` và
  `ruff check src/ tests/` nếu môi trường cho phép.
- Không claim “done” nếu chưa ghi rõ lệnh verification và kết quả thực tế.

## Definition of Done cho một task

- Acceptance criteria có thể kiểm chứng đều đạt.
- Không có thay đổi ngoài scope hoặc secret mới.
- Test mới và test liên quan pass; lint pass.
- API/data contract và docs được cập nhật nếu behavior công khai thay đổi.
- `git diff` đã được tự review; limitation và follow-up được ghi rõ.
- Task quan trọng có reviewer khác owner trước khi merge.

## Khi bị block

1. Ghi lại bước reproduce, lệnh đã chạy và lỗi nguyên văn đã loại secret.
2. Phân biệt block do code, environment, dependency, external service hay thiếu quyết định.
3. Thử phương án nhỏ, an toàn, không đổi scope.
4. Nếu cần quyền, secret hoặc quyết định product/contract, dừng và hỏi đúng owner.
5. Không tự tạo workaround làm sai acceptance criteria.

## Báo cáo cuối task

```md
## Implementation summary

## Files changed

## Tests run

## Result

## Known limitations

## Follow-up tasks
```
