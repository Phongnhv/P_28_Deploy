# Kịch bản Video Demo — RidePulse DQ · Gate 2 MVP

**Thời lượng chuẩn:** ~180 giây (3 phút)
**Ngôn ngữ thuyết minh:** Tiếng Việt
**Tài khoản demo sử dụng:** `steward / steward`
**URL local:** `http://127.0.0.1:5173`

---

## Chuẩn bị trước khi quay

- [ ] API backend đang chạy: `.\.venv\Scripts\python.exe -m uvicorn src.main:app --host 127.0.0.1 --port 8000`
- [ ] Frontend đang chạy: `npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173 --strictPort`
- [ ] Dataset `yellow_tripdata` đã ở trạng thái `PROFILE_READY` (50k rows ingested)
- [ ] Terminal 2 mở sẵn backend logs ở background, font >= 14px
- [ ] Man hinh resolution: 1920x1080, microphone thu am, tat thong bao he thong
- [ ] `AGENT_MODE=graph` va `OPENAI_API_KEY` da set trong `.env`

---

## PHẦN 1 — Hook & Core Problem (~30 giây · 00:00 – 00:30)

| Timestamp | Duration | Visual (Man hinh & Goc quay) | Voiceover (Loi thuyet minh) | Action / Input Data |
|---|---|---|---|---|
| 00:00 | 5s | **Man hinh Login** `http://127.0.0.1:5173`. Focus panel trai: logo **RP · RidePulse DQ**, tagline *"Turn data signals into trusted decisions."*, 3 metrics lon: **50k registered rows · 5 typed rule templates · 100% audit visibility**. Con tro dung yen. | *"Ban dang quan ly mot dataset 50 nghin chuyen taxi New York. Moi ngay, du lieu co the bi loi ma khong ai hay biet — cuoc am, thoi gian don sau thoi gian tra, gia tri null tran lan. Phat hien thu cong? Khong tuong."* | Man hinh login tinh — camera zoom vao 3 metric stats o panel trai. |
| 00:05 | 8s | Van man hinh Login. Pan nhe sang form login ben phai — field username placeholder: *"user, steward, or admin"*. | *"RidePulse DQ la giai phap AI Agent tu dong phan tich du lieu, de xuat luat kiem tra dua tren bang chung thuc te — va chi chay nhung luat ma Data Steward da phe duyet."* | Khong thao tac. |
| 00:13 | 7s | Go username/password vao form login. Click nut **"Open workspace →"**. Transition sang Dashboard `overview`. | *"Hay dang nhap voi vai tro Data Steward."* | Go username = `steward`, password = `steward`. Click **"Open workspace"** (POST `/api/v1/session`). |
| 00:20 | 10s | **Dashboard Overview** load xong. Focus vao: ten dataset *"NYC Yellow Taxi Trip Records"*, badge xanh `PROFILE_READY`, DQ Health Score card: `92.29 · Grade B`. | *"Workspace hien ra ngay lap tuc. Dataset NYC Yellow Taxi voi 50 nghin dong da san sang. Score DQ hien tai la 92.29 diem — hang B. He thong da phat hien mot so van de dang chu y."* | Khong thao tac — man hinh tu dong load tu `GET /api/v1/datasets` va profile. |

---

## PHẦN 2 — Happy Path: Proposal → Review → Run (~90 giây · 00:30 – 02:00)

### 2A — AI Proposal Generation (00:30 – 01:05)

| Timestamp | Duration | Visual | Voiceover | Action / Input Data |
|---|---|---|---|---|
| 00:30 | 5s | Tab **"Overview"** — focus vao bang profile: 19 cot, 50.000 rows, thong ke cot `fare_amount`: null_rate=0%, range=[−62.5, 400.0], p5=5.0, p95=52.0. | *"He thong da tu dong profile toan bo dataset. Day la thong ke aggregate — khong phai du lieu tho."* | Hover vao metric `fare_amount` de tooltip hien min=−62.5, max=400.0. |
| 00:35 | 5s | Click tab **"Rules"** tren thanh navigation. | *"Bay gio toi yeu cau AI de xuat luat dua tren bang chung nay."* | Click tab **"Rules"**. |
| 00:40 | 8s | Click nut **"Request proposals"**. **Progress panel** xuat hien: `ACTIVE JOB · Proposal Run`, thanh tien trinh dong, message *"Generating rule proposals via LLM..."*, tien do `60%`. | *"Agent dang phan tich tung cot — gui bang chung aggregate len OpenAI de sinh luat co can cu. Khong co du lieu tho nao duoc truyen den model."* | Click **"Request proposals"** (POST `/api/v1/datasets/yellow_tripdata/rule-proposals`). Doi ProgressPanel render. |
| 00:48 | 7s | Progress panel cap nhat: `100% · SUCCEEDED`. Danh sach proposals xuat hien — 5+ rule cards hien voi badges `PROPOSED`. | *"Xong. OpenAI da tra ve cac luat kiem tra duoc calibrate chinh xac theo phan phoi du lieu thuc."* | Man hinh tu reload (GET `/api/v1/rule-proposals?dataset_id=yellow_tripdata`). |
| 00:55 | 10s | **Zoom vao 2 proposal cu the:** Card `fare_amount · RANGE >= 0.0 AND <= 57.2` — badge `PROPOSED`, AI reasoning: *"fare_amount co gia tri am (−62.5) bat thuong ve nghiep vu. Nguong tren lay tu p95=52.0, mo rong 10% thanh 57.2..."*. Card `payment_type · ACCEPTED_VALUES · Credit card / Cash / Flex Fare trip / Dispute / No charge / Invalid Payment`. | *"Nhin vao day. Voi cot fare_amount, AI de xuat RANGE tu 0.0 den 57.2 — lay tu p95 cua du lieu, mo rong 10% theo huong dan nghiep vu. Voi payment_type, AI liet ke chinh xac 6 gia tri hop le tu du lieu thuc."* | Hover lan luot vao 2 proposal card de expand AI reasoning text. |

### 2B — Human-in-the-Loop Review (01:05 – 01:25)

| Timestamp | Duration | Visual | Voiceover | Action / Input Data |
|---|---|---|---|---|
| 01:05 | 8s | Focus vao proposal `fare_amount · RANGE`. Click **"Approve"**. Badge chuyen xanh la `APPROVED`. | *"Toi — voi tu cach Data Steward — phe duyet luat nay. Quyet dinh duoc ghi nhan tuc thi vao audit log."* | Click **"Approve"** → PATCH `/api/v1/rule-proposals/{id}` body `{"status":"APPROVED"}`. |
| 01:13 | 5s | Click **"Approve"** proposal `payment_type · ACCEPTED_VALUES`. | *"Phe duyet tiep luat gia tri hop le cho phuong thuc thanh toan."* | Click **"Approve"** → PATCH tuong tu. Badge chuyen `APPROVED`. |
| 01:18 | 7s | Quick scroll — approve them `vendor_id · NOT_NULL` (badge xanh), reject `passenger_count · NULL_RATE` (badge do `REJECTED`). | *"Toi co the approve, chinh sua hoac tu choi tung luat. Chi luat duoc duyet moi duoc phep chay — khong co ngoai le."* | Click **"Approve"** cho `vendor_id.NOT_NULL`. Click **"Reject"** cho `passenger_count.NULL_RATE`. |

### 2C — Execute DQ Run & Results (01:25 – 02:00)

| Timestamp | Duration | Visual | Voiceover | Action / Input Data |
|---|---|---|---|---|
| 01:25 | 5s | Click **"Run DQ checks"**. Progress panel hien `ACTIVE JOB · DQ Run · RUNNING`. | *"Chay tat ca luat da duyet tren toan bo 50 nghin dong."* | Click **"Run DQ checks"** → POST `/api/v1/dq-runs` voi `approved_rule_ids`. |
| 01:30 | 10s | Progress: `100% · SUCCEEDED`. Tab **"Runs"** mo, bang ket qua render. | *"Ket qua ve ngay — runner hoat dong read-only, khong the ghi de du lieu goc."* | Man hinh chuyen sang Runs view, load tu `GET /api/v1/dq-runs/{run_id}/results`. |
| 01:40 | 10s | **Zoom bang Results:** Row `fare_amount · RANGE` — status `FAILED` (mau do), violation_rate=`9.11%`, failed_ids=`["row-00027", "row-00036", ...]`. Row `vendor_id · NOT_NULL` — status `PASSED` (mau xanh), violation_rate=`0.0%`. | *"fare_amount bi FAIL — 9.11% dong vi pham, tuong duong 4.557 chuyen co cuoc am hoac vuot nguong. He thong tra ve ID cac dong loi cu the. Trong khi do vendor_id hoan toan sach."* | Hover vao row `fare_amount FAILED` — tooltip hien: `row-00027, row-00036, row-00051...` |
| 01:50 | 10s | Scroll xuong **"Anomalies"**. Card: `fare_amount · HIGH_VIOLATION_RATE · 9.11% >= threshold 5%`. DQ Score card: `92.29 / 100 · Grade B`. | *"Co che Anomaly Detection kich hoat — 9.11% vuot nguong 5% ngay lan chay dau tien, phan loai HIGH_VIOLATION_RATE. Data Steward nhan canh bao tuc thi."* | Hover vao anomaly card de tooltip hien chi tiet. |

---

## PHẦN 3 — Minh chứng Real LLM / Live Execution (~45 giây · 02:00 – 02:45)

| Timestamp | Duration | Visual | Voiceover | Action / Input Data |
|---|---|---|---|---|
| 02:00 | 5s | **Alt-Tab** sang Terminal backend (`uvicorn`). Zoom font lon. Scroll len tim log cua lan proposal vua chay. | *"Nhin vao logs server de xac nhan day la LLM that — khong phai hardcode hay mock."* | Alt-Tab sang Terminal 1. Scroll len tim log block cua proposal run. |
| 02:05 | 15s | **Terminal highlight 3 dong log key** (scroll cham, dung lai tung dong): `INFO: rule_proposer_node: Sending structured prompt to OpenAI gpt-4o-mini | table=yellow_tripdata | columns=19` -> `INFO: rule_proposer_node: OpenAI response received | tokens_used=1847 | latency_ms=3241` -> `INFO: rule_proposer_node: Proposed 7 rules for table yellow_tripdata` | *"Day: he thong goi OpenAI gpt-4o-mini voi digest 19 cot — LLM tra ve sau 3.2 giay, tong 1847 token. Tat ca la inference that. Khong co gia tri nao duoc hardcode trong source code."* | Dung mouse highlight tung dong bang cach keo chon. Dung 3–4s moi dong. |
| 02:20 | 10s | **Alt-Tab ve Frontend**. Click tab **"Audit"**. Timeline hien: `[06:42] steward approved fare_amount.RANGE`, `[06:43] steward approved payment_type.ACCEPTED_VALUES`, `[06:44] steward rejected passenger_count.NULL_RATE`, `[06:45] DQ Run SUCCEEDED | run_id=2fc703913f5f`. | *"Toan bo hanh dong cua Steward ghi vao Audit Log bat bien — thoi diem phe duyet, tu choi va ket qua run. Trach nhiem giai trinh 100%."* | Alt-Tab ve browser. Click tab **"Audit"** → GET `/api/v1/audit-logs`. |
| 02:30 | 15s | Quay ve tab **"Overview"**. Focus vao DQ Score card: `92.29 / 100 · Grade B`. Hover xem breakdown: `VALIDITY: 87.5%`, `COMPLETENESS: 100%`, `UNIQUENESS: 100%`. | *"Diem DQ tong the: 92.29, hang B. Van de chinh o chieu Validity — cot fare_amount can lam sach. Data team gio co bang chung dinh luong cu the de uu tien xu ly."* | Click tab **"Overview"**. Hover vao score card xem dimension breakdown. |

---

## PHẦN 4 — Kết thúc & Call to Action (~15 giây · 02:45 – 03:00)

| Timestamp | Duration | Visual | Voiceover | Action / Input Data |
|---|---|---|---|---|
| 02:45 | 10s | **Split-screen:** trai = Dashboard DQ Score `92.29 · Grade B`, phai = Terminal log `OpenAI response received | latency_ms=3241`. Overlay text nho goc duoi: *"Gate 2 · Course Project Simulation"*. | *"RidePulse DQ Gate 2 MVP: AI Agent tu dong profile du lieu, LLM de xuat luat co can cu, Steward phe duyet va chay — toan bo co audit trail. Buoc tiep theo: deploy len Vercel va Google Cloud Run voi dataset Supabase thuc te."* | Man hinh tinh — khong thao tac. |
| 02:55 | 5s | Fade out ve man hinh Login. Logo **RP · RidePulse DQ** lon o giua. Tagline *"Turn data signals into trusted decisions."* Dung hinh. | *"RidePulse DQ — bien tin hieu du lieu thanh quyet dinh dang tin."* | Fade transition. End. |

---

## Phụ lục A — Dữ liệu thực tế dùng trong video

> Trich xuat tu `docs/EVAL_EVIDENCES.md` — phien chay thuc te da hoan thanh.

| Field | Gia tri thuc |
|---|---|
| Proposal Run ID | `98883a6adbbe4820ac79301e40f7e998` |
| Test Run ID | `2fc703913f5f4e64930d329d9f1aafc0` |
| DQ Health Score | `92.29 / 100 · Grade B` |
| Dataset | NYC Yellow Taxi Trip Records — 50,000 rows |
| LLM Model | `gpt-4o-mini` (config.py:39 `openai_model_name`) |

### Ket qua 5 rules thuc te (E1–E5)

| Rule | Type | Ket qua thuc |
|---|---|---|
| `fare_amount · RANGE [0.0, 57.2]` | `numeric_range` | **FAILED** — 9.11% vi pham (4,557 rows), failed_ids: `row-00027`, `row-00036` |
| `vendor_id · NOT_NULL` | `not_null` | **PASSED** — 0 vi pham (0.0%) |
| `payment_type · ACCEPTED_VALUES` | `accepted_values` | **PASSED** — 6 gia tri: Credit card, Cash, Flex Fare trip, Dispute, No charge, Invalid Payment |
| `pickup_at <= dropoff_at` | `cross_field_comparison` | **PASSED** — 0 vi pham |
| `source_row_id · UNIQUE` | `duplicate_fingerprint` | **PASSED** — 0 vi pham |

---

## Phu luc B — API Endpoints goi trong video

```
POST   /api/v1/session
GET    /api/v1/datasets
GET    /api/v1/datasets/yellow_tripdata/profile
POST   /api/v1/datasets/yellow_tripdata/rule-proposals
GET    /api/v1/jobs/{job_id}
GET    /api/v1/rule-proposals?dataset_id=yellow_tripdata
PATCH  /api/v1/rule-proposals/{proposal_id}
POST   /api/v1/dq-runs
GET    /api/v1/dq-runs/{run_id}/results
GET    /api/v1/audit-logs
```

---

## Phu luc C — Server Log Lines dung o Phan 3

```log
INFO:     rule_proposer_node: Sending structured prompt to OpenAI gpt-4o-mini | table=yellow_tripdata | columns=19
INFO:     rule_proposer_node: OpenAI response received | tokens_used=1847 | latency_ms=3241
INFO:     rule_proposer_node: Proposed 7 rules for table yellow_tripdata
INFO:     test_runner_node: Running 5 approved rules on dataset yellow_tripdata (50000 rows)
INFO:     persist_report_node: DQ Run SUCCEEDED | test_run_id=2fc703913f5f4e64930d329d9f1aafc0 | score=92.29
```
