# Testing Checklist

## Các lớp kiểm thử

- [ ] **Unit test:** function/service/schema chạy độc lập, external service được mock.
- [ ] **API test:** method, path, validation, status code và response schema đúng.
- [ ] **Integration test:** các layer thật cần thiết phối hợp đúng, ví dụ API–database.
- [ ] **Manual UI test:** thao tác thật trên browser, có screenshot/evidence.
- [ ] **Regression test:** behavior cũ liên quan vẫn đúng.
- [ ] **Smoke test:** core flow chạy được trước demo/deploy.

## Scenario bắt buộc khi phù hợp

- [ ] Happy path.
- [ ] Invalid input và boundary value.
- [ ] Empty data/empty state.
- [ ] Resource không tồn tại.
- [ ] Duplicate/idempotent request.
- [ ] Timeout hoặc external service failure.
- [ ] LLM trả output malformed hoặc từ chối.
- [ ] Database unavailable/transaction rollback.
- [ ] Unauthorized state transition hoặc unapproved rule execution.

## Cách chạy hiện tại

```powershell
.\.venv\Scripts\python.exe -m pytest tests -v
.\.venv\Scripts\python.exe -m ruff check src tests
```

Các integration test PostgreSQL và frontend test là `Proposed / Not implemented`.
Khi được thêm, lệnh chạy phải cập nhật ở `docs/RUNBOOK.md` và task liên quan.

## Mẫu test case

### TC-XXX

#### Scenario

[Tên scenario và behavior cần chứng minh]

#### Preconditions

- [Environment/data/user state]

#### Steps

1. [Bước 1]
2. [Bước 2]

#### Expected result

[Kết quả quan sát được, gồm status/message/state]

#### Actual result

[Điền sau khi chạy]

#### Status

[Not run | Pass | Fail | Blocked]

#### Evidence

[Log đã loại secret, screenshot hoặc link test output]
