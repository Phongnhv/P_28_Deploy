# Debugging Playbook

## Quy trình

1. **Reproduce:** ghi lệnh, environment, input và tần suất xuất hiện.
2. **Chốt expected/actual:** trích contract hoặc acceptance criteria liên quan.
3. **Xác định layer:** setup, frontend, API, service, Agent, LLM, database hay deploy.
4. **Đọc evidence:** bắt đầu từ error đầu tiên có ý nghĩa trong log/stack trace.
5. **Thu nhỏ lỗi:** tạo request/test/fixture nhỏ nhất vẫn reproduce được.
6. **Tạo hypothesis:** mỗi hypothesis phải có cách bác bỏ nhanh.
7. **Thử thay đổi nhỏ nhất:** không refactor hoặc đổi contract trong lúc debug.
8. **Viết regression test:** test phải fail trước fix và pass sau fix nếu khả thi.
9. **Verify:** chạy test hẹp, regression suite, lint và happy path liên quan.
10. **Ghi kết quả:** root cause, fix, limitation và follow-up.

Không paste secret, `.env`, raw dataset hoặc prompt hệ thống vào issue/log.

## Phân loại lỗi

| Layer | Dấu hiệu | Kiểm tra đầu tiên | Owner |
|---|---|---|---|
| Setup/environment | Import lỗi, thiếu command, port bận | Python/venv, dependency, env var, port | Owner task |
| Frontend | Blank screen, state sai, request không gửi | Browser console và Network tab | UI/Integration owner |
| API | 4xx/5xx, schema sai | Request payload, FastAPI log, OpenAPI | Backend owner |
| Business logic | Status đúng nhưng kết quả sai | Unit test service, boundary input | Backend owner |
| Agent/LangGraph | State/node/edge sai | Input/output từng node, mocked graph test | Agent owner |
| LLM/prompt | Output malformed/không ổn định | Model config, structured schema, mock/trace | Agent owner |
| Database | Connection, constraint, transaction | `DATABASE_URL`, migration, query/log | Backend owner |
| Deployment | Local pass nhưng container fail | Image log, env, healthcheck, network | UI/Integration owner |

## Mẫu báo cáo bug

```md
Expected:
Actual:
Reproduction command:
Environment:
First relevant error:
Root-cause hypothesis:
Minimal fix:
Regression test:
Verification result:
```
