# Plan: HITL Rule Review (DB-backed) cho RidePulse DQ

> **Trạng thái:** Đã chốt lại kiến trúc — bỏ checkpointer, DB là source of truth.
> **Tiền đề:** Rule Proposer đã hoàn thành (`docs/plan/rule_proposer_agent.md`).
> **Lưu ý:** Plan này thay thế bản state-backed trước đó và phần "Part E — HITL (DB-backed)" của `rule_proposer_agent.md:172-251`.

## Context

`rule_proposer_node` đã chạy xong và sinh ra `state["proposed_rules"]` — list các dict rule đã được stamp (`src/agents/nodes/rule_proposer_node.py:143-177`). Artifact `data/results/debug_proposed_rules_*.json` là **trace debug**, không phải nguồn dữ liệu.

Bước còn thiếu là HITL: Data Steward phải `Approve` / `Reject` / `Edit` từng rule trước khi rule được đưa sang Test Generator (ràng buộc bắt buộc của đề tài — `DETAI.md:9`).

**Mục tiêu:** Steward review qua REST API. `state["proposed_rules"]` vẫn là shape làm việc **trong Run 1**; ranh giới bền vững giữa Run 1 và Run 2 là **SQLite**. Run 2 load ngược APPROVED rules vào `state["approved_rules"]` của một graph mới.

---

## Vì sao bỏ checkpointer

Bản plan trước chọn LangGraph checkpointer làm nơi state sống qua nhiều HTTP request. Ba lý do độc lập khiến hướng đó sai:

**1. Hai graph khác nhau, không dùng chung thread được.**
`aupdate_state(..., as_node="hitl_gate")` đòi node đó phải tồn tại trong graph đang gọi. `build_proposal_graph` có tập node `raw_profiler / profiler_digest / rule_proposer / hitl_gate`; `build_execution_graph` (`graph.py:82-89`, hiện là stub) sẽ có `test_generator / test_runner / anomaly` — giao nhau bằng 0. Checkpoint lưu `channel_versions` theo topology của một graph cụ thể, nên resume cùng `thread_id` bằng graph khác là hành vi không đảm bảo.

Chính xác hơn: bản plan cũ gọi `aupdate_state` trên *proposal* graph nên không vỡ ngay. Khiếm khuyết thật là **coupling**: tầng REST phải boot một compiled LLM graph chỉ để trả về một list, và mọi lần đổi topology `build_proposal_graph` (thêm/bớt node) sẽ làm checkpoint của các run cũ thành stale.

**2. Write amplification.**
Checkpoint lưu **toàn bộ** `AgentState`, gồm cả `dataset_profile` và `dataset_profile_digest` (`state.py:20,30`). Đo trên artifact thật của dataset 1 bảng:

| Thành phần | Kích thước |
|---|---|
| `dataset_profile` | 18 KB |
| `dataset_profile_digest` | 10 KB |
| `proposed_rules` | 35 KB |
| **Mỗi checkpoint write** | **≈ 64 KB** |

Approve 50 rule = 50 bản copy full state ≈ **3.2 MB** cho một run. Con số này scale theo số bảng — dataset nhiều bảng sẽ tệ hơn nhiều lần.

**3. Không query được.**
Lọc theo `status` / `dimension` phải load hết state rồi filter bằng Python, không index. Với DB thì đây là `WHERE` + index, và `review-summary` là một câu `GROUP BY`.

**Hệ quả tốt:** bỏ hẳn dependency `langgraph-checkpoint-sqlite` + `aiosqlite`. `requirements.txt` đã có `sqlalchemy>=2.0.0` (dòng 16) — **không cần thêm gì**. `main.py:13-14` cũng đã gọi `init_db()` trong lifespan, nên **`main.py` không cần sửa**.

---

## ⚠ Cửa sổ migration đang mở

`data/app.db` **chưa tồn tại** (chỉ có `data/yellow_tripdata.db`). Bảng `proposed_rules` chưa từng được tạo → sửa schema `rule_store.py` lúc này **không cần migration**, `create_all` sẽ tạo bảng mới đúng luôn.

Cửa sổ này đóng lại ngay khi chạy `uvicorn` lần đầu. **Làm bước schema (mục 3) trước khi boot server.**

---

## 🔴 Bug chặn đường: `run_id` bị ghi đè

Phát hiện khi rà lại code — bug này làm **toàn bộ HITL flow trả rỗng**, độc lập với chuyện chọn checkpointer hay DB:

- `routes.py:102` sinh `run_id = uuid4().hex`, gọi `create_run(run_id, ...)` (ghi vào `proposal_runs`), rồi truyền `state["rule_run_id"] = run_id`. Đây là id **client nhận được**.
- `rule_proposer_node.py:239` lại sinh `run_id = uuid.uuid4().hex` **vô điều kiện** và return `{"rule_run_id": run_id}`. LangGraph merge dict này vào state → `rule_run_id` **bị ghi đè** bằng uuid khác.
- `persist_rules_node` (`rule_proposer_node.py:281`) đọc `state["rule_run_id"]` — tức id đã bị clobber.

Kết quả: `proposed_rules.run_id` ≠ `proposal_runs.run_id`. Client poll `/dq/runs/{run_id}` thấy `DONE`, nhưng `GET /dq/runs/{run_id}/rules` trả `[]` vĩnh viễn.

**Fix** tại `rule_proposer_node.py:239`:

```python
run_id = state.get("rule_run_id") or uuid.uuid4().hex
```

Giữ nhánh `or` để debug harness (`rule_proposer_node.py:333-337`, state không có `rule_run_id`) vẫn chạy được.

---

## Quyết định kiến trúc

| Vấn đề | Quyết định |
|---|---|
| Source of truth | Bảng SQLite `proposed_rules` |
| Shape trong Run 1 | `state["proposed_rules"]` — không đổi |
| Định danh rule | PK ghép `(run_id, rule_id)`; `rule_id` là string, unique trong 1 run |
| File JSON | Chỉ là trace debug, ghi bởi `hitl_gate_node` |
| HITL trong graph | `hitl_gate_node` persist + ghi trace rồi `END`. **Không** dùng `interrupt()` |
| Steward review | REST thuần → SQLAlchemy. Không cần graph object |
| Concurrency | Transaction của DB. **Không** cần `asyncio.Lock` |
| Run metadata | Giữ nguyên bảng `proposal_runs` (đang chạy tốt) |
| Handoff Run 1 → Run 2 | `SELECT ... WHERE run_id=? AND status='APPROVED'` → nạp vào `state["approved_rules"]` của graph mới |

**Vì sao DB thay vì state:** ranh giới bền vững phải là thứ tồn tại độc lập với topology của bất kỳ graph nào. `rule_store.py` đã viết sẵn ~80% (`ProposedRuleModel`, `effective_parameters`, `bulk_review`, WAL mode) — chỉ cần vá schema và đổi khoá.

---

## Luồng tổng thể

```
Run 1: POST /api/v1/dq/propose
  raw_profiler → profiler_digest → rule_proposer → hitl_gate → END
                                                        │
                                    INSERT vào proposed_rules (status=PENDING)
                                    + trace data/results/proposed_rules_{run_id}.json
                                                        │
Steward review (REST → SQLAlchemy, không dính graph)
  GET   /dq/runs/{run_id}/rules?status=PENDING&dimension=COMPLETENESS
  PATCH /dq/runs/{run_id}/rules/{rule_id}      approve / reject / edit 1 rule
  POST  /dq/runs/{run_id}/rules/bulk-review    checkbox nhiều rule
  GET   /dq/runs/{run_id}/review-summary       badge tiến độ cho UI
                                                        │
Run 2: POST /dq/runs/{run_id}/generate-tests  (vẫn stub 501)
  get_approved_rules(run_id) → state["approved_rules"] của execution graph
```

---

## Shape của 1 rule

`_stamp_rule` sinh phần AI; HITL bổ sung 6 field review. **`parameters` bất biến** (audit trail), Steward sửa vào `edited_parameters`.

```jsonc
{
  // --- AI sinh ra (immutable) ---
  "rule_id": "yellow_tripdata.vendor_id.NOT_NULL",
  "run_id": "4894eee57a29492abc0e0a3a619a6b8d",
  "dataset_id": "yellow_tripdata",
  "table_name": "yellow_tripdata",
  "column": "vendor_id",
  "rule_type": "NOT_NULL",
  "parameters": {},
  "confidence_score": 1.0,
  "severity": "CRITICAL",          // Steward được phép override
  "dimension": "COMPLETENESS",     // cần THÊM CỘT
  "rule_description": "Mã nhà cung cấp thiết bị (VendorID) phải luôn có giá trị…",
  "ai_reasoning": "Digest cho thấy null_pct = 0.0…",

  // --- HITL ---
  "status": "PENDING",             // PENDING | APPROVED | REJECTED
  "edited_parameters": null,       // Steward override, null = dùng nguyên bản AI
  "reviewer": null,
  "review_note": null,             // lý do reject — bắt buộc khi REJECTED
  "reviewed_at": null,             // ISO-8601 UTC
  "created_at": "2026-08-09T17:52:30+00:00"
}
```

`effective_parameters` **không lưu cột riêng** — là property tính lúc đọc (`rule_store.py:89-96`, đã có). Đây là field duy nhất Test Generator cần đọc.

---

## Các file cần sửa / tạo

### 1. `src/models/rule_schemas.py` — thêm enum status

Thêm cạnh `Severity` (`rule_schemas.py:41`), tái dùng style `str, Enum` sẵn có:

```python
class RuleStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
```

### 2. `src/agents/nodes/rule_proposer_node.py` — fix run_id, rule_id, status

**a. Fix `run_id` bị ghi đè** tại dòng 239 (xem mục 🔴 phía trên). Đây là thay đổi quan trọng nhất trong file.

**b. Unique hoá `rule_id`** trong `_stamp_rule` (`rule_proposer_node.py:143-177`). Hiện `f"{table_name}.{col_key}.{rule_type}"` (dòng 150) bị trùng nếu LLM đề xuất 2 rule cùng type trên cùng cột — với PK ghép `(run_id, rule_id)` thì trùng key sẽ thành `IntegrityError`.

⚠ **`used_ids` phải là keyword arg có default.** `tests/test_rule_proposer.py:299,321` gọi `_stamp_rule(rule, "orders", "abc123")` bằng 3 positional args và assert `rule_id == "orders.order_id.NOT_NULL"`. Thêm param bắt buộc sẽ làm vỡ 2 test đang xanh:

```python
def _stamp_rule(
    rule: ProposedRule,
    table_name: str,
    run_id: str,
    used_ids: set[str] | None = None,
) -> dict:
```

Nếu trùng thì nối hậu tố `#2`, `#3` — deterministic trong 1 run. `rule_proposer_node` truyền một `set` chung khi loop qua các bảng (`rule_proposer_node.py:243-253`).

**c. Thống nhất status.** `_stamp_rule` đang ghi `"PENDING_REVIEW"` (dòng 176) còn `save_proposed_rules` hardcode `"PENDING"` (`rule_store.py:216`) — hai vocabulary khác nhau cho cùng một trạng thái. Đổi sang `RuleStatus.PENDING.value` và **xoá hẳn chuỗi `PENDING_REVIEW`** khỏi codebase (còn ở dòng 148 docstring và 176).

**d. Thêm field HITL mặc định:** `edited_parameters=None`, `reviewer=None`, `review_note=None`, `reviewed_at=None`, `created_at=<ISO UTC now>`.

### 3. `src/services/rule_store.py` — vá schema + đổi khoá (**làm trước khi boot server**)

**a. `ProposedRuleModel` (`rule_store.py:56-87`) — đổi khoá và thêm 4 cột.**

Bỏ `id: Mapped[int]` autoincrement (dòng 59). Dùng **PK ghép**:

```python
run_id:  Mapped[str] = mapped_column(String(64),  primary_key=True)
rule_id: Mapped[str] = mapped_column(String(512), primary_key=True)
```

`rule_id` chỉ unique trong phạm vi 1 run, nên PK phải gồm cả `run_id`. Điều này map 1-1 với route lồng `/dq/runs/{run_id}/rules/{rule_id}`.

Thêm 4 cột đang thiếu — 3 cột đầu là 3 field vừa thêm cho UI Steward (`rule_schemas.py:78-91`) mà `save_proposed_rules` đang **làm mất**:

```python
dimension:        Mapped[str]           = mapped_column(String(32), nullable=False, index=True)
rule_description: Mapped[str]           = mapped_column(Text, nullable=False)
review_note:      Mapped[Optional[str]] = mapped_column(Text, nullable=True)
```

Đổi `default="PENDING"` (dòng 77) → `default=RuleStatus.PENDING.value`.

Index (`rule_store.py:85-87`): giữ `ix_proposed_rules_run_status`, thêm `Index("ix_proposed_rules_run_dim", "run_id", "dimension")` cho filter theo dimension của UI. Gỡ `index=True` trên `run_id` (dòng 60) vì nó đã là cột đầu của PK.

**b. `to_dict()` (`rule_store.py:98-121`):** bỏ `"id"`, thêm `"rule_id"`, `"dimension"`, `"rule_description"`, `"review_note"`. Đổi key `"column_name"` → **`"column"`** để khớp shape state và schema `ProposedRule`. Giữ tên **cột DB** là `column_name` (tránh nhầm với `sqlalchemy.Column`), chỉ đổi ở boundary serialize.

**c. `save_proposed_rules` (`rule_store.py:196-222`):** đọc thêm `rule_id`, `dimension`, `rule_description`; lấy `status` từ rule thay vì hardcode `"PENDING"` (dòng 216).

**Idempotency:** với PK ghép, chạy lại Run 1 trùng `run_id` sẽ raise `IntegrityError`. Xoá trước khi insert trong cùng transaction:

```python
session.query(ProposedRuleModel).filter_by(run_id=run_id).delete()
```

**d. Các hàm rule-level — đổi khoá từ `int` sang `(run_id, rule_id)`:**

| Hàm | Thay đổi |
|---|---|
| `list_rules` | thêm param `dimension`; `order_by(rule_id)` thay `order_by(id)` (dòng 242) |
| `review_rule` | signature `(run_id, rule_id, status, edited_parameters?, severity?, reviewer?, review_note?)`; `session.get(ProposedRuleModel, (run_id, rule_id))` |
| `bulk_review` | nhận `run_id` + decisions; **trả `(updated, not_found_ids)`** thay vì im lặng bỏ qua id sai (dòng 286-289) |
| `get_approved_rules` | thêm scope `run_id` (đã có) |
| `get_review_summary` | **mới** — `{total, pending, approved, rejected, edited, by_dimension, by_severity, is_complete}` bằng `GROUP BY`, không load hết row |

**e. Validate `edited_parameters` trước khi ghi:** dựng lại `ProposedRule.model_validate({...})` để guardrail `_validate_parameters` (`rule_schemas.py:106-127`) chặn dữ liệu vô lý (vd `RANGE` mà cả `min`/`max` đều `None`). Lỗi → `ValueError`, route trả 422.

**f. Concurrency:** một câu `UPDATE ... WHERE run_id=? AND rule_id=?` là atomic trong transaction — hai Steward PATCH đồng thời 2 rule khác nhau không đụng nhau. **Không cần `asyncio.Lock`.** `bulk_review` chạy trong 1 transaction, commit một lần.

**g. Wrapper async:** giữ pattern `asyncio.to_thread` như routes hiện tại (`routes.py:120,138,151`) vì SQLAlchemy ở đây là sync.

### 4. `src/agents/nodes/hitl_gate_node.py` — file rỗng (0 byte), viết mới

Node mỏng, **không pause graph**:

```python
async def hitl_gate_node(state: AgentState) -> dict:
    """Chốt Run 1: persist rules vào DB, ghi trace JSON, đánh dấu chờ Steward duyệt."""
```

- Đọc `state["proposed_rules"]`, `state["rule_run_id"]`, `state["dataset_id"]`.
- `await asyncio.to_thread(save_proposed_rules, run_id, dataset_id, rules)` — lazy import như `persist_rules_node` (`rule_proposer_node.py:278`). **Lỗi ở bước này phải raise** để `_run_proposal_pipeline` mark run `FAILED`.
- Ghi trace ra `data/results/proposed_rules_{run_id}.json` (`ensure_ascii=False, indent=2` — giống `rule_proposer_node.py:346`). Bọc `try/except` chỉ log warning: **trace hỏng không được làm fail run**.
- Trả `{"metadata": {**metadata, "hitl_status": "AWAITING_REVIEW", "rules_saved": n, "trace_path": str(path)}}`.
- **Không** trả lại `proposed_rules` (tránh ghi đè thừa).

### 5. `src/agents/graph.py` — rewire

Trong `build_proposal_graph()` (`graph.py:36-75`): thay node `persist_rules` bằng `hitl_gate` → `rule_proposer → hitl_gate → END` (sửa dòng 54, 72-73).

**Không** thêm param `checkpointer`, **không** cần singleton graph — API HITL không dùng graph. `build_proposal_graph()` gọi mới mỗi lần trong `_run_proposal_pipeline` (`routes.py:72`) là ổn.

Giữ nguyên `persist_rules_node` trong `rule_proposer_node.py` nhưng gỡ khỏi graph; đánh dấu deprecated trong docstring.

### 6. `src/models/schemas.py` — cập nhật models

- `RuleReviewResponse` (`schemas.py:54-70`): bỏ `id: int` → **`rule_id: str`**; đổi `column_name` → `column`; **thêm `dimension: str`, `rule_description: str`, `review_note: Optional[str]`**.
- `RuleUpdateRequest` (`schemas.py:77`): `status: Literal["APPROVED", "REJECTED"]` (hiện là `str` trần → typo lọt xuống store); thêm `review_note: Optional[str]`.
- `BulkDecision` (`schemas.py:96`): `rule_id: int` → `rule_id: str`; `status` cũng `Literal`.
- `BulkReviewResponse`: thêm `not_found: list[str]`.
- Thêm `ReviewSummaryResponse` + `ReviewSummaryCounts`.

### 7. `src/api/routes.py` — nối route

Giữ đúng convention hiện có: lazy import trong thân hàm, `HTTPException` + detail tiếng Việt, docstring tiếng Việt, param body tên `body`.

| Method | Path | Ghi chú |
|---|---|---|
| `GET` | `/dq/runs/{run_id}/rules` | thay `GET /dq/rules` (`routes.py:126`). Query: `status`, `table_name`, `dimension` |
| `PATCH` | `/dq/runs/{run_id}/rules/{rule_id}` | thay `PATCH /dq/rules/{rule_id}` (`routes.py:142`); `rule_id: str` |
| `POST` | `/dq/runs/{run_id}/rules/bulk-review` | thay `routes.py:164` |
| `GET` | `/dq/runs/{run_id}/review-summary` | **mới** — badge tiến độ |
| `GET` | `/dq/runs/{run_id}/approved-rules` | giữ path (`routes.py:177`), đổi signature service |

**Vì sao nested dưới `/runs/{run_id}`:** `rule_id` chỉ unique trong 1 run, khớp PK ghép. Breaking so với 3 route cũ — chưa có UI hay test nào gọi chúng nên an toàn.

⚠ **Thứ tự khai báo route:** `PATCH /runs/{run_id}/rules/{rule_id}` phải đặt **sau** `POST /runs/{run_id}/rules/bulk-review`? Không — khác HTTP method nên FastAPI không nhầm. Nhưng nếu sau này thêm `GET /runs/{run_id}/rules/{rule_id}` thì phải đặt dưới `bulk-review`, kẻo `bulk-review` bị match thành `rule_id`.

Xử lý lỗi:
- `run_id` không có trong `proposal_runs` → **404** `f"run_id={run_id!r} không tồn tại"`
- `run_id` tồn tại nhưng chưa có rule (status `RUNNING`) → **200** với list rỗng, không phải 404. UI phân biệt được qua `GET /dq/runs/{run_id}`.
- `rule_id` không thấy → **404**
- `status=REJECTED` mà thiếu `review_note` → **422** (validate ở `RuleUpdateRequest` bằng `model_validator`)
- `edited_parameters` trượt guardrail → **422** kèm message từ `ValidationError`

### 8. `src/config.py` — thêm 1 setting

`data/results` đang hardcode 3 chỗ (`rule_proposer_node.py:213,320,343`). Thêm cạnh `chroma_persist_dir` (`config.py:47`):

```python
results_dir: str = "./data/results"
```

**Không cần** `checkpoint_db_path` nữa. `database_url` (`config.py:44`) đã đủ.

### 9. `src/main.py` — **không sửa**

`init_db()` đã được gọi trong lifespan (`main.py:13-14`) và `create_all` sẽ tạo bảng `proposed_rules` với schema mới.

---

## Những thứ **không** đụng tới

- `ProposalRunModel` + `create_run` / `update_run_status` / `get_run` (`rule_store.py:124-189`) — metadata run, đang chạy tốt.
- `persist_rules_node` (gỡ khỏi graph, giữ code), `tests/test_profiler.py`, node `profiler_*`, `templates.py`, `chroma_rag_tool.py`.
- `requirements.txt` — không thêm dependency nào.

---

## Thứ tự thực hiện

1. **`rule_store.py` schema** (mục 3a-3c) — trước khi boot server, lúc còn free migration
2. `rule_schemas.py` (`RuleStatus`) → `config.py` (`results_dir`)
3. `rule_proposer_node.py` — **fix `run_id` clobber trước**, rồi `_stamp_rule`
4. `rule_store.py` phần CRUD (mục 3d-3g)
5. `hitl_gate_node.py` → `graph.py` rewire
6. `schemas.py` → `routes.py`
7. Tests

---

## Kiểm thử

### Unit — `tests/test_hitl_gate.py` (mới)

Theo pattern `tests/test_rule_proposer.py` (`@pytest.mark.asyncio`, `unittest.mock.patch`). **Không cần LLM** — dựng fake `proposed_rules` bằng tay. Dùng SQLite in-memory hoặc `tmp_path` để không đụng `data/app.db`.

- `rule_proposer_node` **giữ nguyên `rule_run_id` từ state** khi state đã có (regression test cho bug clobber)
- `rule_proposer_node` tự sinh `rule_run_id` khi state không có (debug harness)
- `_stamp_rule(rule, table, run_id)` 3 positional args vẫn chạy (backward compat)
- `_stamp_rule` sinh id unique khi 2 rule cùng `(column, rule_type)` → `#2`
- `save_proposed_rules` **giữ đủ** `dimension`, `rule_description`, `rule_id` (regression cho bug mất field)
- `save_proposed_rules` gọi 2 lần cùng `run_id` → không `IntegrityError`, không nhân đôi row
- `hitl_gate_node` trả `rules_saved` chính xác; không raise khi thư mục trace không ghi được
- `review_rule` → `status`, `reviewer`, `reviewed_at` được set; **`parameters` gốc không đổi**
- `effective_parameters`: có `edited_parameters` → dùng bản edit; không có → dùng bản AI
- `bulk_review` trả đúng `not_found` cho id sai
- `edited_parameters` vô lý (RANGE thiếu cả min/max) → `ValueError`
- Hai rule cùng `rule_id` khác `run_id` cùng tồn tại (chứng minh PK ghép đúng)

### Integration — `tests/test_api/test_hitl_routes.py` (mới)

Dùng fixture `client` sẵn có (`tests/conftest.py:10-15`).

⚠ **`ASGITransport` không chạy lifespan** → `init_db()` không được gọi. Với hướng DB thì fix đơn giản: fixture gọi trực tiếp `init_db()` + `create_run()` + `save_proposed_rules()` để seed, không cần `LifespanManager`. Đây là lợi thế so với hướng checkpointer.

Cases: 404 run_id lạ → 404 rule_id lạ → 422 status không hợp lệ → 422 REJECTED thiếu `review_note` → PATCH approve 200 → GET rules xác nhận status đổi → filter `dimension` → bulk-review với 1 id sai → review-summary khớp số → approved-rules chỉ trả APPROVED.

### End-to-end thủ công

```bash
uvicorn src.main:app --reload
# 1. Chạy Run 1
curl -X POST localhost:8000/api/v1/dq/propose \
  -H "Content-Type: application/json" \
  -d '{"dataset_id":"yellow_tripdata","sampling_rate":1.0}'
# 2. Poll tới khi DONE
curl localhost:8000/api/v1/dq/runs/<run_id>
# 3. run_id KHỚP: rules trả về non-empty, CÓ dimension + rule_description
curl "localhost:8000/api/v1/dq/runs/<run_id>/rules?status=PENDING"
# 4. Approve 1 rule
curl -X PATCH "localhost:8000/api/v1/dq/runs/<run_id>/rules/yellow_tripdata.vendor_id.NOT_NULL" \
  -H "Content-Type: application/json" \
  -d '{"status":"APPROVED","reviewer":"steward@ridepulse.vn"}'
# 5. RESTART uvicorn (Ctrl+C rồi chạy lại)
# 6. Rule vẫn APPROVED
curl "localhost:8000/api/v1/dq/runs/<run_id>/rules?status=APPROVED"
curl localhost:8000/api/v1/dq/runs/<run_id>/review-summary
```

Bước 3 là bài test cho bug `run_id` clobber. Bước 5-6 xác nhận durability — với DB thì đây là điều hiển nhiên, không cần dependency mới.

---

## Ghi chú bàn giao cho UI

- Danh sách rule: `GET /dq/runs/{run_id}/rules` — mỗi row có `rule_description` (câu tiếng Việt cho người không biết code) và `ai_reasoning` (panel "Vì sao AI đề xuất").
- Filter theo `dimension` phục vụ đúng mục đích khai báo tại `rule_schemas.py:33`, có index hỗ trợ.
- Modal Edit chỉ ghi vào `edited_parameters`; hiển thị diff với `parameters` để Steward thấy mình đã đổi gì.
- Test Generator (Run 2) **chỉ đọc `effective_parameters`** — không cần biết rule có bị sửa hay không.
- Chưa có auth: `reviewer` là free-text do client tự khai. RBAC Steward-only là việc của milestone sau (`docs/plan/rule_proposer_agent.md:251`).
