# RidePulse DQ — API Contract

> **Current base URL:** `http://localhost:8000`
>
> **Current API prefix:** `/api/v1`
>
> Endpoint ghi `Proposed / Not implemented` không tồn tại trong code hiện tại.

## 1. Quy ước

- JSON request/response dùng `snake_case`.
- ID đề xuất dùng UUID string.
- Timestamp đề xuất dùng ISO 8601 UTC.
- Long-running operation trả resource/run ID và status, không giữ HTTP connection.
- Không trả raw trip records trong API Agent/DQ result.

### Proposed error envelope

```json
{
  "detail": {
    "code": "DATASET_NOT_FOUND",
    "message": "Dataset không tồn tại.",
    "request_id": "3d970ff6-42d4-4a07-a719-86adf5542020"
  }
}
```

FastAPI validation error `422` hiện vẫn dùng format mặc định. Error envelope trên là
Proposed và chưa được implement.

## 2. Endpoint hiện có

### GET `/health`

- **Status:** Implemented.
- **Purpose:** Liveness cơ bản của process.
- **Request:** Không có body.
- **Success:** `200 OK`.

```json
{
  "status": "ok",
  "env": "development"
}
```

`env` lấy từ `Settings.app_env`, chỉ nhận `development`, `production`, `test`.
Endpoint chưa kiểm tra LLM/database readiness.

```powershell
curl.exe http://localhost:8000/health
```

### GET `/api/v1/status`

- **Status:** Implemented placeholder.
- **Purpose:** Trả status tĩnh của demo Agent.
- **Success:** `200 OK`.

```json
{
  "status": "ready",
  "agent": "LangGraph Agent v1.0"
}
```

Response không chứng minh LLM/external dependency thật sự ready.

```powershell
curl.exe http://localhost:8000/api/v1/status
```

### POST `/api/v1/chat`

- **Status:** Implemented placeholder; không thuộc product MVP target.
- **Request:** `ChatRequest`.

```json
{
  "message": "Hello"
}
```

Validation:

- `message`: string, required, min length 1, max length 5000.
- Empty hoặc missing message trả `422 Unprocessable Entity`.

Success `200 OK`:

```json
{
  "response": "Kết quả dựa trên phân tích: Phân tích: Hello",
  "analysis": "Phân tích: Hello"
}
```

Current failure:

- Exception trong graph trả `500` với `{"detail":"<exception text>"}`.
- Đây là current behavior, không phải error contract mong muốn.

```powershell
curl.exe -X POST http://localhost:8000/api/v1/chat `
  -H "Content-Type: application/json" `
  -d '{"message":"Hello"}'
```

## 3. Endpoint MVP đề xuất

### Tổng quan

| Method | Path | Status | Success codes |
|---|---|---|---|
| POST | `/api/v1/datasets/ingest` | Proposed | 202, 200 idempotent replay |
| GET | `/api/v1/datasets` | Proposed | 200 |
| POST | `/api/v1/datasets/{dataset_id}/profile` | Proposed | 202 |
| GET | `/api/v1/datasets/{dataset_id}/profile` | Proposed | 200 |
| POST | `/api/v1/datasets/{dataset_id}/rule-proposals` | Proposed | 202 |
| GET | `/api/v1/rule-proposals` | Proposed | 200 |
| PATCH | `/api/v1/rule-proposals/{proposal_id}` | Proposed | 200 |
| POST | `/api/v1/rules/{rule_id}/compile` | Proposed | 200 |
| POST | `/api/v1/dq-runs` | Proposed | 202 |
| GET | `/api/v1/dq-runs/{run_id}` | Proposed | 200 |
| GET | `/api/v1/dq-runs/{run_id}/results` | Proposed | 200 |
| GET | `/api/v1/audit-logs` | Proposed | 200 |

### POST `/api/v1/datasets/ingest`

Request:

```json
{
  "manifest_name": "nyc-yellow-2024-01-dev-small"
}
```

Validation: manifest phải nằm trong server-side allow-list; client không được gửi
arbitrary filesystem path hoặc URL.

Response `202 Accepted`:

```json
{
  "ingestion_run_id": "uuid",
  "dataset_id": "uuid",
  "status": "PENDING"
}
```

Errors: `404 MANIFEST_NOT_FOUND`, `409 INGESTION_ALREADY_RUNNING`,
`422 MANIFEST_INVALID`, `503 DATABASE_UNAVAILABLE`.

```powershell
curl.exe -X POST http://localhost:8000/api/v1/datasets/ingest `
  -H "Content-Type: application/json" `
  -d '{"manifest_name":"nyc-yellow-2024-01-dev-small"}'
```

### GET `/api/v1/datasets`

Response `200 OK`:

```json
{
  "items": [
    {
      "dataset_id": "uuid",
      "name": "nyc-yellow-2024-01-dev-small",
      "row_count": 100000,
      "status": "READY",
      "latest_profile_id": "uuid"
    }
  ]
}
```

Empty result trả `{"items":[]}`, không trả `404`.

### POST `/api/v1/datasets/{dataset_id}/profile`

Không có body trong MVP. Response `202 Accepted`:

```json
{
  "profile_run_id": "uuid",
  "dataset_id": "uuid",
  "status": "PENDING"
}
```

Errors: `404 DATASET_NOT_FOUND`, `409 PROFILE_ALREADY_RUNNING`,
`422 DATASET_NOT_READY`.

### GET `/api/v1/datasets/{dataset_id}/profile`

Query optional: `profile_id=<uuid>`. Không truyền thì lấy latest successful profile.

Response `200 OK`:

```json
{
  "profile_id": "uuid",
  "dataset_id": "uuid",
  "row_count": 100000,
  "generated_at": "2026-08-06T10:00:00Z",
  "columns": [
    {
      "name": "trip_distance",
      "data_type": "double precision",
      "null_rate": 0.0,
      "distinct_count": 2451,
      "statistics": {"min": 0.0, "median": 1.7, "p95": 8.2, "max": 75.4}
    }
  ]
}
```

Errors: `404 DATASET_NOT_FOUND` hoặc `PROFILE_NOT_FOUND`.

### POST `/api/v1/datasets/{dataset_id}/rule-proposals`

Request:

```json
{
  "profile_id": "uuid"
}
```

Response `202 Accepted`:

```json
{
  "proposal_run_id": "uuid",
  "status": "PENDING"
}
```

Errors: `404 DATASET_NOT_FOUND/PROFILE_NOT_FOUND`, `409 PROPOSAL_ALREADY_RUNNING`,
`422 PROFILE_NOT_READY`, `503 LLM_UNAVAILABLE`.

### GET `/api/v1/rule-proposals`

Query: `dataset_id` required; `status` optional; `limit` default 50, max 100.

Response `200 OK`:

```json
{
  "items": [
    {
      "proposal_id": "uuid",
      "status": "PROPOSED",
      "rule_type": "numeric_range",
      "table": "trips_raw",
      "column": "trip_distance",
      "parameters": {"min": 0},
      "severity": "high",
      "reason": "Observed minimum is below zero.",
      "evidence_refs": ["trip_distance.min"]
    }
  ]
}
```

### PATCH `/api/v1/rule-proposals/{proposal_id}`

Request approve/reject:

```json
{
  "action": "approve",
  "actor": "demo-steward",
  "comment": "Threshold phù hợp với data dictionary."
}
```

Request edit:

```json
{
  "action": "edit",
  "actor": "demo-steward",
  "parameters": {"min": 0, "max": 100},
  "severity": "medium",
  "comment": "Thêm upper bound cho demo."
}
```

Validation: `action` chỉ nhận `approve`, `edit`, `reject`; transition phải hợp lệ.
Response `200` trả proposal/rule state mới. Errors: `404 PROPOSAL_NOT_FOUND`,
`409 INVALID_RULE_TRANSITION`, `422 INVALID_RULE_PARAMETERS`.

### POST `/api/v1/rules/{rule_id}/compile`

Không có body. Chỉ rule `APPROVED` được compile.

Response `200 OK`:

```json
{
  "rule_id": "uuid",
  "status": "COMPILED",
  "compiler_version": "1",
  "sql_preview": "SELECT source_row_id FROM trips_raw WHERE trip_distance < :min_value"
}
```

`sql_preview` không chứa literal secret và không được client tùy chỉnh. Errors:
`404 RULE_NOT_FOUND`, `409 RULE_NOT_APPROVED`, `422 RULE_NOT_COMPILABLE`.

### POST `/api/v1/dq-runs`

Request:

```json
{
  "dataset_id": "uuid",
  "rule_ids": ["uuid"]
}
```

Response `202 Accepted`:

```json
{
  "run_id": "uuid",
  "status": "PENDING"
}
```

Validation: ít nhất một rule; tất cả rule phải thuộc dataset và đã compile/active.
Errors: `404 DATASET_NOT_FOUND/RULE_NOT_FOUND`, `409 RULE_NOT_EXECUTABLE`.

### GET `/api/v1/dq-runs/{run_id}`

Response `200 OK`:

```json
{
  "run_id": "uuid",
  "dataset_id": "uuid",
  "status": "SUCCEEDED",
  "started_at": "2026-08-06T10:05:00Z",
  "finished_at": "2026-08-06T10:05:12Z"
}
```

Status allow-list: `PENDING`, `RUNNING`, `SUCCEEDED`, `FAILED`.

### GET `/api/v1/dq-runs/{run_id}/results`

Response `200 OK`:

```json
{
  "run_id": "uuid",
  "data_health_score": 0.992,
  "results": [
    {
      "rule_id": "uuid",
      "status": "FAILED",
      "failed_rows": 800,
      "eligible_rows": 100000,
      "failed_source_row_ids": ["row-1", "row-9"],
      "failed_ids_truncated": true
    }
  ]
}
```

Danh sách failed IDs bị giới hạn; endpoint không trả raw record values.

### GET `/api/v1/audit-logs`

Query filters Proposed: `dataset_id`, `rule_id`, `event_type`, `limit` (max 100).
Response `200` trả append-only event summaries. Chưa có authentication trong MVP;
actor là demo identity và phải được ghi rõ limitation.

## 4. Compatibility policy

- Thay đổi method/path/required field/status code là contract change và cần ADR/review.
- Thêm optional response field không được làm client cũ fail.
- Không tái sử dụng endpoint `/chat` làm dataset/rule API.
- Khi endpoint Proposed được implement, cập nhật status trong file này cùng task.
