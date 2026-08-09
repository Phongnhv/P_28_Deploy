# Prompt template — Implement feature

```text
Bạn đang implement task dưới đây trong repository hiện tại:

[PASTE TASK]

Yêu cầu làm việc:
1. Đọc AGENTS.md, docs/PRODUCT_SPEC.md, docs/IMPLEMENTATION_PLAN.md,
   docs/BACKLOG.md và contract trực tiếp liên quan.
2. Inspect code, test và git status hiện tại trước khi sửa.
3. Tóm tắt ngắn current behavior, expected behavior và các assumption.
4. Nêu plan 3–6 bước, liệt kê file dự kiến sửa và verification command.
5. Chỉ sửa file nằm trong phạm vi task. Nếu cần file/dependency/contract ngoài
   scope, dừng và báo blocker.
6. Implement incremental; tái sử dụng service/schema/utility hiện có.
7. Không hard-code secret, không swallow exception, không dùng mock/fake để che lỗi
   trong production flow.
8. Thêm test cho happy path và failure path phù hợp.
9. Chạy test hẹp, test regression và lint.
10. Review git diff, đối chiếu từng acceptance criterion.

Báo cáo cuối theo format:

## Implementation summary
## Files changed
## Tests run
## Result
## Known limitations
## Follow-up tasks
```
