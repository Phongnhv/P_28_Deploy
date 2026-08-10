# Cập nhật triển khai CROSS_FIELD_COMPARISON

Ngày cập nhật: 2026-08-11

## Phạm vi

Thực hiện đúng nội dung trong `implementation_plan.md` để AI Rule Proposer có thể sinh, kiểm tra schema và đóng dấu ID cho rule so sánh liên cột. Không thay đổi compiler, runner, API hoặc database schema.

## Thay đổi so với code gốc

### `src/models/rule_schemas.py`

- Thêm `CROSS_FIELD_COMPARISON` vào enum `RuleType`.
- Thêm hai field optional `target_column` và `operator` vào `RuleParameters`.
- Thêm guardrail trong `ProposedRule`: rule `CROSS_FIELD_COMPARISON` chỉ hợp lệ khi `target_column` và `operator` đều khác `None`.
- Giữ nguyên validation của các rule cũ.

### `src/agents/nodes/templates.py`

- Bổ sung định nghĩa và điều kiện áp dụng `CROSS_FIELD_COMPARISON` vào bảng rule được hỗ trợ.
- Đổi hướng dẫn cho `cross_column_hints` từ việc xem xét `RANGE`/custom sang ưu tiên `CROSS_FIELD_COMPARISON`.
- Bổ sung mapping `CROSS_FIELD_COMPARISON` sang dimension `CONSISTENCY`.
- Cập nhật danh sách hợp lệ từ 8 lên 9 `RuleType`.
- Thêm few-shot example số 5 cho quan hệ `tpep_pickup_datetime <= tpep_dropoff_datetime`.

### `src/agents/tools/profile_digest.py`

- Đưa `cross_column_hints` lên ngay sau `table` và `rows` trong `table_digest` để xuất hiện sớm khi serialize JSON cho prompt.
- Không thay đổi nội dung hoặc cách tính `cross_column_hints`.

### `src/agents/nodes/rule_proposer_node.py`

- Cập nhật `_stamp_rule()` để rule liên cột có ID dạng:
  `table.source_column.VS.target_column.CROSS_FIELD_COMPARISON`.
- Giữ nguyên format ID của các rule cũ và cơ chế chống trùng bằng suffix `#2`, `#3`, ...

### `tests/test_rule_proposer.py`

- Thêm test parse payload giả lập từ LLM thành `TableRuleProposal`.
- Kiểm tra enum, `target_column`, `operator`, parameters sau khi stamp và format `rule_id` liên cột.

## Tương thích ngược

- Các rule cũ như `NOT_NULL`, `UNIQUE`, `RANGE`, `ACCEPTED_VALUES`, `REGEX_FORMAT`, `FRESHNESS`, `ROW_COUNT` và `NULL_RATE` giữ nguyên schema và format ID.
- Cơ chế lưu `parameters` dưới dạng JSON hiện có tiếp nhận hai field mới mà không cần migration database.
- API/HITL và cơ chế deduplicate `rule_id` tiếp tục hoạt động qua các regression test hiện có.

## Kết quả kiểm tra

- `python -m pytest tests/test_rule_proposer.py -v`: **12 passed**.
- `python -m pytest tests/test_hitl_gate.py -v`: **15 passed**.
- `python -m pytest tests/test_api/test_hitl_routes.py -v`: **16 passed**.
- Tổng cộng: **43 passed**.
- `python -m ruff check src tests`: **All checks passed**.
- `git diff --check`: không phát hiện whitespace error.
- Standalone harness `python -m src.agents.nodes.rule_proposer_node` chạy thành công với UTF-8, nhưng không thực hiện live proposal vì workspace chưa có file `debug_profile_digest_*.json` trong `output/profiler/` hoặc `data/results/`.

## Môi trường kiểm thử

- Đặt tạm `PROVIDER=openai` trong process test vì `.env` hiện không cung cấp provider hợp lệ cho test collection.
- Đồng bộ `venv` bằng dependencies đã có sẵn trong `requirements.txt`; không sửa `requirements.txt`.
- Đặt tạm `PYTHONUTF8=1` khi chạy standalone harness để PowerShell hiển thị tiếng Việt đúng encoding.
