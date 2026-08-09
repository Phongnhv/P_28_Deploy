# Hướng dẫn sử dụng bộ tài liệu AI coding

Bộ tài liệu này giúp team giao task nhỏ, kiểm chứng được cho coding agent. Nó không
thay thế review của con người và không cấp quyền tự mở rộng product scope.

## Bản đồ tài liệu

- [Luật coding agent](./VIBE_CODING_RULES.md)
- [Template task](./TASK_TEMPLATE.md)
- [Checklist review](./REVIEW_CHECKLIST.md)
- [Checklist testing](./TESTING_CHECKLIST.md)
- [Debugging playbook](./DEBUGGING_PLAYBOOK.md)
- [Prompt implement feature](./prompts/implement-feature.md)
- [Prompt fix bug](./prompts/fix-bug.md)
- [Prompt review code](./prompts/review-code.md)
- [Product spec](../PRODUCT_SPEC.md)
- [Implementation plan](../IMPLEMENTATION_PLAN.md)
- [Backlog](../BACKLOG.md)

## Bắt đầu một task

1. Chọn một task nhỏ trong `docs/BACKLOG.md`.
2. Gán đúng một owner chính và một reviewer khác owner cho task quan trọng.
3. Copy `TASK_TEMPLATE.md`, điền scope, acceptance criteria và verification command.
4. Owner đọc product spec, contract và source/test liên quan.
5. Gửi task hoàn chỉnh cho coding agent bằng prompt phù hợp.
6. Agent phải inspect, tóm tắt hiểu biết, nêu plan ngắn và file dự kiến sửa trước.
7. Implement theo increment nhỏ, chạy test và tự review `git diff`.
8. Reviewer dùng `REVIEW_CHECKLIST.md`; Product/QA owner chạy manual acceptance test.

## Cách đọc context

Đọc theo thứ tự từ ổn định đến chi tiết:

```text
PRODUCT_SPEC
  -> IMPLEMENTATION_PLAN
  -> BACKLOG/TASK
  -> API_CONTRACT + DATA_MODEL
  -> source code + tests
```

Code hiện tại là nguồn sự thật cho behavior đang chạy. Các mục ghi `Proposed`,
`Assumption` hoặc `Not implemented` không được mô tả như tính năng đã hoàn thành.

## Cách giao task cho AI agent

Task tốt phải nói rõ outcome, current/expected behavior, file được phép sửa, điều
không được sửa, acceptance criteria và lệnh verification.

Ví dụ tốt:

> Implement `DQ-API-001`: thêm Pydantic request/response schema đúng contract trong
> `docs/API_CONTRACT.md`. Chỉ sửa `src/models/` và test tương ứng. Không thêm route,
> dependency hoặc database code. Chạy `pytest tests/unit -v` và Ruff.

Ví dụ quá mơ hồ:

> Làm backend RidePulse cho xong, thêm database và sửa luôn UI nếu cần.

Task mơ hồ phải được chia nhỏ trước khi giao agent.

## Review diff

- Đọc `git status --short` và danh sách file changed trước.
- So từng thay đổi với acceptance criteria, không review dựa trên số dòng.
- Tìm file ngoài scope, duplicate code, secret, contract change và test bị xóa.
- Chạy lại verification command độc lập.
- Với UI, yêu cầu screenshot và test happy/empty/error state.

## Khi nào phải hỏi người phụ trách

- Thay đổi product scope hoặc user flow: hỏi Product/QA owner.
- Thay đổi API/data contract hoặc dependency: hỏi Backend owner và reviewer.
- Thay đổi graph/prompt/model/evaluation: hỏi Agent owner.
- Thay đổi UI integration/deployment: hỏi UI/Integration owner.
- Cần secret, quyền truy cập, dữ liệu hoặc quyết định chưa có trong docs: dừng và hỏi.

Không để AI tự quyết định khi lựa chọn làm thay đổi dữ liệu, bảo mật, chi phí API,
public contract, dependency nền tảng hoặc Definition of Done.

## Lưu prompt và quyết định quan trọng

- Công cụ đã có auto-hook không được log thủ công; đọc `.agents/rules/ai-log-hook.md`.
- Tool web không có hook dùng workflow `.agents/workflows/log.md`.
- Quyết định kiến trúc lâu dài phải ghi vào `docs/DECISIONS.md`.
- Thay đổi scope/backlog phải được cập nhật trong tài liệu tương ứng và review.
