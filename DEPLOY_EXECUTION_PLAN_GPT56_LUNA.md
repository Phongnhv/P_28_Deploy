# RidePulse DQ — Kế hoạch hoàn thiện blocker, deploy và E2E cloud

Bạn đang làm việc trong repository:

`C:\Users\ADMIN\WorkPlace\Vinuni\AssignmentProject\P-028-deploy-fresh`

- Model: `gpt-5.6-luna`
- Reasoning effort: `high`

## Mục tiêu

Tự động hoàn thiện các blocker cuối, kiểm thử regression, commit và cập nhật `origin/deploy`, triển khai API + worker + frontend lên môi trường cloud hiện có, sau đó kiểm thử E2E trực tiếp trên website production.

Không yêu cầu người dùng tự chạy Google Cloud CLI. Chỉ dừng hỏi người dùng nếu gặp xác thực Vercel/Google hoặc một quyết định destructive không thể rollback.

## Trạng thái cloud đã xác minh

### Google Cloud

- Project ID: `asignmentvinuni`
- Project number: `1119395105`
- Region: `asia-southeast1`
- Tài khoản gcloud hiện tại có `roles/owner`
- Cloud Run API service: `ridepulse-api`
- API URL: `https://ridepulse-api-gbnhdahaya-as.a.run.app`
- Revision hiện tại: `ridepulse-api-00021-zhj`
- API service account: `ridepulse-api-storage@asignmentvinuni.iam.gserviceaccount.com`
- Artifact Registry repository ưu tiên: `ridepulse`
- Registry cũ cũng tồn tại: `cloud-run-source-deploy`
- GCS bucket: `ridepulse-dbt-artifacts-asignmentvinuni`
- Bucket location: `ASIA-SOUTHEAST1`
- Bucket đã cấp `roles/storage.objectUser` cho API service account
- Chưa có Cloud Run Job

### Secret Manager

API hiện tham chiếu:

- `DATABASE_URL` → secret `database-url:latest`
- `SUPABASE_DATABASE_URL` → secret `supabase-database-url:latest`
- `OPENAI_API_KEY` → secret `openai-api-key:1`
- `DEMO_USER_PASSWORD` → `demo-user-password:latest`
- `DEMO_STEWARD_PASSWORD` → `demo-steward-password:latest`
- `DEMO_ADMIN_PASSWORD` → `demo-admin-password:latest`

Hai secret `database-url` và `supabase-database-url` hiện có cùng giá trị/fingerprint. URL local đã E2E là một Supabase khác.

Code ưu tiên:

- `DATABASE_URL` cho control-plane/job/workflow persistence.
- `SUPABASE_DATABASE_URL` cho Supabase source execution.

Vì vậy hai biến production phải luôn trỏ cùng database trong bản deploy này. Không được cập nhật một secret mà bỏ secret còn lại ở database cũ.

### Frontend

- Production URL: `https://c3-app-028.vercel.app`
- Production API origin: `https://ridepulse-api-gbnhdahaya-as.a.run.app`
- Repository local chưa link Vercel CLI.
- Ưu tiên deployment qua Git integration hiện có sau khi push `origin/deploy`.
- Chỉ yêu cầu đăng nhập/link Vercel nếu Git integration không tự deploy.

### Git

- Branch làm việc: `codex/deploy-feature-testing`
- `HEAD` và `origin/deploy` hiện cùng base commit `13f3f41`.
- Các thay đổi mới đang chưa commit.
- `.ai-log/session.jsonl` đang có thay đổi phát sinh từ hook và không được đưa vào commit.

## Bằng chứng local

- Frontend real mode: pass
- Backend + local worker: pass
- Supabase thật: pass
- GCS artifact: pass
- OpenAI thật: HTTP 200
- Graph 1 + semantic continuation: pass
- 24/24 rules approved
- Graph 2/3: `COMPLETED`
- Analysis nodes: 10/10
- Rule results: 23 PASS, 1 FAIL, 0 ERROR
- Reload khôi phục đúng analysis
- Analysis run: `analysis-7e6a8031539f4424b7`
- Full pytest gần nhất: 272 passed, 10 skipped
- Targeted regression gần nhất: 22 passed
- Frontend production build: pass

## Nguyên tắc

- Không reset hoặc xóa dữ liệu Supabase cũ.
- Không xóa secret version cũ.
- Không reset bucket hoặc xóa object hàng loạt.
- Không commit secret, `.env`, API key, database URL hoặc password.
- Không commit `.ai-log/session.jsonl`.
- Không force-push.
- Không tạo project Google Cloud/Supabase/Vercel mới.
- Không thêm scheduler, reconciler, dead-letter queue hoặc authorization mở rộng trong vòng deploy này.
- Dùng immutable image tag/digest theo commit SHA.
- API và worker phải dùng cùng image digest và cùng database target.
- Luôn giữ revision/image/secret version cũ để rollback.

## Phase 1 — Hoàn thiện blocker code

### 1. Cloud Run SDK

`src/services/gcp_run.py` dùng `google.cloud.run_v2`, nhưng `requirements.txt` chưa có dependency tương ứng.

Thực hiện:

- Thêm package Cloud Run SDK chính thức tương thích với Python hiện tại vào `requirements.txt`.
- Cài dependency.
- Chạy:

```bash
python -c "from google.cloud import run_v2; print('cloud-run-import-ok')"
```

- Bổ sung Docker smoke test để image production import được `google.cloud.run_v2`.

### 2. Worker async entrypoint

Xác minh và giữ regression fix trong `src/worker.py`:

- Chỉ có một top-level `asyncio.run(main())`.
- Không gọi lồng `asyncio.run()` khi event loop đang chạy.
- Canonical async workflow phải được `await`.
- Worker đọc `RUN_JOB_ID` và `RUN_JOB_TYPE`.
- Worker reload durable job/entity từ Supabase.
- Canonical job không fallback sang taxi legacy path.
- Job terminal không chạy lại.

Bổ sung test trực tiếp cho worker entrypoint nếu fix hiện chỉ được E2E chứng minh.

### 3. Namespace idempotency tối thiểu

Thay canonical import key global bằng key có workspace:

```text
versioned-import-{workspace_id}-{idempotency_key}
```

Không mở rộng authorization model trong vòng này.

Test:

- Cùng workspace + cùng key + cùng payload → replay.
- Cùng workspace + cùng key + payload khác → 409.
- Hai workspace dùng cùng client key → không collision.

### 4. Anomaly report consistency

Sửa mâu thuẫn:

- Decision canonical: `INSUFFICIENT_HISTORY`.
- Report hiện có đoạn gọi conclusion là `NORMAL`.

Yêu cầu:

- Report sử dụng đúng decision canonical.
- `INSUFFICIENT_HISTORY` phải được mô tả là chưa đủ lịch sử, không phải `NORMAL`.
- Không đổi detector nếu detector đang đúng.
- Bổ sung test cho `NORMAL`, `ANOMALY` và `INSUFFICIENT_HISTORY`.

## Phase 2 — Chốt Supabase target

Mục tiêu là quyết định giữa:

- Database cloud cũ đang phục vụ revision `00021-zhj`.
- Database local mới đã pass E2E và có schema mới.

Thực hiện hoàn toàn read-only trước:

1. Kiểm tra migration/schema version của cả hai database.
2. So sánh các bảng/cột/index canonical:
   - `jobs`
   - `dataset_versions`
   - `profile_runs`
   - `graph1_runs`
   - `analysis_runs`
   - `governed_artifacts`
   - `governance_audit_events`
   - `lease_expires_at`
   - `attempt_count`
3. Kiểm tra database cloud cũ có dữ liệu cần giữ không.
4. Không in connection string hoặc password.

Quyết định mặc định cho project/demo:

- Nếu database local E2E có schema đầy đủ và cloud cũ chỉ là dữ liệu demo có thể giữ làm rollback, chọn database local E2E làm target mới.
- Nếu cloud cũ chứa dữ liệu cần tiếp tục sử dụng, giữ cloud cũ và chỉ áp dụng migration additive đã review.
- Nếu không thể xác định an toàn, dừng trước khi đổi secret và báo người dùng.

Nếu chuyển sang database local E2E:

1. Ghi lại secret version cũ của cả `database-url` và `supabase-database-url`.
2. Tạo version mới cho cả hai secret bằng cùng một URL target.
3. Không disable/destroy version cũ.
4. Chỉ deploy revision mới sau khi hai secret đã đồng bộ.
5. Rollback bằng cách tạo revision trỏ lại version cũ hoặc tạo secret version khôi phục.

Không xóa database Supabase cũ.

## Phase 3 — Regression và image smoke

Chạy lại sau mọi sửa đổi:

```text
python -m ruff check <src, tests và script đã thay đổi>
python -m pytest -q
cd frontend && npm run build
docker build
```

Trong container/image phải kiểm tra:

```text
import src.main
import src.worker
from google.cloud import run_v2
```

Điều kiện pass:

- Full pytest pass.
- Frontend build pass.
- Ruff changed-source pass.
- Docker build pass.
- Không có import error.
- Không có secret trong build output/image metadata.

## Phase 4 — Commit và cập nhật origin/deploy

1. Kiểm tra `git diff --check`.
2. Kiểm tra diff không chứa secret.
3. Không stage `.ai-log/session.jsonl`.
4. Không stage `.env`, recording, `node_modules`, frontend `dist` hoặc test artifacts.
5. Commit source, docs và tests hợp lệ.

Commit message gợi ý:

```text
fix: prepare durable workflows for cloud deployment
```

6. Fetch `origin/deploy` lần cuối.
7. Nếu vẫn fast-forward, push:

```bash
git push origin HEAD:deploy
```

8. Không force-push.
9. Ghi lại commit SHA.

## Phase 5 — Build và push immutable image

Sử dụng Artifact Registry repository:

```text
asia-southeast1-docker.pkg.dev/asignmentvinuni/ridepulse
```

Build image theo commit SHA:

```text
asia-southeast1-docker.pkg.dev/asignmentvinuni/ridepulse/ridepulse:<commit-sha>
```

Thực hiện:

- Authenticate Docker với Artifact Registry.
- Build image từ đúng commit đã test.
- Push image.
- Lấy image digest.
- Dùng cùng digest cho API và worker.

## Phase 6 — Tạo Cloud Run Job worker

Tạo service account riêng nếu chưa tồn tại:

```text
ridepulse-worker@asignmentvinuni.iam.gserviceaccount.com
```

Cấp quyền tối thiểu cần thiết:

- Đọc các Secret Manager secret được worker sử dụng.
- `roles/storage.objectUser` trên bucket `ridepulse-dbt-artifacts-asignmentvinuni`.
- Không cấp Owner/Editor cho worker.

Tạo Cloud Run Job:

- Name: `ridepulse-worker`
- Project: `asignmentvinuni`
- Region: `asia-southeast1`
- Image: immutable digest vừa push
- Command: Python executable
- Args: `-m`, `src.worker`
- Task count: 1
- Parallelism: 1
- Max retries: 1 hoặc 2
- Timeout đủ cho Telco Graph 1/2/3 và report LLM
- Memory/CPU đủ cho pandas
- Service account: worker service account

Gắn các biến/secrets giống API ở mức cần thiết:

- `APP_ENV=production`
- `PROVIDER=openai`
- `AGENT_MODE=graph`
- `DQ_EXECUTION_BACKEND=supabase`
- `DATABASE_URL` từ đúng secret/version target
- `SUPABASE_DATABASE_URL` từ đúng secret/version target
- `OPENAI_API_KEY`
- `DEMO_USER_PASSWORD`
- `DEMO_STEWARD_PASSWORD`
- `DEMO_ADMIN_PASSWORD`
- `OBJECT_STORAGE_PROVIDER=gcs`
- `OBJECT_STORAGE_BUCKET=ridepulse-dbt-artifacts-asignmentvinuni`
- `OBJECT_STORAGE_PREFIX=dbt-tests`

Cấp cho API service account quyền chạy riêng Job này bằng IAM binding hẹp nhất mà Cloud Run hỗ trợ. Không cấp quyền project-wide rộng nếu job-level binding hoạt động.

Smoke worker trước khi deploy API mới:

- Xác nhận Job resource READY.
- Chạy một execution chỉ khi có durable smoke job hợp lệ hoặc worker hỗ trợ dry-run an toàn.
- Nếu chưa có job ID hợp lệ, kiểm tra container command/startup mà không tạo dữ liệu giả mạo.
- Xác nhận không có import error, permission error hoặc secret error.

## Phase 7 — Deploy Cloud Run API revision mới

Ghi lại revision/image hiện tại để rollback:

- Revision: `ridepulse-api-00021-zhj`
- Current image digest phải được đọc và lưu trong báo cáo deploy.

Update `ridepulse-api` bằng cùng immutable image digest với worker.

Giữ cấu hình hiện tại và bổ sung:

- `GOOGLE_CLOUD_PROJECT=asignmentvinuni`
- `GOOGLE_CLOUD_REGION=asia-southeast1`
- `CLOUD_RUN_JOB_NAME=ridepulse-worker`

Đảm bảo:

- `DATABASE_URL` và `SUPABASE_DATABASE_URL` trỏ cùng target database.
- `FRONTEND_ORIGIN=https://c3-app-028.vercel.app`.
- Không đặt `WORKER_DISPATCH_MODE=local`.
- Không đặt `LOCAL_WORKER_URL`.
- API service account vẫn là account hiện tại, trừ khi có lý do rõ ràng để đổi.

Deploy revision không chuyển traffic ngay nếu CLI hỗ trợ `--no-traffic`.

Trước khi chuyển traffic:

- Revision READY.
- `/api/v1/status` trả 200.
- Startup logs không có schema/secret/import error.
- API có thể dispatch một smoke job sang `ridepulse-worker`.

Sau smoke pass mới chuyển traffic sang revision mới.

## Phase 8 — Frontend deploy

Frontend production phải dùng:

```text
VITE_USE_MOCK_API=false
VITE_API_BASE_URL=https://ridepulse-api-gbnhdahaya-as.a.run.app
VITE_WORKSPACE_ID=<workspace production hiện có>
```

Ưu tiên Git integration:

1. Sau khi push `origin/deploy`, kiểm tra Vercel deployment mới có được trigger không.
2. Nếu có, chờ deployment READY.
3. Nếu không, mới cài/login/link Vercel CLI hoặc hướng dẫn người dùng xác thực.
4. Không tạo Vercel project mới.

URL kiểm thử:

`https://c3-app-028.vercel.app`

## Phase 9 — Cloud smoke test

Dùng browser automation và tạo recording mới.

Trước full E2E, chạy dataset nhỏ có prefix:

```text
cloud-smoke-<timestamp>
```

Kiểm tra:

1. Frontend tải được.
2. Login thành công.
3. Không có CORS/cookie/CSRF error.
4. Upload tạo durable `INGEST_PROFILE` job.
5. API tạo Cloud Run Job execution.
6. Worker hoàn tất profiling.
7. UI cập nhật `SUCCEEDED`.
8. Refresh vẫn thấy dataset/profile.
9. Upload lại cùng file replay, không HTTP 500 và không tạo object GCS thừa.
10. Graph 1 đến semantic gate.
11. Semantic continuation chạy qua worker.
12. Approve rules.
13. Graph 2/3 hoàn tất.
14. Report tồn tại trong DB, `governed_artifacts` và GCS.
15. Reload khôi phục analysis.

Nếu smoke dataset nhỏ fail, không chạy Telco.

## Phase 10 — Cloud E2E Telco

Chạy trên `https://c3-app-028.vercel.app`:

```text
login
→ upload/replay Telco
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
- Không cross-field TypeError
- Không execution ERROR
- Analysis 10/10 nodes
- Report decision nhất quán với anomaly decision
- Report artifact tồn tại trên GCS
- OpenAI call thành công
- UI không báo mất API
- Không có durable job treo trong luồng bình thường

Thu thập:

- Commit SHA
- Image digest
- API revision
- Worker Job execution IDs
- Dataset/version/profile/Graph1/analysis IDs
- Browser recording
- Network/console errors
- API/worker logs đã redact

## Rollback

Nếu smoke hoặc E2E fail:

1. Dừng tạo workflow mới.
2. Chuyển Cloud Run traffic về revision `ridepulse-api-00021-zhj` hoặc revision cũ đã ghi lại.
3. Roll worker về image digest cũ hoặc tạm ngừng dispatcher mới.
4. Roll frontend về deployment Vercel trước đó nếu frontend mới gây lỗi.
5. Nếu đã đổi database target, khôi phục cả `DATABASE_URL` và `SUPABASE_DATABASE_URL` về cùng secret version cũ.
6. Không xóa database mới/cũ.
7. Không rollback migration bằng SQL destructive tự phát.
8. Giữ run/job lỗi để chẩn đoán.

## Điều kiện GO

Chỉ kết luận GO khi:

- Full regression pass trên commit deploy.
- Docker image import được Cloud Run SDK.
- `ridepulse-worker` chạy đúng `src.worker`.
- API dispatch Job thành công.
- API và worker dùng cùng image digest.
- API và worker dùng cùng database target.
- Cloud smoke dataset nhỏ pass.
- Telco E2E pass.
- Duplicate upload không tạo orphan.
- Cross-field không ERROR.
- Report/anomaly decision nhất quán.
- Report tồn tại DB + governed artifact + GCS.
- Refresh/rerun hoạt động.
- Không có secret trong log.
- Có rollback revision, image và secret version rõ ràng.

## Báo cáo cuối

Trả về:

1. Blocker đã sửa.
2. Supabase target đã chọn và lý do, không lộ URL/password.
3. Secret versions mới/cũ dùng cho rollback.
4. Commit SHA và image digest.
5. IAM/service account/job đã tạo hoặc cập nhật.
6. API revision và URL.
7. Frontend deployment và URL.
8. Regression results.
9. Cloud smoke/E2E matrix.
10. Dataset/run/job IDs.
11. Lỗi hoặc rủi ro còn lại.
12. `GO`, `CONDITIONAL` hoặc `NO-GO`.

Không tuyên bố GO nếu chỉ health endpoint pass nhưng worker workflow chưa chạy end-to-end.
