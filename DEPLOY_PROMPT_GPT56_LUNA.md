# RidePulse DQ — Prompt triển khai cloud

Bạn đang làm việc trong repository:

`C:\Users\ADMIN\WorkPlace\Vinuni\AssignmentProject\P-028-deploy-fresh`

- Model: `gpt-5.6-luna`
- Reasoning effort: `high`

## Mục tiêu

Hoàn thiện các blocker cuối, commit phiên bản đã kiểm thử, triển khai RidePulse DQ lên cloud và thực hiện smoke test có kiểm soát.

Kiến trúc canonical hiện tại:

```text
Frontend Vercel
→ Cloud Run API Service
→ durable job trong Supabase
→ Cloud Run Job worker
→ Supabase + GCS + OpenAI
```

Không thêm Cloud Scheduler, reconciler, dead-letter queue hoặc hệ thống authorization mới trong nhiệm vụ này.

## Bằng chứng local đã có

Local E2E đã thành công:

- Frontend: `http://127.0.0.1:5173`
- Backend: `http://127.0.0.1:8000`
- Worker: `http://127.0.0.1:8001`
- Mock API: tắt
- Supabase thật
- GCS/object artifact thật
- OpenAI `/v1/chat/completions`: HTTP 200
- Graph 1 profiling: `SUCCEEDED`
- Semantic continuation: `SUCCEEDED`
- 24/24 rules approved
- Graph 2/3: `COMPLETED`
- 10/10 analysis nodes
- 24 test results
- 23 PASS, 1 FAIL, 0 ERROR
- Analysis run: `analysis-7e6a8031539f4424b7`
- Reload khôi phục đúng analysis

Kết quả test gần nhất:

- Full backend suite: 272 passed, 10 skipped
- Targeted regression: 22 passed
- Frontend production build: pass
- Ruff trên các file thay đổi chính: pass

Recordings:

- `C:\Users\ADMIN\.config\browser-harness\agent-workspace\recordings\ridepulse-e2e-user-session-20260826`
- `C:\Users\ADMIN\.config\browser-harness\agent-workspace\recordings\ridepulse-deploy-readiness-smoke-20260826`

## Quy tắc an toàn

- Đọc `AGENTS.md` và hướng dẫn repository trước khi hành động.
- Kiểm tra `git status`, branch và remote.
- Không reset, stash hoặc ghi đè thay đổi hiện có.
- Không commit `.env`, API key, database URL, password hoặc secret.
- Không commit việc xóa 500 dòng trong `.ai-log/session.jsonl`.
- Không reset hoặc xóa dữ liệu Supabase/GCS.
- Không tạo Google Cloud project hoặc Supabase project mới.
- Sử dụng project, region, repository, bucket và Vercel project hiện có.
- Không in secret trong terminal output hoặc báo cáo.
- Không force-push.
- Chỉ deploy lên branch/environment deploy của repository VinUni.
- Nếu thiếu project ID, region, service account hoặc quyền IAM, dừng trước mutation và báo chính xác thứ còn thiếu.

## Phase 1 — Preflight và sửa blocker

### 1. Kiểm tra Git

- Xác nhận branch hiện tại.
- Xác nhận `origin` là repository VinUni.
- Kiểm tra toàn bộ diff.
- Loại `.ai-log/session.jsonl` khỏi commit mà không làm mất thay đổi source.
- Kiểm tra không có secret trong diff.
- Chạy `git diff --check`.

### 2. Sửa dependency Cloud Run

Production dispatcher đang dùng:

```python
from google.cloud import run_v2
```

Bổ sung dependency phù hợp vào `requirements.txt`, ưu tiên version range tương thích với Python và Google libraries hiện tại.

Sau khi cài, bắt buộc kiểm tra:

```bash
python -c "from google.cloud import run_v2; print('cloud-run-import-ok')"
```

Bổ sung test hoặc Docker smoke test để dependency này không thể bị thiếu mà CI vẫn pass.

Không thay dispatcher sang `BackgroundTasks` hoặc inline mode.

### 3. Namespace idempotency tối thiểu

Đổi canonical import key từ dạng global:

```text
versioned-import-{idempotency_key}
```

thành ít nhất:

```text
versioned-import-{workspace_id}-{idempotency_key}
```

Không triển khai authorization phức tạp trong nhiệm vụ này.

Bổ sung test:

- Hai workspace dùng cùng client key không collision.
- Replay trong cùng workspace vẫn trả resource cũ.
- Cùng workspace/key nhưng payload khác vẫn trả 409.

### 4. Sửa mâu thuẫn anomaly report

Hiện kết quả chính là `INSUFFICIENT_HISTORY`, nhưng report có đoạn nói không tạo hypothesis vì conclusion là `NORMAL`.

Yêu cầu:

- Report phải dùng đúng anomaly decision canonical.
- Nếu `INSUFFICIENT_HISTORY`, giải thích rằng chưa đủ lịch sử, hypothesis không được tạo hoặc độ tin cậy bị giới hạn.
- Không được gọi decision này là `NORMAL`.
- Không thay đổi detector nếu detector đang đúng.
- Chỉ sửa state mapping, template hoặc report rendering cần thiết.
- Bổ sung regression test cho `NORMAL`, `ANOMALY`, `INSUFFICIENT_HISTORY` và execution-health failure nếu có.

### 5. Xác nhận worker entrypoint

Kiểm tra `src/worker.py`:

- Chỉ có một top-level `asyncio.run(main())`.
- Canonical async workflow trong event loop phải được `await`, không lồng `asyncio.run()`.
- Worker đọc `RUN_JOB_ID` và `RUN_JOB_TYPE`.
- Worker không fallback canonical job sang taxi legacy path.
- Job không tồn tại hoặc type sai phải fail rõ.
- Terminal job không chạy lại.
- Retry không tạo duplicate report/artifact.

## Phase 2 — Regression trước deploy

Chạy lại toàn bộ:

```text
python -m ruff check <toàn bộ src, tests và các file script đã thay đổi>
python -m pytest -q
cd frontend && npm run build
docker build
```

Docker smoke bắt buộc phải import được:

```text
src.main
src.worker
google.cloud.run_v2
```

Không dùng kết quả test cũ nếu code vừa được sửa.

Chỉ tiếp tục khi:

- Full pytest pass.
- Frontend build pass.
- Changed-source Ruff pass.
- Docker image build pass.
- Không có import error.
- Không có secret trong image/build arguments.
- Không có untracked source/test cần thiết bị bỏ quên.

## Phase 3 — Commit checkpoint

Commit toàn bộ thay đổi hợp lệ, bao gồm test mới.

Không commit:

- `.env`
- credential
- recording
- `.ai-log/session.jsonl` deletion
- local build output
- `node_modules`
- file dữ liệu test sinh ra

Commit message gợi ý:

```text
fix: harden durable versioned workflow deployment
```

Push commit lên `origin/deploy` bằng fast-forward hoặc normal push. Không force-push.

Ghi lại:

- Commit SHA
- Branch
- Remote
- Test result tương ứng commit đó

## Phase 4 — Cloud preflight read-only

### Google Cloud

Kiểm tra:

- Active account
- Active project
- Region
- Artifact Registry repository
- Cloud Run API service hiện có
- Cloud Run Job hiện có hay chưa
- GCS bucket
- API service account
- Worker service account
- Secret Manager references

Không hiển thị secret values.

### Supabase

Xác nhận các bảng/cột/index cần thiết:

- `jobs`
- `lease_expires_at`
- `attempt_count`
- `dataset_versions`
- `profile_runs`
- `graph1_runs`
- `analysis_runs`
- `governed_artifacts`
- `governance_audit_events`

So sánh migrations trong Git với schema hiện tại.

- Không chạy reset database.
- Chỉ áp dụng migration còn thiếu, additive và đã review.
- Nếu migration destructive hoặc không rõ trạng thái, dừng và báo.

### Vercel

- Xác nhận frontend project hiện có.
- Xác nhận production environment variables tồn tại.
- Không đọc hoặc in secret values.

## Phase 5 — Build immutable image

Build một Docker image duy nhất từ commit đã test.

- Tag bằng commit SHA, không chỉ dùng `latest`.
- Push image lên Artifact Registry.
- Lấy immutable image digest sau push.
- Dùng cùng digest cho API và worker.
- Không để API và worker chạy hai phiên bản source khác nhau.

Ví dụ logic tên image:

```text
<registry>/<project>/ridepulse:<commit-sha>
```

## Phase 6 — Deploy Cloud Run Job worker

Tạo hoặc update Cloud Run Job hiện có.

Tên mặc định trong code là `ridepulse-worker`, nhưng phải ưu tiên tên cloud resource hiện có nếu project đã cấu hình khác.

Worker configuration:

- Image: immutable digest vừa build
- Command: `python -m src.worker`
- Region: cùng API
- Task count: 1
- Parallelism: 1
- Max retries: 1 hoặc 2
- Timeout đủ cho Graph 1/2/3 và LLM report
- Memory đủ cho pandas xử lý Telco
- CPU phù hợp
- Không public endpoint

Environment và secrets:

- `APP_ENV=production`
- `SUPABASE_DATABASE_URL`
- `OPENAI_API_KEY`
- model/provider configuration hiện có
- `OBJECT_STORAGE_PROVIDER=gcs`
- `OBJECT_STORAGE_BUCKET`
- các biến Graph/dbt cần thiết
- demo password chỉ khi worker thực sự cần startup path đó

IAM worker:

- Đọc/ghi Supabase qua connection secret
- Đọc source object GCS
- Ghi report/dbt artifact GCS
- Không cấp quyền cloud admin rộng

Sau deploy worker:

- Chạy một smoke execution an toàn hoặc kiểm tra command startup.
- Không chạy full E2E trước khi API được update.
- Xác nhận worker đọc được environment và không log secret.

## Phase 7 — Deploy Cloud Run API

Deploy hoặc update API service bằng cùng image digest.

API command:

```text
uvicorn src.main:app --host 0.0.0.0 --port ${PORT}
```

Environment:

- `APP_ENV=production`
- `GOOGLE_CLOUD_PROJECT`
- `GOOGLE_CLOUD_REGION`
- `CLOUD_RUN_JOB_NAME`
- `SUPABASE_DATABASE_URL`
- `OBJECT_STORAGE_PROVIDER=gcs`
- `OBJECT_STORAGE_BUCKET`
- `FRONTEND_ORIGIN`
- `DEMO_USER_PASSWORD`
- `DEMO_STEWARD_PASSWORD`
- `DEMO_ADMIN_PASSWORD`
- các model/provider variables cần thiết

Không đặt trên production API:

- `WORKER_DISPATCH_MODE=local`
- `LOCAL_WORKER_URL`

IAM API service account:

- Có quyền chạy Cloud Run Job cụ thể.
- Có quyền truy cập secret cần thiết.
- Không dùng Owner/Editor nếu không cần.

Cloud Run settings:

- Health endpoint: `/api/v1/status`
- API timeout không cần bằng workflow timeout vì workflow chạy ở Job
- Min instances có thể là 0 cho scope project
- CORS chỉ cho frontend production URL và local origin nếu thực sự cần
- Cookie production phải `Secure` và `SameSite=None` khi frontend/API khác origin

Sau deploy:

- Kiểm tra service revision `READY`.
- Gọi health endpoint.
- Kiểm tra startup log.
- Không tiếp tục nếu startup fail vì demo secrets hoặc schema mismatch.

## Phase 8 — Deploy frontend Vercel

Frontend environment:

```text
VITE_USE_MOCK_API=false
VITE_API_BASE_URL=<Cloud Run API HTTPS URL>
VITE_WORKSPACE_ID=<workspace cloud hiện có>
```

- Không bundle backend secret.
- Build lại frontend với production variables.
- Chỉ deploy frontend sau khi worker và API đã READY, API health pass.

Kiểm tra:

- Frontend production mở được.
- Network gọi đúng Cloud Run API.
- Không gọi localhost.
- Mock bị tắt.
- Login request có cookie/CSRF đúng.
- Không có CORS error.

## Phase 9 — Cloud smoke test

Dùng browser automation và tạo recording mới.

Bắt đầu bằng CSV nhỏ với prefix:

```text
cloud-smoke-<timestamp>
```

Kiểm tra:

1. Login.
2. Upload.
3. API tạo durable `INGEST_PROFILE` job.
4. Cloud Run Job execution xuất hiện.
5. Worker hoàn tất profile.
6. UI polling thấy `SUCCEEDED`.
7. Refresh vẫn thấy dataset/profile.
8. Upload lại cùng file không tạo orphan object.
9. Chạy Graph 1 đến semantic gate.
10. Semantic approval dispatch continuation.
11. Approve rules.
12. Chạy Graph 2/3.
13. Report được persist trong `analysis_runs.report_markdown`, `governed_artifacts` và GCS.
14. Refresh khôi phục analysis.
15. Không có HTTP 500 hoặc job treo.

Chỉ chạy Telco đầy đủ sau khi dataset nhỏ pass.

## Phase 10 — Cloud E2E Telco

Chạy toàn bộ:

```text
login
→ upload/replay
→ profile
→ Graph 1
→ semantic review
→ approve rules
→ Graph 2
→ Graph 3
→ report
→ refresh
→ rerun
```

Expected:

- 7.043 rows
- 21 columns
- Không taxi hardcode
- Không cross-field `TypeError`
- 24 test results hoặc số rules mới hợp lệ
- Không ERROR kỹ thuật
- Analysis 10/10 nodes
- Report decision nhất quán với anomaly decision
- Report artifact tồn tại trên GCS
- OpenAI trả thành công
- UI không báo mất API

Thu thập:

- Frontend URL
- API URL
- Commit SHA
- Image digest
- Cloud Run API revision
- Cloud Run Job execution IDs
- Dataset/version/profile/Graph1/analysis IDs
- HTTP status
- Browser recording
- Console/network errors
- Worker logs đã redact

## Rollback

Trước deploy, ghi lại:

- API revision cũ
- Worker image/config cũ
- Frontend deployment cũ
- Schema migration đã áp dụng

Nếu cloud smoke fail:

1. Dừng tạo request E2E mới.
2. Roll API traffic về revision cũ.
3. Roll frontend về deployment cũ.
4. Roll worker về image cũ hoặc disable dispatcher mới.
5. Không rollback migration destructive bằng SQL tự phát.
6. Giữ job/run lỗi để chẩn đoán.
7. Báo chính xác layer fail: frontend, CORS/session, API, IAM dispatch, worker, Supabase, GCS hoặc OpenAI.

## Điều kiện GO

Chỉ kết luận GO khi:

- Dependency Cloud Run tồn tại trong image.
- Worker Job thực sự chạy `src.worker`.
- API dispatch được worker.
- Cloud smoke dataset nhỏ pass.
- Telco E2E pass.
- Duplicate upload không tạo orphan.
- Cross-field không ERROR.
- Report và anomaly decision nhất quán.
- Report tồn tại trong DB, governed artifact và GCS.
- Refresh/rerun hoạt động.
- Không có secret trong log.
- Không có canonical job `PENDING`/`RUNNING` vô thời hạn trong test bình thường.
- Có revision/image rollback rõ ràng.

## Đầu ra cuối

Báo cáo:

1. Các blocker đã sửa.
2. Commit SHA và image digest.
3. Cloud resources đã tạo/cập nhật.
4. Supabase migration/schema verification.
5. API/worker/frontend URLs.
6. Test results.
7. Cloud smoke/E2E matrix.
8. Các job/run IDs.
9. Lỗi còn lại.
10. `GO`, `CONDITIONAL` hoặc `NO-GO`.
11. Hướng rollback chính xác.

Không tuyên bố GO nếu chỉ health check pass nhưng chưa chạy worker workflow.
