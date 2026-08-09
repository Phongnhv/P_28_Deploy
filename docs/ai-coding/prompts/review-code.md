# Prompt template — Review code

```text
Review thay đổi hiện tại so với task/contract sau:

[PASTE TASK OR CONTRACT]

Trước khi review:
- Đọc AGENTS.md và docs/ai-coding/REVIEW_CHECKLIST.md.
- Inspect git status, full diff, code liên quan và test hiện có.
- Không sửa code; chỉ review trừ khi được yêu cầu riêng.

Đánh giá:
- Correctness và edge cases.
- Security, secret/data exposure và authorization boundary.
- Input validation và error handling.
- Maintainability, duplicate code và layer boundaries.
- Test coverage và chất lượng assertion.
- API/data compatibility.
- Scope creep và file ngoài task.
- Potential regression.

Mỗi finding phải có severity P0–P3, file/line, impact và cách reproduce hoặc lý do
kỹ thuật. Không tạo finding chỉ vì preference style.

Output đúng format:

## Summary
## Findings
## Must fix
## Should fix
## Nice to have
## Tests missing
## Merge recommendation
```
