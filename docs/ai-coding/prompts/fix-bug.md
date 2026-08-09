# Prompt template — Fix bug

```text
Bạn đang sửa bug sau trong repository hiện tại:

[PASTE BUG TASK]

Quy trình bắt buộc:
1. Đọc AGENTS.md, contract và source/test liên quan.
2. Không sửa code trước khi reproduce hoặc tạo được regression test thể hiện lỗi.
3. Ghi rõ expected behavior, actual behavior, input và lệnh reproduce.
4. Xác định layer và root cause; phân biệt root cause với symptom.
5. Nêu minimal fix và file dự kiến sửa trước khi implement.
6. Chỉ sửa phạm vi nhỏ nhất; không refactor không liên quan hoặc đổi API contract.
7. Thêm regression test fail trước fix và pass sau fix khi khả thi.
8. Chạy test hẹp, regression suite và lint.
9. Không workaround bằng cách swallow exception, bỏ validation, hard-code output,
   thêm dữ liệu giả hoặc làm yếu assertion.
10. Review git diff và báo test nào chưa thể chạy.

Báo cáo cuối theo format:

## Implementation summary
## Root cause
## Files changed
## Tests run
## Result
## Known limitations
## Follow-up tasks
```
