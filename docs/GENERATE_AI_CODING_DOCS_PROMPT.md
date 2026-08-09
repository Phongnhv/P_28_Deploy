# Prompt: Hoàn thiện bộ tài liệu MVP và Vibe Coding

Copy toàn bộ nội dung trong phần `BEGIN PROMPT` đến `END PROMPT` để gửi cho coding agent.

---

## BEGIN PROMPT

Bạn đang làm việc trong repository hiện tại của team. Nhiệm vụ của bạn là hoàn thiện một bộ tài liệu quản lý MVP và vibe coding, không phải viết lại toàn bộ source code.

## Mục tiêu

- Hỗ trợ team phối hợp implement MVP với trách nhiệm và phạm vi rõ ràng.
- Giúp GPT, Codex, Cursor, Claude hoặc Gemini sửa code có kiểm soát.
- Làm rõ product scope, architecture, API contract, data model, backlog, testing và quy trình review.
- Tài liệu phải phản ánh code hiện tại, không được tự bịa kiến trúc hoặc feature chưa tồn tại.

## Quy tắc bắt buộc

1. Trước khi chỉnh sửa, hãy inspect:
   - `README.md`
   - `ARCHITECTURE.md`
   - `WORKLOG.md`
   - `JOURNAL.md`
   - toàn bộ file liên quan trong `src/`
   - toàn bộ file liên quan trong `tests/`
   - các file hiện có trong `docs/`
   - `.agents/`, nếu có liên quan.
2. Không xóa hoặc overwrite nội dung có giá trị của team.
3. Không sửa source code, trừ khi thật sự cần thiết để tài liệu chính xác; nếu sửa, phải báo rõ.
4. Không thêm công nghệ, database, API hoặc feature chưa tồn tại mà không đánh dấu `Proposed`, `Assumption` hoặc `Not implemented`.
5. Nếu tài liệu cũ có placeholder hoặc mâu thuẫn với code, cập nhật theo code thực tế.
6. Nếu thiếu thông tin, ghi vào `Open Questions`, không tự đoán.
7. Viết tài liệu bằng tiếng Việt; giữ nguyên tên file, class, function, endpoint và technical terms cần thiết.
8. Dùng Markdown đơn giản, dễ đọc trên GitHub.
9. Không tạo README cho từng folder code. Tài liệu chỉ tập trung vào product, feature, contract, task và workflow.

## Files cần tạo hoặc cập nhật

Tạo/cập nhật các file sau:

```text
AGENTS.md
docs/ai-coding/README.md
docs/ai-coding/VIBE_CODING_RULES.md
docs/ai-coding/TASK_TEMPLATE.md
docs/ai-coding/REVIEW_CHECKLIST.md
docs/ai-coding/TESTING_CHECKLIST.md
docs/ai-coding/DEBUGGING_PLAYBOOK.md
docs/ai-coding/prompts/implement-feature.md
docs/ai-coding/prompts/fix-bug.md
docs/ai-coding/prompts/review-code.md
docs/PRODUCT_SPEC.md
docs/IMPLEMENTATION_PLAN.md
docs/BACKLOG.md
docs/API_CONTRACT.md
docs/DATA_MODEL.md
docs/TEST_PLAN.md
docs/DECISIONS.md
docs/RUNBOOK.md
```

Có thể cập nhật `ARCHITECTURE.md`, `README.md` và `WORKLOG.md` nếu nội dung đang là placeholder hoặc đã stale, nhưng phải giữ lại nội dung có giá trị.

## Nội dung bắt buộc

### `AGENTS.md`

Đây là luật chung cho mọi coding agent. Phải có:

- Project overview ngắn.
- Các tài liệu phải đọc trước khi code.
- Quy tắc inspect code trước khi sửa.
- Chỉ sửa file trong phạm vi task.
- Không tự ý thêm dependency hoặc thay đổi API contract.
- Không hard-code secret/API key.
- Không xóa test hiện có.
- Quy tắc code, error handling và testing.
- Definition of Done.
- Cách xử lý khi bị block.

Mọi task phải kết thúc bằng báo cáo:

```md
## Implementation summary
## Files changed
## Tests run
## Result
## Known limitations
## Follow-up tasks
```

### `docs/ai-coding/`

Tạo bộ hướng dẫn gồm:

- Cách bắt đầu và giao task cho AI.
- Cách inspect code, lập plan, implement incremental, chạy test và review diff.
- Các điều agent phải làm và không được làm.
- Không rewrite toàn bộ project.
- Không sửa file ngoài phạm vi.
- Không tạo duplicate service/schema/utility.
- Không đưa business logic vào API route nếu project có service layer.
- Không thêm feature ngoài scope.
- Không dùng dữ liệu giả để che lỗi.
- Không claim hoàn thành nếu test fail hoặc chưa chạy verification.

Tạo checklist review có các mục: correctness, scope, security, error handling, empty state, test coverage, API compatibility, regression và rollback.

Tạo testing checklist bao gồm unit test, API test, integration test, agent test, manual UI test, invalid input, empty data, external service failure, regression và demo smoke test.

Tạo debugging playbook theo quy trình:

```text
Reproduce lỗi
→ Ghi input và expected behavior
→ Xác định layer lỗi
→ Đọc log/stack trace
→ Đặt hypothesis
→ Thử thay đổi nhỏ nhất
→ Viết regression test
→ Verify lại happy path
```

Các prompt template trong `docs/ai-coding/prompts/` phải lần lượt dùng cho:

1. Implement feature.
2. Fix bug.
3. Review code.

Mỗi prompt phải yêu cầu agent đọc context, inspect code, nêu plan, liệt kê file dự kiến sửa, implement nhỏ từng bước, chạy test, review diff và báo cáo kết quả.

### `docs/PRODUCT_SPEC.md`

Ghi rõ:

- Problem statement.
- Target user.
- User journey.
- MVP objective.
- Must-have features.
- Nice-to-have features.
- Out-of-scope features.
- Success criteria.
- Demo scenario.
- Open questions.

Không tự bịa feature. Thông tin chưa xác định phải ghi `[NEEDS CONFIRMATION]`.

### `docs/IMPLEMENTATION_PLAN.md`

Chia kế hoạch thành:

```text
Phase 0 — Scope and setup
Phase 1 — Stable project skeleton
Phase 2 — First end-to-end happy path
Phase 3 — API and data contract stabilization
Phase 4 — Error handling and reliability
Phase 5 — UI/demo polish
Phase 6 — Evaluation and deployment
```

Mỗi phase phải có objective, deliverables, tasks, dependencies, owner role, exit criteria, risks và verification method.

Ưu tiên vertical slice: mỗi feature nên đi được qua UI → API → business logic/agent → data → test, thay vì chia việc chỉ theo folder.

### `docs/BACKLOG.md`

Tạo task nhỏ, có thể giao cho AI agent. Mỗi task phải có:

- ID.
- Title.
- Priority P0/P1/P2.
- Owner role.
- Status.
- Dependencies.
- Estimate.
- Acceptance criteria.
- Verification command.
- Related files.

Không tạo task quá lớn như `Build entire backend`. Hãy chia thành các task như:

- Add health endpoint.
- Define request/response schema.
- Implement one service function.
- Add one agent node.
- Add API test.
- Add loading state.
- Add empty state.
- Add demo dataset.
- Add smoke test.

### `docs/API_CONTRACT.md`

Dựa trên code thực tế để ghi endpoint, HTTP method, request, response, error response, status codes, validation rules và ví dụ curl/JSON.

Đánh dấu rõ endpoint nào đang tồn tại và endpoint nào chỉ mới là proposed.

### `docs/DATA_MODEL.md`

Ghi entity, field, type, required/optional, relationship, source of truth, sample data, validation và privacy/security concern.

Nếu database chưa được implement, ghi rõ `Status: Proposed / Not implemented`.

### `docs/TEST_PLAN.md`

Tạo test strategy gồm unit, API, agent, integration, manual test, demo smoke test, regression và evaluation dataset. Có bảng area, test type, owner, minimum coverage và status.

### `docs/DECISIONS.md`

Tạo ADR cho các quyết định thể hiện trong code, ví dụ FastAPI, LangGraph, LLM service, Pydantic schemas, storage/database, frontend và deployment.

Mỗi ADR dùng format:

```md
## ADR-XXX: [Decision]

Date:
Status: Accepted / Proposed / Superseded

### Context
### Decision
### Alternatives considered
### Consequences
```

### `docs/RUNBOOK.md`

Hướng dẫn setup, tạo `.env`, chạy backend/frontend, chạy test, lint, mở API docs, debug lỗi thường gặp, chuẩn bị demo và deploy nếu repository đã có cấu hình. Ưu tiên lệnh Windows PowerShell nhưng có thể thêm lệnh Linux/macOS nếu cần.

## Phân công team

Dùng role thay vì tự gán tên:

| Role | Responsibility |
|---|---|
| Product/QA owner | Scope, user flow, datasets, acceptance criteria, manual testing |
| Backend owner | API, schemas, services, data access |
| Agent owner | LangGraph, prompts, tools, evaluation |
| UI/Integration owner | UI, API integration, demo flow, deployment support |

Nguyên tắc:

- Mỗi task có một owner chính.
- Product/QA owner chịu trách nhiệm scope, acceptance criteria và manual testing.
- Task quan trọng cần reviewer khác owner.
- Không merge nếu thiếu acceptance criteria hoặc verification result.

## Kiểm tra sau khi hoàn thành

1. Kiểm tra toàn bộ link nội bộ.
2. Kiểm tra tên file và đường dẫn có đúng repository.
3. Đảm bảo tài liệu không mô tả feature chưa có như đã hoàn thiện.
4. Loại bỏ trùng lặp không cần thiết giữa các file.
5. Đảm bảo mỗi backlog task có acceptance criteria.
6. Đảm bảo mỗi phase có exit criteria.
7. Đảm bảo `AGENTS.md` đủ ngắn để coding agent thực sự đọc.
8. Chạy test hiện có nếu hợp lý.
9. Không implement feature mới.

Cuối cùng, báo cáo:

- Files created.
- Files updated.
- Assumptions.
- Open questions.
- Các điểm team cần xác nhận.
- Verification commands đã chạy và kết quả.

Hãy bắt đầu bằng việc inspect repository, lập danh sách file cần tạo/cập nhật, sau đó thực hiện từng nhóm tài liệu có kiểm soát.

## END PROMPT

---

## Cách sử dụng

Mở file này, copy nội dung giữa `BEGIN PROMPT` và `END PROMPT`, rồi gửi cho coding agent. Agent phải làm việc trong repository hiện tại và tạo/cập nhật các file được chỉ định.
