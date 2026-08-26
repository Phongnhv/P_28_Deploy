# Nhật ký sửa lỗi Graph 1 · 2 · 3

**Dự án:** RidePulse DQ — Autonomous Data Quality & Anomaly Intelligence Platform
**Phạm vi:** 19 lỗi trên 16 file mã nguồn + 7 file test
**Kết quả suite:** 4 failed / 197 passed / 2 skipped (trước: 9 failed / 179 passed)

**Quy trình:** viết test đỏ trước, xác nhận nó fail trên mã hiện tại, rồi mới sửa. Không sửa lỗi nào mà không có bằng chứng chạy được.

---

## Mục lục

1. [Bản đồ file đã sửa](#1--bản-đồ-file-đã-sửa)
2. [P0 — Lỗi khiến hệ thống chạy sai](#2--p0--lỗi-khiến-hệ-thống-chạy-sai)
3. [P1 — An toàn và tính đúng đắn của dữ liệu](#3--p1--an-toàn-và-tính-đúng-đắn-của-dữ-liệu)
4. [P2 — Độ tin cậy thống kê](#4--p2--độ-tin-cậy-thống-kê)
5. [P3 — Hardcode và metadata sai](#5--p3--hardcode-và-metadata-sai)
6. [Lỗi phát hiện thêm khi chạy test](#6--lỗi-phát-hiện-thêm-khi-chạy-test)
7. [Không sửa — và tại sao](#7--không-sửa--và-tại-sao)
8. [Thay đổi hợp đồng — đọc trước khi deploy](#8--thay-đổi-hợp-đồng--đọc-trước-khi-deploy)
9. [Cách chạy toàn bộ kiểm chứng](#9--cách-chạy-toàn-bộ-kiểm-chứng)

---

## 1 · Bản đồ file đã sửa

Đường dẫn tính từ gốc repo `P-028/`.

| Thư mục             | File                             | Sửa gì                      |
| --------------------- | -------------------------------- | ----------------------------- |
| `src/services/`     | `anomaly_service.py`           | NEW-1, LOGIC-02, NEW-5, NEW-6 |
| `src/services/`     | `dashboard_anomaly.py`         | NEW-9                         |
| `src/services/`     | `rule_store.py`                | NEW-3 (chú thích kiểu)     |
| `src/agents/nodes/` | `persist_report_node.py`       | BUG-02, BUG-03, BUG-04, NEW-2 |
| `src/agents/nodes/` | `test_runner_node.py`          | NEW-3, NEW-4, BUG-01, BUG-06  |
| `src/agents/nodes/` | `dbt_validation.py`            | NEW-2                         |
| `src/agents/nodes/` | `validate_dbt_project_node.py` | NEW-2                         |
| `src/agents/nodes/` | `hitl_semantic_gate_node.py`   | BUG-08                        |
| `src/agents/nodes/` | `rule_proposer_node.py`        | G1-B                          |
| `src/agents/nodes/` | `persist_analysis_node.py`     | BUG-05                        |
| `src/agents/nodes/` | `steward_insights_node.py`     | BUG-05                        |
| `src/agents/nodes/` | `report_writer_node.py`        | NEW-7, NEW-8                  |
| `src/agents/`       | `graph.py`                     | G1-A, BUG-08, BUG-10, BUG-05  |
| `src/agents/`       | `state.py`                     | BUG-08, NEW-2                 |
| `src/models/`       | `database.py`                  | PRE-1, PRE-2                  |
| `src/models/`       | `schemas.py`                   | NEW-3                         |

### File test

| File                                              | Trạng thái                              |
| ------------------------------------------------- | ----------------------------------------- |
| `tests/test_agents/test_persist_report_node.py` | **mới** — 2 test                  |
| `tests/test_agents/test_proposal_run_status.py` | **mới** — 3 test                  |
| `tests/test_agents/test_runner_safety.py`       | **mới** — 4 test                  |
| `tests/test_services/test_anomaly_baseline.py`  | **mới** — 3 test                  |
| `tests/test_services/test_anomaly_service.py`   | cập nhật — +1 test regression          |
| `tests/test_agents/test_semantic_contract.py`   | cập nhật — đổi theo hợp đồng mới |
| `tests/test_agents/test_execution_nodes.py`     | cập nhật — đổi theo hợp đồng mới |

---

## 2 · P0 — Lỗi khiến hệ thống chạy sai

Năm lỗi trong nhóm này đều thuộc loại "im lặng": hệ thống vẫn chạy, vẫn trả kết quả, nhưng kết quả đó sai và không có tín hiệu nào báo cho người dùng biết.

---

### NEW-1 — Phép trung bình có trọng số làm loãng điểm bất thường

**File:** `src/services/anomaly_service.py` — hàm `detect_anomalies()`, khối tổng hợp điểm

#### Tại sao phải sửa

Bất thường là quan hệ **HOẶC**: chỉ cần một nhóm tín hiệu (family) báo động là đợt chạy đáng ngờ. Mã cũ lấy *trung bình có trọng số* qua tất cả family, nên một family khỏe mạnh (điểm 0.0) kéo tụt điểm của family đang báo động.

Hậu quả thực tế: sau bước ingest, dataset nào cũng có bản ghi `profiles`, nên `VOLUME_DRIFT_DETECTOR` luôn sinh một signal. Trần điểm của family `STATISTICAL` vì thế chỉ còn `0.6 / 1.4 = 0.4286` — thấp hơn cả ngưỡng `WATCH` (0.45). Nghĩa là **một rule vi phạm 100% dữ liệu vẫn được kết luận NORMAL**.

Tôi chọn `max()` vì nó đảm bảo *tính đơn điệu*: thêm một signal không bao giờ làm giảm điểm tổng hợp. Ngưỡng 0.45 / 0.70 giữ nguyên để hạn chế phạm vi thay đổi; `family_weights` không bị bỏ đi mà chuyển sang vai trò chọn "family chủ đạo" khi hòa điểm.

#### Trước

```python
    if has_critical_override:
        final_score = max(family_reps.values())
        decision = "CRITICAL"
        confidence = 0.95
        severity = "HIGH"
    else:
        # Weighted family aggregation
        weighted_sum = 0.0
        weight_sum = 0.0
        for fam, rep_score in family_reps.items():
            w = family_weights.get(fam, 0.5)
            weighted_sum += w * rep_score
            weight_sum += w

        final_score = (weighted_sum / weight_sum) if weight_sum > 0 else 0.0

        # Reliability sum
        rel_sum = sum(sig["reliability"] for sig in signals)
        avg_reliability = (rel_sum / len(signals)) if signals else 0.0
```

#### Sau

```python
    if has_critical_override:
        final_score = max(family_reps.values())
        decision = "CRITICAL"
        confidence = 0.95
        severity = "HIGH"
    else:
        # Family aggregation — MAX, không phải trung bình.
        #
        # Bất thường là quan hệ HOẶC: chỉ cần MỘT family báo động là đợt chạy đáng ngờ.
        # Bản cũ lấy trung bình có trọng số nên một family khỏe mạnh (score 0.0) kéo tụt
        # điểm của family đang báo động — ví dụ STATISTICAL=0.80 + VOLUME=0.00 cho ra
        # 0.3429 (NORMAL) thay vì 0.80 (ANOMALY). Với dataset đã ingest, signal VOLUME
        # luôn tồn tại nên trần điểm của family STATISTICAL chỉ còn 0.6/1.4 = 0.4286,
        # thấp hơn cả ngưỡng WATCH → detector thống kê bị vô hiệu hoá hoàn toàn.
        #
        # MAX đảm bảo tính đơn điệu: thêm một signal không bao giờ làm giảm điểm tổng hợp.
        final_score = max(family_reps.values()) if family_reps else 0.0

        # Reliability sum
        rel_sum = sum(sig["reliability"] for sig in signals)
        avg_reliability = (rel_sum / len(signals)) if signals else 0.0
```

Thêm mới, ngay sau khi tính `family_reps`:

```python
    # Family "chủ đạo": điểm cao nhất, hoà điểm thì ưu tiên family đáng tin cậy hơn.
    dominant_family = ""
    if family_reps:
        dominant_family = max(
            family_reps,
            key=lambda fam: (family_reps[fam], family_weights.get(fam, 0.5)),
        )

    # ... và bổ sung vào dict trả về:
    return {
        "decision": decision,
        "score": final_score,
        ...
        "override_reason": override_reason,
        "dominant_family": dominant_family,
    }
```

#### Tác động đo được

| Kịch bản                        | Điểm cũ | Kết luận cũ      | Điểm mới | Kết luận mới      |
| --------------------------------- | ---------: | ------------------- | ----------: | -------------------- |
| STAT=0.8 (không có profile)     |     0.8000 | ANOMALY             |        0.80 | ANOMALY              |
| STAT=0.8 + VOLUME=0.0             |     0.3429 | **NORMAL** ❌ |        0.80 | **ANOMALY** ✅ |
| STAT=1.0 + VOLUME=0.0             |     0.4286 | **NORMAL** ❌ |        1.00 | **ANOMALY** ✅ |
| STAT=1.0 + VOLUME=0.0 + FRESH=0.0 |     0.2727 | **NORMAL** ❌ |        1.00 | **ANOMALY** ✅ |
| STAT=0.4 (cold-start nhẹ)        |     0.4000 | NORMAL              |        0.40 | NORMAL               |
| STAT=0.8 + VOLUME=0.9             |     0.8571 | ANOMALY             |        0.90 | ANOMALY              |

#### Cách kiểm chứng

```bash
venv/Scripts/python.exe -m pytest \
  tests/test_services/test_anomaly_service.py::test_volume_signal_does_not_dilute_rule_anomaly -q
```

**Test này kiểm tra gì:** tạo đúng kịch bản production — một rule FAIL 8% cộng một bản ghi `ProfileModel` (để sinh signal VOLUME). Khẳng định có signal `VOLUME` trong kết quả, family `STATISTICAL` đạt 0.80, và điểm tổng hợp `>= 0.70` để ra `ANOMALY`.

**Trước khi sửa test này fail với:** `AssertionError: Điểm tổng hợp bị pha loãng: 0.3429`

> **Lưu ý quan trọng:** các test cũ vẫn pass là vì fixture của chúng *không* tạo `ProfileModel`, nên không có signal VOLUME — chúng vô tình né đúng kịch bản gây lỗi. Đây là lý do lỗi này sống sót qua nhiều lần review.

---

### BUG-02 — Báo cáo đếm PASS/FAIL luôn ra 0

**File:** `src/agents/nodes/persist_report_node.py` — 3 vị trí đếm status

#### Tại sao phải sửa

Trong repo tồn tại song song hai bộ từ vựng trạng thái: `PASS/FAIL` và `PASSED/FAILED`. `test_runner_node` chuẩn hoá đầu ra về `PASS/FAIL`, nhưng `persist_report_node` vẫn so sánh với `PASSED/FAILED` ⇒ mọi bộ đếm cho ra 0. Dashboard đọc file báo cáo này sẽ thấy "0 đạt, 0 thất bại" bất kể thực tế.

Điểm đáng chú ý: *ngay trong cùng một hàm*, cách đó 66 dòng, có một chỗ đếm đúng (`in ("FAIL", "FAILED")`). Đây là dấu hiệu refactor nửa vời — khi thêm bước chuẩn hoá, chỉ một nửa số chỗ đọc được cập nhật.

Vì thế tôi không vá từng chỗ, mà tạo **một nguồn chân lý duy nhất**: hàm `_normalize_status()`. Đọc thì chấp nhận cả hai biến thể (để không vỡ dữ liệu cũ), ghi thì chỉ một dạng.

#### Trước — 3 vị trí rời rạc

```python
# Vị trí 1 — trong _save_decoupled_run(), đếm cho DqRunModel
failed_count = sum(1 for r in test_results if r.get("status") in ("FAIL", "FAILED"))

# Vị trí 2 — trong vòng lặp ghi DqResultModel
for res in test_results:
    r_status = res.get("status", "PASS")
    if r_status == "PASSED":
        r_status = "PASS"
    elif r_status == "FAILED":
        r_status = "FAIL"

# Vị trí 3 — payload file báo cáo JSON
"passed_count": sum(1 for r in test_results if r.get("status") == "PASSED"),
"failed_count": sum(1 for r in test_results if r.get("status") == "FAILED"),
"error_count":  sum(1 for r in test_results if r.get("status") == "ERROR"),
```

#### Sau — một hàm chuẩn hoá, ba chỗ dùng chung

```python
# Thêm mới ở đầu module
# test_runner_node chuẩn hoá status về PASS/FAIL/ERROR/SKIPPED, nhưng dữ liệu cũ (và các
# harness chạy tay) vẫn dùng PASSED/FAILED. Đọc thì chấp nhận cả hai, ghi thì chỉ một dạng.
_PASS_STATUSES = ("PASS", "PASSED")
_FAIL_STATUSES = ("FAIL", "FAILED")


def _normalize_status(raw: str | None) -> str:
    """Quy mọi biến thể status về đúng một từ vựng: PASS / FAIL / ERROR / SKIPPED."""
    value = (raw or "PASS").upper()
    if value in _PASS_STATUSES:
        return "PASS"
    if value in _FAIL_STATUSES:
        return "FAIL"
    if value == "ERROR":
        return "ERROR"
    return "SKIPPED"


# Vị trí 1
failed_count = sum(1 for r in test_results if _normalize_status(r.get("status")) == "FAIL")

# Vị trí 2
for res in test_results:
    r_status = _normalize_status(res.get("status"))

# Vị trí 3
"passed_count": sum(1 for r in test_results if _normalize_status(r.get("status")) == "PASS"),
"failed_count": sum(1 for r in test_results if _normalize_status(r.get("status")) == "FAIL"),
"error_count":  sum(1 for r in test_results if _normalize_status(r.get("status")) == "ERROR"),
```

#### Cách kiểm chứng

```bash
venv/Scripts/python.exe -m pytest \
  tests/test_agents/test_persist_report_node.py::test_report_counts_normalized_statuses -q
```

**Test này kiểm tra gì:** đưa vào `persist_report_node` đúng định dạng mà `test_runner_node` trả ra (status `"PASS"` / `"FAIL"`), đọc lại file JSON qua `metadata["report_file_path"]`, rồi khẳng định `passed_count == 1` và `failed_count == 1`.

**Kiểm tra thủ công:** chạy Run 2 rồi mở file mới nhất trong `output/reports/` — trường `passed_count` phải khác 0.

---

### G1-A — Run 1 luôn báo "DONE" kể cả khi thất bại hoàn toàn

**File:** `src/agents/graph.py` — hàm `run_proposal_graph()`

#### Tại sao phải sửa

`graph.ainvoke()` **không ném exception** khi một node trả về `{"error": ...}` — LangGraph chỉ định tuyến sang `END`. Nhưng dòng ngay sau đó ghi `DONE` vô điều kiện, nên khối `except` phía dưới không bao giờ chạy với loại lỗi này.

Kết hợp với `hitl_semantic_gate_node` (ghi `AWAITING_SEMANTIC_REVIEW` vào DB rồi trả về error), trạng thái "đang chờ Steward duyệt" **bị ghi đè thành DONE ngay lập tức** — HITL gate của Graph 1 bị vô hiệu hoá ở tầng runner.

Sửa: đọc `final_state` và phân ba nhánh. Mỗi nhánh trả về dict có cùng khoá (`run_id`, `rules`, `summary`) nên caller hiện tại không vỡ, chỉ thêm khoá `status`.

#### Trước

```python
    try:
        final_state = await proposal_graph.ainvoke(initial_state)
        update_run_status(run_id=run_id, status="DONE")

        rules = list_rules(run_id=run_id)
        summary = get_review_summary(run_id=run_id)

        print("\n" + "=" * 75)
        print(f"🎉 RUN 1 HOÀN THÀNH THÀNH CÔNG (Proposal run_id: {run_id})")
        print("=" * 75)
        ...
        return {"run_id": run_id, "rules": rules, "summary": summary}

    except Exception as exc:
        logger.error("Run 1 thất bại: %s", exc, exc_info=True)
        update_run_status(run_id=run_id, status="FAILED", error=str(exc))
```

#### Sau

```python
    try:
        final_state = await proposal_graph.ainvoke(initial_state)

        # `ainvoke` KHÔNG ném exception khi một node trả về {"error": ...} — graph chỉ
        # định tuyến sang END. Trước đây runner ghi "DONE" vô điều kiện nên một Run 1
        # thất bại hoàn toàn (LLM hết quota → 0 rules) vẫn được báo là thành công, và
        # trạng thái AWAITING_SEMANTIC_REVIEW do gate ghi cũng bị ghi đè ngay lập tức.
        pause_reason = final_state.get("pause_reason")
        graph_error = final_state.get("error")

        if pause_reason:
            update_run_status(run_id=run_id, status=str(pause_reason))
            logger.info("Run 1 tạm dừng chờ người duyệt | run_id=%s | lý do=%s", run_id, pause_reason)
            print(f"\n⏸️  RUN 1 TẠM DỪNG — {pause_reason} (Proposal run_id: {run_id})\n")
            return {
                "run_id": run_id,
                "status": str(pause_reason),
                "rules": list_rules(run_id=run_id),
                "summary": get_review_summary(run_id=run_id),
            }

        if graph_error:
            update_run_status(run_id=run_id, status="FAILED", error=str(graph_error))
            logger.error("Run 1 thất bại trong graph | run_id=%s | error=%s", run_id, graph_error)
            print(f"\n❌ RUN 1 THẤT BẠI: {graph_error}\n")
            return {
                "run_id": run_id,
                "status": "FAILED",
                "error": str(graph_error),
                "rules": list_rules(run_id=run_id),
                "summary": get_review_summary(run_id=run_id),
            }

        update_run_status(run_id=run_id, status="DONE")

        rules = list_rules(run_id=run_id)
        summary = get_review_summary(run_id=run_id)
        ...
        return {"run_id": run_id, "status": "DONE", "rules": rules, "summary": summary}
```

#### Cách kiểm chứng

```bash
venv/Scripts/python.exe -m pytest tests/test_agents/test_proposal_run_status.py -q
```

**Ba test trong file này:**

- `test_run_marked_failed_when_graph_returns_error` — graph giả trả `{"error": ...}`, khẳng định run có status `FAILED`.
- `test_run_stays_awaiting_review_when_paused` — graph giả trả `{"pause_reason": ...}`, khẳng định status giữ nguyên `AWAITING_SEMANTIC_REVIEW`.
- `test_run_marked_done_on_success` — đường thành công vẫn phải ra `DONE` (test chống hồi quy).

**Kỹ thuật:** dùng `monkeypatch.setattr` thay `build_proposal_graph` bằng một lớp `_FakeGraph` có `ainvoke()` trả về state định sẵn — không cần LLM, không cần DB thật.

---

### G1-B — LLM chết toàn bộ vẫn báo thành công

**File:** `src/agents/nodes/rule_proposer_node.py` — cuối hàm `rule_proposer_node()`

#### Tại sao phải sửa

`asyncio.gather(..., return_exceptions=True)` nuốt mọi exception; vòng lặp phía sau chỉ ghi chúng vào `rule_proposal_errors` rồi `continue`. Không có ai set `error`, nên graph chạy tiếp sang `hitl_gate`, node này thấy danh sách rỗng và trả về bình thường ⇒ runner ghi `DONE`.

Kết quả người dùng nhìn thấy: *"Run thành công — 0 rules được đề xuất"* — không phân biệt được với trường hợp hợp lệ "dataset quá sạch, không cần rule nào".

Tôi chọn ngưỡng **thất bại toàn bộ** (có lỗi và không có rule nào) mới coi là lỗi; thất bại một phần chỉ ghi cảnh báo, vì mất một bảng trong nhiều bảng không nên giết cả pipeline.

#### Trước

```python
    return {
        "proposed_rules": flat_rules,
        "rule_proposal_errors": errors,
        "rule_run_id": run_id,
    }
```

#### Sau

```python
result: dict = {
    "proposed_rules": flat_rules,
    "rule_proposal_errors": errors,
    "rule_run_id": run_id,
}

# Thất bại toàn bộ (ví dụ LLM hết quota / mất mạng) trước đây chỉ được ghi vào
# `rule_proposal_errors` rồi graph vẫn chạy tiếp và runner báo DONE với 0 rules —
# không phân biệt được với trường hợp hợp lệ "dataset sạch, không cần rule nào".
if errors and not flat_rules:
    result["error"] = f"Rule proposer thất bại trên toàn bộ {len(errors)}/{len(table_names)} bảng: " + "; ".join(
        f"{e.get('table')}: {e.get('error')}" for e in errors[:3]
    )
elif errors:
    logger.warning(
        "rule_proposer_node thành công một phần: %d/%d bảng thất bại",
        len(errors),
        len(table_names),
    )

return result
```

#### Cách kiểm chứng

```bash
venv/Scripts/python.exe -m pytest tests/test_agents/test_rule_proposer_node.py tests/test_agents/test_graph.py -q
```

**Kiểm tra thủ công:** tạm đặt `OPENAI_API_KEY` sai trong `.env` rồi chạy `python -m src.agents.graph 1 <dataset_id>`. Console phải in `❌ RUN 1 THẤT BẠI` thay vì banner thành công, và bảng `jobs` phải có status `FAILED`.

---

### BUG-08 — Một biến `error` mang ba nghĩa khác nhau

**File:**

- `src/agents/nodes/hitl_semantic_gate_node.py`
- `src/agents/graph.py` (hàm nội bộ `_should_continue_proposal`)
- `src/agents/state.py`

#### Tại sao phải sửa

Trường `error` đang gánh ba ngữ nghĩa: lỗi thật, tín hiệu tạm dừng (`AWAITING_SEMANTIC_REVIEW`), và trạng thái bình thường. Một biến ba nghĩa thì không routing nào đúng được — và đây chính là gốc rễ khiến G1-A gây hại.

Sửa bằng cách tách hẳn một trường mới `pause_reason` trong state schema.

#### Trước — `hitl_semantic_gate_node.py`, cuối hàm

```python
# Set error đặc biệt để conditional edge dẫn tới END
return {"error": "AWAITING_SEMANTIC_REVIEW", "progress_state": "WAITING_FOR_SEMANTIC_REVIEW"}
```

#### Sau

```python
# Tạm dừng có chủ đích — KHÔNG phải lỗi. Dùng `pause_reason` để conditional edge dẫn
# tới END mà runner vẫn phân biệt được "chờ Steward duyệt" với "chạy thất bại".
return {"pause_reason": "AWAITING_SEMANTIC_REVIEW", "progress_state": "WAITING_FOR_SEMANTIC_REVIEW"}
```

#### Trước — `graph.py`, trong `build_proposal_graph()`

```python
    def _should_continue_proposal(state: AgentState) -> str:
        if state.get("error"):
            return END
        return "next"
```

#### Sau

```python
    def _should_continue_proposal(state: AgentState) -> str:
        # Dừng khi có lỗi thật HOẶC khi một gate chủ động tạm dừng chờ người duyệt.
        # Hai trường hợp này dẫn tới cùng một điểm kết thúc graph nhưng runner sẽ ghi
        # trạng thái run khác nhau (FAILED vs AWAITING_SEMANTIC_REVIEW).
        if state.get("error") or state.get("pause_reason"):
            return END
        return "next"
```

#### Trước — `state.py`, class `AgentState`

```python
    query: str
    context: str
    analysis: str
    response: str
    error: str
    metadata: dict
```

#### Sau

```python
    query: str
    context: str
    analysis: str
    response: str
    error: str
    # Lý do tạm dừng có chủ đích (ví dụ chờ Steward duyệt Semantic Contract).
    # Tách khỏi `error` để routing và trạng thái run phân biệt được "lỗi" với "đang chờ người".
    pause_reason: str
    metadata: dict
```

#### Cách kiểm chứng

```bash
venv/Scripts/python.exe -m pytest tests/test_agents/test_semantic_contract.py -q
```

**Test đã được cập nhật:** `test_hitl_semantic_gate_node` trước đây khẳng định `res_draft.get("error") == "AWAITING_SEMANTIC_REVIEW"` — tức là nó *khoá chặt chính cái lỗi này*. Giờ nó khẳng định `pause_reason == "AWAITING_SEMANTIC_REVIEW"` **và** `error is None`.

> Đây là trường hợp cập nhật test là một phần hợp lệ của việc sửa lỗi: test cũ mã hoá hành vi sai.

---

## 3 · P1 — An toàn và tính đúng đắn của dữ liệu

---

### NEW-3 — Cột `failed_row_ids` thực ra chứa nguyên dòng dữ liệu thô

**File:** `src/agents/nodes/test_runner_node.py` — `_fetch_sample_failures()`, `_fetch_unique_samples()` + `src/models/schemas.py`

#### Tại sao phải sửa

`SELECT *` lấy **toàn bộ cột** của dòng vi phạm rồi nhét vào cột tên là `failed_row_ids` (ngụ ý chỉ chứa ID). Với dữ liệu taxi, đó là toạ độ đón/trả, thời gian, số tiền — đủ để tái định danh chuyến đi. Vừa là lỗ hổng quyền riêng tư, vừa là tên gọi lừa dối.

Nó còn **làm vỡ API**: `routes.py:246` khai `failed_row_ids: list[str]` và frontend `types.ts:183` cũng vậy, nhưng đường agent ghi vào đó một danh sách dict ⇒ `GET /dq-runs/{id}/results` sẽ lỗi validation.

Đường Supabase đã làm đúng từ đầu (`SELECT source_row_id ... LIMIT :failed_id_limit`), nên tôi sửa theo chuẩn đó và đồng thời nâng giới hạn mẫu từ 5 lên **20** để hai pipeline dùng chung một hằng số. Cần một hàm dò cột định danh vì các bảng khác nhau có tên khoá khác nhau (`source_row_id`, `trip_id`…).

#### Trước

```python
def _fetch_sample_failures(
    table_name: str,
    predicate: str | None,
    bind_params: dict,
    dialect_name: str = "sqlite",
    limit: int = 5,
) -> list[dict]:
    """Lấy tối đa 5 bản ghi mẫu vi phạm điều kiện.

    CRITICAL SAFETY GUARD: predicate MUST be programmatically constructed (e.g. from _build_row_predicate)
    and must not accept raw user input directly.
    """
    if not predicate or predicate == "1=0":
        return []

    # Basic runtime safety guards
    assert "--" not in predicate and ";" not in predicate, (
        "Security violation: potential SQL injection detected in predicate"
    )

    quoted_table = _quote_ident(table_name, dialect_name)
    sample_sql = f"SELECT * FROM {quoted_table} WHERE {predicate} LIMIT {limit}"

    engine = get_engine()
    try:
        with engine.connect() as conn:
            result = conn.execute(text(sample_sql), bind_params)
            columns = result.keys()
            rows = result.fetchall()
            return [dict(zip(columns, row)) for row in rows]
    except Exception as exc:
        logger.warning("Không thể lấy sample failures cho %s: %s", table_name, exc)
        return []


def _fetch_unique_samples(
    table_name: str,
    col_name: str,
    dialect_name: str = "sqlite",
    limit: int = 5,
) -> list[dict]:
    """Lấy mẫu các giá trị bị trùng lặp cho rule UNIQUE."""
    quoted_table = _quote_ident(table_name, dialect_name)
    quoted_col = _quote_ident(col_name, dialect_name)
    sample_sql = (
        f"SELECT {quoted_col} AS duplicated_value, COUNT(*) AS occurrences "
        f"FROM {quoted_table} "
        f"WHERE {quoted_col} IS NOT NULL "
        f"GROUP BY {quoted_col} "
        f"HAVING COUNT(*) > 1 "
        f"LIMIT {limit}"
    )
    engine = get_engine()
    try:
        with engine.connect() as conn:
            result = conn.execute(text(sample_sql))
            columns = result.keys()
            rows = result.fetchall()
            return [dict(zip(columns, row)) for row in rows]
    except Exception as exc:
        logger.warning("Không thể lấy duplicate samples cho %s.%s: %s", table_name, col_name, exc)
        return []
```

#### Sau

```python
# Số ID vi phạm tối đa được gom cho mỗi rule. Khớp với `failed_id_limit` của đường
# thực thi Supabase (src/services/supabase_dataset.py) để hai pipeline cùng một giới hạn.
SAMPLE_FAILURE_LIMIT = 20

# Thứ tự ưu tiên khi dò cột định danh của bảng.
_IDENTITY_COLUMN_PREFERENCES = ("source_row_id", "id", "row_id")


def _resolve_identity_column(table_name: str) -> str | None:
    """Tìm cột định danh của bảng để trích ID dòng vi phạm.

    Ưu tiên: source_row_id → khóa chính → id/row_id → cột đầu tiên có hậu tố `_id`.
    Trả về None nếu không xác định được (khi đó bỏ qua việc lấy mẫu thay vì đọc cả dòng).
    """
    try:
        inspector = sa_inspect(get_engine())
        columns = [col["name"] for col in inspector.get_columns(table_name)]
    except Exception as exc:
        logger.warning("Không thể đọc schema của bảng %s: %s", table_name, exc)
        return None

    if not columns:
        return None

    lookup = {name.lower(): name for name in columns}
    for preferred in _IDENTITY_COLUMN_PREFERENCES:
        if preferred in lookup:
            return lookup[preferred]

    try:
        pk_columns = inspector.get_pk_constraint(table_name).get("constrained_columns") or []
        if pk_columns:
            return pk_columns[0]
    except Exception:
        pass

    for name in columns:
        if name.lower().endswith("_id"):
            return name
    return None


def _fetch_sample_failures(
    table_name: str,
    predicate: str | None,
    bind_params: dict,
    dialect_name: str = "sqlite",
    limit: int = SAMPLE_FAILURE_LIMIT,
) -> list[str]:
    """Lấy tối đa `limit` ID của các dòng vi phạm điều kiện.

    Chỉ trả về ID, KHÔNG trả về nội dung dòng: kết quả này được ghi vào cột
    `dq_results.failed_row_ids` và hiển thị trên UI. Bản trước dùng `SELECT *` nên lưu
    nguyên bản ghi (toạ độ, thời gian, số tiền...) vào một cột mang tên "row_ids".

    CRITICAL SAFETY GUARD: predicate MUST be programmatically constructed (e.g. from _build_row_predicate)
    and must not accept raw user input directly.
    """
    if not predicate or predicate == "1=0":
        return []

    _assert_safe_predicate(predicate)

    identity_column = _resolve_identity_column(table_name)
    if not identity_column:
        logger.warning("Bỏ qua lấy mẫu vi phạm cho %s: không xác định được cột định danh", table_name)
        return []

    quoted_table = _quote_ident(table_name, dialect_name)
    quoted_id = _quote_ident(identity_column, dialect_name)
    sample_sql = f"SELECT {quoted_id} FROM {quoted_table} WHERE {predicate} ORDER BY {quoted_id} LIMIT {limit}"

    engine = get_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(sample_sql), bind_params).fetchall()
            return [str(row[0]) for row in rows if row[0] is not None]
    except Exception as exc:
        logger.warning("Không thể lấy sample failures cho %s: %s", table_name, exc)
        return []


def _fetch_unique_samples(
    table_name: str,
    col_name: str,
    dialect_name: str = "sqlite",
    limit: int = SAMPLE_FAILURE_LIMIT,
) -> list[str]:
    """Lấy tối đa `limit` ID của các dòng có giá trị bị trùng lặp (rule UNIQUE)."""
    identity_column = _resolve_identity_column(table_name)
    if not identity_column:
        logger.warning("Bỏ qua lấy mẫu trùng lặp cho %s: không xác định được cột định danh", table_name)
        return []

    quoted_table = _quote_ident(table_name, dialect_name)
    quoted_col = _quote_ident(col_name, dialect_name)
    quoted_id = _quote_ident(identity_column, dialect_name)
    sample_sql = (
        f"SELECT {quoted_id} FROM {quoted_table} "
        f"WHERE {quoted_col} IN ("
        f"SELECT {quoted_col} FROM {quoted_table} "
        f"WHERE {quoted_col} IS NOT NULL "
        f"GROUP BY {quoted_col} HAVING COUNT(*) > 1"
        f") ORDER BY {quoted_id} LIMIT {limit}"
    )
    engine = get_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(sample_sql)).fetchall()
            return [str(row[0]) for row in rows if row[0] is not None]
    except Exception as exc:
        logger.warning("Không thể lấy duplicate samples cho %s.%s: %s", table_name, col_name, exc)
        return []
```

#### Kèm theo — `src/models/schemas.py`

```python
# Trước
    sample_failures: list[dict[str, Any]] | None = None

# Sau
    # Chỉ chứa ID dòng vi phạm, không phải nội dung bản ghi (xem test_runner_node).
    sample_failures: list[str] | None = None
```

#### Cách kiểm chứng

```bash
venv/Scripts/python.exe -m pytest \
  tests/test_agents/test_execution_nodes.py::test_execution_graph_end_to_end \
  tests/test_agents/test_runner_safety.py::test_sample_limit_matches_supabase_path -q
```

**Test end-to-end kiểm tra gì:** chạy toàn bộ Run 2 trên bảng `mock_trips` (cột định danh là `trip_id`, không có khoá chính — kiểm tra luôn nhánh dò `*_id`). Khẳng định `cross_res["sample_failures"] == ["TRIP_5"]` — một danh sách chuỗi, không phải dict.

**Test hằng số:** dùng `inspect.signature()` đọc giá trị mặc định `failed_id_limit` của `execute_rule()` bên Supabase và khẳng định nó bằng `SAMPLE_FAILURE_LIMIT` — nếu ai đó đổi một bên, test sẽ đỏ.

**Kiểm tra thủ công:** sau một DQ run, mở UI tab kết quả — cột "dòng lỗi mẫu" phải hiện danh sách ID ngăn cách bởi dấu phẩy, không phải `[object Object]`.

---

### NEW-4 — Chốt chặn SQL injection dùng `assert`

**File:** `src/agents/nodes/test_runner_node.py` — hàm mới `_assert_safe_predicate()`

#### Tại sao phải sửa

Python **xoá sạch mọi câu lệnh `assert`** khi chạy với cờ `-O` hoặc biến môi trường `PYTHONOPTIMIZE=1`. Nhiều Docker image production bật cờ này mặc định. Nghĩa là chốt chặn bảo mật sẽ biến mất đúng lúc cần nhất — ở production.

Tôi tách thành hàm riêng để tái sử dụng, và bổ sung chặn cả comment khối `/* */`.

#### Trước — nằm giữa thân `_fetch_sample_failures()`

```python
# Basic runtime safety guards
assert "--" not in predicate and ";" not in predicate, (
    "Security violation: potential SQL injection detected in predicate"
)
```

#### Sau — hàm riêng, dùng raise

```python
def _assert_safe_predicate(predicate: str) -> None:
    """Chốt chặn chống SQL injection cho predicate do hệ thống tự sinh.

    Dùng `raise` chứ KHÔNG dùng `assert`: Python xoá mọi câu lệnh assert khi chạy với
    cờ -O / PYTHONOPTIMIZE=1 (nhiều image production bật mặc định), tức chốt chặn bảo mật
    sẽ biến mất đúng lúc cần nhất.
    """
    if "--" in predicate or ";" in predicate or "/*" in predicate or "*/" in predicate:
        raise ValueError("Security violation: potential SQL injection detected in predicate")
```

#### Cách kiểm chứng

```bash
venv/Scripts/python.exe -m pytest tests/test_agents/test_runner_safety.py -q
```

**Test kiểm tra gì:** ba payload độc hại (`; DROP TABLE`, `-- comment`, `/* x */`) phải làm hàm ném `ValueError`; một predicate hợp lệ phải đi qua êm.

**Chứng minh lý do dùng raise** — chạy lại chính test đó với cờ tối ưu, nó vẫn phải pass:

```bash
venv/Scripts/python.exe -O -m pytest tests/test_agents/test_runner_safety.py -q
```

Với mã cũ dùng `assert`, lệnh trên sẽ fail vì chốt chặn đã bị xoá khỏi bytecode.

---

### NEW-2 — Quality gate dbt tự tuyên bố "đã đạt" khi chưa hề chạy

**File:**

- `src/agents/nodes/dbt_validation.py` (`run_dbt_parse`)
- `src/agents/nodes/validate_dbt_project_node.py`
- `src/agents/nodes/persist_report_node.py`

#### Tại sao phải sửa

Không tìm thấy executable `dbt` trong môi trường local/dev ⇒ hàm trả về `True`, tức "hợp lệ". Mà `dbt` không có trong `requirements.txt` cũng không có trong venv, nên chốt chặn này **chưa từng hoạt động**, và báo cáo vẫn ghi `dbt_status = "SUCCESS"` cho một lần kiểm tra chưa từng diễn ra.

Tôi *không* đổi thành `False`: làm vậy sẽ chặn toàn bộ pipeline ở máy dev và phá hàng loạt test — một quyết định lớn hơn phạm vi sửa lỗi. Thay vào đó tôi thêm **trạng thái thứ ba**: pipeline vẫn chạy, nhưng ghi trung thực `SKIPPED` thay vì `SUCCESS`. Việc có nên thêm `dbt-core` vào dependencies là quyết định của đội.

#### Trước — `dbt_validation.py`

```python
def run_dbt_parse(dbt_dir: Path) -> tuple[bool, str, int | None]:
    dbt_cmd = shutil.which("dbt")
    settings = get_settings()
    if not dbt_cmd:
        if settings.app_env in ("local", "development", "test"):
            return True, "dbt executable unavailable; structural validation used", None
        return False, "dbt executable is required in production", None
    try:
        result = subprocess.run(
            [dbt_cmd, "parse", "--project-dir", str(dbt_dir), "--profiles-dir", str(dbt_dir)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as exc:
        return False, f"dbt parse could not run: {exc}", None
    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    return result.returncode == 0, output, result.returncode
```

#### Sau

```python
#: Giá trị `dbt_status` khi chốt chặn dbt không thực sự chạy được.
DBT_PARSE_SKIPPED = "SKIPPED"


def run_dbt_parse(dbt_dir: Path) -> tuple[bool, str, int | None]:
    """Chạy `dbt parse` để kiểm tra artifact YAML đã sinh.

    Trả về (valid, output, return_code). Khi không có executable `dbt` trong môi trường
    local/dev/test, hàm vẫn cho pipeline đi tiếp (valid=True) nhưng đánh dấu rõ trong
    `output` rằng chốt chặn đã bị BỎ QUA — gọi `dbt_parse_was_skipped(output)` để phân
    biệt "đã kiểm tra và đạt" với "chưa hề kiểm tra". Trước đây hai tình huống này không
    thể phân biệt: báo cáo ghi dbt_status="SUCCESS" cho một lần kiểm tra chưa từng chạy.
    """
    dbt_cmd = shutil.which("dbt")
    settings = get_settings()
    if not dbt_cmd:
        if settings.app_env in ("local", "development", "test"):
            return (
                True,
                f"{DBT_PARSE_SKIPPED}: dbt executable unavailable; only structural YAML validation ran",
                None,
            )
        return False, "dbt executable is required in production", None
    try:
        result = subprocess.run(
            [dbt_cmd, "parse", "--project-dir", str(dbt_dir), "--profiles-dir", str(dbt_dir)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as exc:
        return False, f"dbt parse could not run: {exc}", None
    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    return result.returncode == 0, output, result.returncode


def dbt_parse_was_skipped(output: str | None) -> bool:
    """True khi `run_dbt_parse` cho qua mà KHÔNG thực sự chạy dbt."""
    return bool(output) and output.startswith(DBT_PARSE_SKIPPED)
```

#### Trước — `validate_dbt_project_node.py`

```python
    content = ""
    valid = False
    error = None
    return_code = None
    try:
        ...
            valid, output, return_code = run_dbt_parse(dbt_dir)
            if not valid:
                error = output or "dbt parse failed"
    ...
    updates: dict = {
        "generated_dbt_yaml": content,
        "dbt_validation_valid": valid,
        "dbt_validation_error": error,
        ...
    }
```

#### Sau

```python
    content = ""
    valid = False
    error = None
    return_code = None
    dbt_skipped = False
    try:
        ...
            valid, output, return_code = run_dbt_parse(dbt_dir)
            dbt_skipped = dbt_parse_was_skipped(output)
            if dbt_skipped:
                logger.warning(
                    "Chốt chặn dbt BỊ BỎ QUA (không tìm thấy executable dbt): %s", output
                )
            if not valid:
                error = output or "dbt parse failed"
    ...
    updates: dict = {
        "generated_dbt_yaml": content,
        "dbt_validation_valid": valid,
        # Phân biệt "đã chạy dbt parse và đạt" với "chưa hề chạy dbt" — nếu không,
        # báo cáo ghi dbt_status=SUCCESS cho một lần kiểm tra chưa từng diễn ra.
        "dbt_validation_skipped": dbt_skipped,
        "dbt_validation_error": error,
        ...
    }
```

#### Trước / Sau — `persist_report_node.py`, khi tạo `DqRunModel`

```python
# Trước
                dbt_status=state.get("metadata", {}).get("dbt_status", "SUCCESS"),

# Sau
                dbt_status=(
                    "SKIPPED"
                    if state.get("dbt_validation_skipped")
                    else state.get("metadata", {}).get("dbt_status", "SUCCESS")
                ),
```

#### Cách kiểm chứng

```bash
venv/Scripts/python.exe -m pytest \
  tests/test_agents/test_runner_safety.py::test_skipped_dbt_parse_is_distinguishable \
  tests/test_agents/test_dbt_validation_nodes.py -q
```

**Test kiểm tra gì:** chuỗi bắt đầu bằng `SKIPPED:` phải cho `True`; output thật của dbt (`"Found 3 models, 2 tests"`) phải cho `False`; `None` cũng cho `False`.

**Kiểm tra thủ công:** chạy Run 2 khi máy không cài dbt, rồi truy vấn DB:

```sql
SELECT id, dbt_status FROM dq_runs ORDER BY created_at DESC LIMIT 1;
-- kỳ vọng: SKIPPED  (trước đây: SUCCESS)
```

Log cũng phải xuất hiện dòng `WARNING ... Chốt chặn dbt BỊ BỎ QUA`.

---

### BUG-04 — Mốc thời gian `dq_runs` lệch 7 giờ

**File:** `src/agents/nodes/persist_report_node.py` — hàm nội bộ `_save_decoupled_run()`

#### Tại sao phải sửa

Repo có một hợp đồng thời gian rõ ràng, ghi trong docstring của `src/time_utils.py`: **naive-UTC** — cột `DateTime` không mang timezone nhưng giá trị phải là giờ UTC. Cột `DqRunModel.created_at` mặc định dùng đúng helper `utc_now()`.

Nhưng node này ghi đè bằng `datetime.now()` — giờ *địa phương*. Ở múi UTC+7, mọi bản ghi `dq_runs` bị đẩy sớm 7 giờ so với mọi bảng khác. Không crash, chỉ sai âm thầm: thứ tự sắp xếp run, biểu đồ quality trend, "run gần nhất" đều lệch.

**Lỗi âm thầm nguy hiểm hơn lỗi crash**, vì không có gì báo cho bạn biết.

#### Trước

```python
from datetime import datetime
...
            dq_run = DqRunModel(
                id=test_run_id,
                job_id=state.get("job_id") or test_run_id,
                dataset_id=state.get("dataset_id") or "unknown",
                rule_ids=json.dumps([...]),
                status=dq_status,
                total_failed=failed_count,
                total_checked=checked_count,
                created_at=datetime.now(),
                completed_at=datetime.now(),
                ...
            )
```

#### Sau

```python
from datetime import datetime
from src.time_utils import utc_now
...
            dq_run = DqRunModel(
                id=test_run_id,
                job_id=state.get("job_id") or test_run_id,
                dataset_id=state.get("dataset_id") or "unknown",
                rule_ids=json.dumps([...]),
                status=dq_status,
                total_failed=failed_count,
                total_checked=checked_count,
                created_at=utc_now(),
                completed_at=utc_now(),
                ...
            )
```

#### Cách kiểm chứng

```bash
venv/Scripts/python.exe -m pytest \
  tests/test_agents/test_persist_report_node.py::test_dq_run_timestamps_use_naive_utc -q
```

**Test kiểm tra gì:** ghi lại `utc_now()` trước và sau khi gọi node, rồi khẳng định `before <= run.created_at <= after`. Bất kỳ độ lệch múi giờ nào cũng đẩy giá trị ra ngoài cửa sổ này.

**Trước khi sửa test fail với:**

```
AssertionError: created_at=2026-08-20 18:30:10 nằm ngoài
[2026-08-20 11:30:10, 2026-08-20 11:30:10] — nhiều khả năng là giờ địa phương
```

Đúng 7 giờ chênh lệch, khớp với múi giờ Việt Nam.

---

### BUG-06 — FRESHNESS báo động giả vì lệch múi giờ

**File:** `src/agents/nodes/test_runner_node.py` — nhánh `query_type == "freshness"` trong `_execute_single_test()`

#### Tại sao phải sửa

Comment trong code nói "Giả định UTC nếu không có timezone", nhưng dòng ngay dưới lại dùng `datetime.now()` — giờ địa phương. Ở UTC+7, phép trừ cho ra `tuổi_thật + 7 giờ`: tuổi dữ liệu bị **thổi phồng**.

Hệ quả là **false positive**: dữ liệu 18 giờ tuổi (còn tốt so với ngưỡng 24 giờ) bị báo FAILED. Báo động giả làm Data Steward mất niềm tin và bắt đầu bỏ qua cảnh báo — về lâu dài nguy hiểm hơn bỏ sót.

Tôi cũng thêm thông báo lỗi nêu rõ số giờ trễ, vì trước đó khi FAILED không có lời giải thích nào.

#### Trước

```python
                if ts_val.tzinfo is None:
                    # Giả định UTC nếu không có timezone
                    now_t = datetime.now()
                else:
                    now_t = datetime.now(UTC)

                age_delta = now_t - ts_val
                if age_delta > timedelta(hours=max_age_hours):
                    status = "FAILED"
                    v_count = 1
                    v_rate = 1.0
```

#### Sau

```python
                if ts_val.tzinfo is None:
                    # Timestamp không mang timezone → theo hợp đồng naive-UTC của
                    # src/time_utils.py, phải so với ĐỒNG HỒ UTC. Bản trước dùng
                    # datetime.now() (giờ địa phương) nên ở múi UTC+7 tuổi dữ liệu bị
                    # thổi phồng thêm 7 giờ → FRESHNESS báo FAILED giả.
                    now_t = utc_now()
                else:
                    now_t = datetime.now(UTC)

                age_delta = now_t - ts_val
                if age_delta > timedelta(hours=max_age_hours):
                    status = "FAILED"
                    v_count = 1
                    v_rate = 1.0
                    err_msg = (
                        f"Dữ liệu đã cũ {age_delta.total_seconds() / 3600:.1f} giờ "
                        f"(ngưỡng {max_age_hours} giờ); mốc mới nhất: {max_ts}."
                    )
```

> ### ⚠️ Giả định đã áp dụng — cần đội xác nhận
>
> Tôi coi timestamp không mang timezone trong DB là **giờ UTC**, theo đúng hợp đồng `src/time_utils.py`. Nhưng dữ liệu `pickup_at` gốc của NYC Taxi thực chất là *giờ địa phương New York*. Nếu đội quyết định coi timestamp là giờ địa phương, cả comment lẫn logic cần đổi khác — đây là câu hỏi nghiệp vụ, không phải kỹ thuật.

#### Cách kiểm chứng

Chưa có test tự động cho nhánh freshness (cần dữ liệu có mốc thời gian điều khiển được). Kiểm chứng bằng tay:

```bash
venv/Scripts/python.exe -c "
from datetime import datetime, UTC
from src.time_utils import utc_now
print('local  :', datetime.now())
print('utc_now:', utc_now())
print('chenh  :', (datetime.now() - utc_now()).total_seconds()/3600, 'gio')
"
```

Nếu máy ở UTC+7, dòng cuối in ra `7.0` — đó chính là sai số mà mã cũ cộng vào tuổi dữ liệu.

**Đề xuất bổ sung sau:** thêm test tạo bảng có `max_timestamp` cách hiện tại 18 giờ với ngưỡng 24 giờ, khẳng định status là `PASSED`.

---

## 4 · P2 — Độ tin cậy thống kê

---

### LOGIC-02 — Baseline không có cửa sổ trượt + truy vấn N+1

**File:** `src/services/anomaly_service.py` — hàm `detect_anomalies()`, phần nạp lịch sử

#### Tại sao phải sửa

Ba vấn đề chồng lên nhau, xếp theo mức nghiêm trọng:

1. **Không có cửa sổ thời gian** — đây là lỗi *đúng đắn*, không phải hiệu năng. Median/MAD tính trên toàn bộ lịch sử từ đầu. Sau một năm chạy, một đợt drift mới bị pha loãng bởi 365 điểm dữ liệu cũ ⇒ detector ngày càng chai lì.
2. **N+1 query** — vòng lặp qua N rule, mỗi rule một truy vấn.
3. **Lọc trong bộ nhớ** — thực ra hợp lý, vì tập loại trừ rất nhỏ. Đây là vấn đề nhỏ nhất.

Tôi gộp cả ba: một truy vấn duy nhất cho mọi rule, sắp xếp mới nhất trước, rồi cắt cửa sổ 30 đợt gần nhất cho từng rule khi duyệt kết quả.

#### Trước — nằm trong vòng lặp `for res in current_results`

```python
# 2. Iterate through rules and run detectors
for res in current_results:
    rule_id = res.rule_id
    checked_count = res.checked_count
    failed_count = res.failed_count
    current_rate = failed_count / checked_count if checked_count > 0 else 0.0

    # 2.1 Fetch historical results for this rule
    # Exclude: current run, failed runs (not SUCCEEDED/DONE), and true anomalies
    history_rows = (
        db.query(DqResultModel)
        .join(DqRunModel, DqRunModel.id == DqResultModel.run_id)
        .filter(
            DqResultModel.rule_id == rule_id,
            DqResultModel.run_id != execution_run_id,
            DqRunModel.dataset_id == current_run.dataset_id,
            or_(DqRunModel.status == "SUCCEEDED", DqRunModel.status == "DONE"),
        )
        .all()
    )
    # Filter true anomalies in memory
    history_rows = [r for r in history_rows if r.run_id not in excluded_run_ids]

    history_rates = [(r.failed_count / r.checked_count if r.checked_count > 0 else 0.0) for r in history_rows]

    sufficient_history = len(history_rates) >= 5
```

#### Sau — một truy vấn trước vòng lặp, cắt cửa sổ khi duyệt

```python
# Hằng số mới ở đầu module
# Số đợt chạy gần nhất dùng làm baseline cho mỗi rule (cửa sổ trượt).
# Không giới hạn cửa sổ thì median/MAD bị pha loãng bởi toàn bộ lịch sử từ đầu,
# khiến detector ngày càng chai lì với drift mới.
_HISTORY_WINDOW = 30

    # 1.1 Nạp lịch sử của TẤT CẢ rules trong một query duy nhất (trước đây là N+1: mỗi
    # rule một query). Sắp xếp mới nhất trước rồi cắt cửa sổ trượt _HISTORY_WINDOW cho
    # từng rule — baseline chỉ phản ánh giai đoạn gần đây thay vì toàn bộ lịch sử.
    # Loại trừ: đợt chạy hiện tại, đợt chạy lỗi, và đợt đã được Steward gán TRUE_ANOMALY.
    history_by_rule: dict[str, list[float]] = {}
    rule_ids = [res.rule_id for res in current_results]
    if rule_ids:
        history_rows = (
            db.query(DqResultModel)
            .join(DqRunModel, DqRunModel.id == DqResultModel.run_id)
            .filter(
                DqResultModel.rule_id.in_(rule_ids),
                DqResultModel.run_id != execution_run_id,
                DqRunModel.dataset_id == current_run.dataset_id,
                or_(DqRunModel.status == "SUCCEEDED", DqRunModel.status == "DONE"),
            )
            .order_by(DqRunModel.created_at.desc())
            .all()
        )
        for row in history_rows:
            if row.run_id in excluded_run_ids:
                continue
            bucket = history_by_rule.setdefault(row.rule_id, [])
            if len(bucket) < _HISTORY_WINDOW:
                bucket.append(
                    row.failed_count / row.checked_count if row.checked_count > 0 else 0.0
                )

    # 2. Iterate through rules and run detectors
    for res in current_results:
        rule_id = res.rule_id
        checked_count = res.checked_count
        failed_count = res.failed_count
        current_rate = failed_count / checked_count if checked_count > 0 else 0.0

        history_rates = history_by_rule.get(rule_id, [])

        sufficient_history = len(history_rates) >= 5
```

#### Cách kiểm chứng

```bash
venv/Scripts/python.exe -m pytest \
  tests/test_services/test_anomaly_baseline.py::test_baseline_uses_sliding_window_only -q
```

**Test kiểm tra gì:** tạo `_HISTORY_WINDOW + 10 = 40` đợt chạy lịch sử với `created_at` tăng dần, rồi khẳng định `signal["baseline"]["history_size"] == 30` — chứng minh chỉ 30 đợt mới nhất được dùng.

**Test hồi quy đi kèm:** `test_detect_anomalies_with_exclusions` (đã có sẵn) vẫn phải pass — nó kiểm tra rằng đợt chạy FAILED và đợt bị gán `TRUE_ANOMALY` vẫn bị loại khỏi baseline sau khi tôi đổi cách truy vấn.

---

### NEW-5 — `LIMIT` không kèm `ORDER BY`, baseline không tái lập được

**File:** `src/services/anomaly_service.py` — phần `VOLUME_DRIFT_DETECTOR`

#### Tại sao phải sửa

`LIMIT` không kèm `ORDER BY` là hành vi **không xác định** trong chuẩn SQL — cơ sở dữ liệu được quyền trả về 20 dòng bất kỳ. Baseline volume vì thế có thể thay đổi giữa các lần chạy *trên cùng một dữ liệu*, vi phạm nguyên tắc cơ bản: detector thống kê phải cho kết quả tái lập được.

#### Trước

```python
hist_profiles = (
    db.query(ProfileModel)
    .filter(ProfileModel.dataset_id == current_run.dataset_id, ProfileModel.generated_at < profile.generated_at)
    .limit(20)
    .all()
)
```

#### Sau

```python
# LIMIT không kèm ORDER BY là hành vi không xác định trong SQL — DB được quyền
# trả về 20 dòng bất kỳ, khiến baseline thay đổi giữa các lần chạy trên cùng dữ liệu.
hist_profiles = (
    db.query(ProfileModel)
    .filter(ProfileModel.dataset_id == current_run.dataset_id, ProfileModel.generated_at < profile.generated_at)
    .order_by(ProfileModel.generated_at.desc())
    .limit(_VOLUME_HISTORY_WINDOW)
    .all()
)
```

#### Cách kiểm chứng

```bash
venv/Scripts/python.exe -m pytest tests/test_services/ tests/test_dashboard_anomaly.py -q
```

**Ghi chú:** lỗi loại này rất khó bắt bằng test vì trên SQLite thứ tự trả về thường ổn định một cách tình cờ; nó chỉ lộ ra trên PostgreSQL khi bảng bị VACUUM hoặc kế hoạch truy vấn thay đổi. Đây là lý do phải sửa bằng lập luận, không chờ test đỏ.

---

### NEW-6 — MAD = 0 trả về hằng số 3.0 cho mọi sai lệch

**File:** `src/services/anomaly_service.py` — hàm `calculate_robust_zscore()`

#### Tại sao phải sửa

Khi lịch sử hoàn toàn phẳng (MAD = 0 — rất phổ biến vì phần lớn đợt chạy đều 0% vi phạm), mọi sai lệch dù nhỏ nhất đều nhận Z = 3.0. Lệch 0.001% và lệch 100% cho **cùng một điểm số**. Con số 3.0 là hằng số không có căn cứ thống kê.

Tôi thay bằng thang đo dự phòng tỉ lệ với độ lớn baseline: 10% của median, hoặc sàn tuyệt đối 0.5 điểm phần trăm khi median gần 0. Có cap ở ±10 để tránh giá trị vô hạn khi mẫu số quá nhỏ.

#### Trước — toàn bộ hàm

```python
def calculate_robust_zscore(current: float, history: list[float]) -> tuple[float, float, float]:
    """Calculate robust Z-score using Median and MAD.

    Formula: Robust Z = 0.6745 * (current - median) / MAD
    Returns:
        (robust_zscore, median, mad)
    """
    if not history:
        return 0.0, current, 0.0
    median = compute_median(history)
    mad = compute_mad(history, median)

    if mad == 0.0:
        # If MAD is 0, deviation of 0 has robust_z = 0.0, otherwise 3.0 (or higher if vastly different)
        return (0.0 if current == median else 3.0), median, 0.0

    robust_z = 0.6745 * (current - median) / mad
    return robust_z, median, mad
```

#### Sau — toàn bộ hàm

```python
# Hằng số mới ở đầu module
# Khi MAD = 0 (lịch sử hoàn toàn phẳng), dùng thang đo dự phòng thay cho hằng số cứng.
_MAD_ZERO_FLOOR = 0.005  # 0.5 điểm phần trăm
_MAX_ROBUST_Z = 10.0


def calculate_robust_zscore(current: float, history: list[float]) -> tuple[float, float, float]:
    """Calculate robust Z-score using Median and MAD.

    Formula: Robust Z = 0.6745 * (current - median) / MAD
    Returns:
        (robust_zscore, median, mad)
    """
    if not history:
        return 0.0, current, 0.0
    median = compute_median(history)
    mad = compute_mad(history, median)

    if mad == 0.0:
        # MAD = 0 nghĩa là lịch sử hoàn toàn phẳng — rất phổ biến khi mọi đợt chạy đều 0% vi phạm.
        # Trả về hằng số 3.0 cho mọi sai lệch khiến lệch 0.001% và lệch 100% nhận cùng một điểm.
        # Thay bằng thang đo dự phòng theo độ lớn baseline để phản hồi có phân cấp.
        if current == median:
            return 0.0, median, 0.0
        fallback_scale = max(abs(median) * 0.1, _MAD_ZERO_FLOOR)
        robust_z = 0.6745 * (current - median) / fallback_scale
        return max(-_MAX_ROBUST_Z, min(_MAX_ROBUST_Z, robust_z)), median, 0.0

    robust_z = 0.6745 * (current - median) / mad
    return robust_z, median, mad
```

#### Cách kiểm chứng

```bash
venv/Scripts/python.exe -m pytest tests/test_services/test_anomaly_baseline.py -q
```

**Hai test:**

- `test_zero_mad_gives_graded_response_not_constant` — với lịch sử phẳng `[0.05]*6`, khẳng định điểm của sai lệch 0.05% *khác* điểm của sai lệch 85%, và nhỏ hơn về giá trị tuyệt đối.
- `test_zero_mad_identical_value_is_not_anomalous` — giá trị trùng khít median vẫn phải cho Z = 0.

**Kiểm tra hồi quy quan trọng:** `test_detect_anomalies_with_exclusions` có lịch sử `[0.05, 0.05, 0.06, 0.05, 0.05]` ⇒ MAD = 0, và giá trị hiện tại 0.40. Mã cũ cho Z = 3.0 ⇒ score 0.80. Mã mới cho Z bị cap ở 10.0 ⇒ score 1.0. Test khẳng định `score >= 0.80` nên vẫn pass, và giờ điểm phản ánh đúng mức độ nghiêm trọng.

---

## 5 · P3 — Hardcode và metadata sai

Không gây sai kết quả nghiệp vụ, nhưng làm hỏng khả năng quan sát hệ thống (observability) và chất lượng đầu vào cho LLM.

---

### BUG-05 — Ghi tên model bịa và độ trễ luôn bằng 0

**File:**

- `src/agents/nodes/persist_analysis_node.py`
- `src/agents/nodes/steward_insights_node.py`
- `src/agents/graph.py` (`run_anomaly_graph`)

#### Tại sao phải sửa

Bảng `anomaly_hypotheses` ghi `model_name = "gemini-3.5-flash"` trong khi hệ thống thực tế gọi provider cấu hình trong `settings.llm_provider` (mặc định OpenAI, `.env` đang đặt `gpt-4o-mini`). Ngoài ra `latency_ms` luôn ghi 0 và `prompt_version` là chuỗi hằng.

**Toàn bộ cột observability của bảng này là dữ liệu bịa.** Nếu sau này dùng nó để tính chi phí token hay so sánh chất lượng model, mọi kết luận sẽ sai.

Sửa hai đầu: node gọi LLM *đo* độ trễ thật và ghi tên model thật vào metadata; node lưu trữ *đọc* từ metadata, có hàm dự phòng nếu metadata trống.

#### Trước — `persist_analysis_node.py`

```python
            # Create AnomalyHypotheses
            for h in hypotheses:
                hyp_record = AnomalyHypothesisModel(
                    id=f"hyp-{uuid.uuid4().hex[:12]}",
                    anomaly_run_id=anomaly_run_id,
                    hypothesis_type=h["hypothesis_type"],
                    summary=h["summary"],
                    confidence=float(h["confidence"]),
                    ...
                    model_name=state.get("metadata", {}).get("model_name", "gemini-3.5-flash") if isinstance(state.get("metadata"), dict) else "gemini-3.5-flash",
                    prompt_version="1.0.0",
                    latency_ms=0,
                    fallback_used=state.get("hypothesis_status") == "FALLBACK_USED"
                )
```

#### Sau — `persist_analysis_node.py`

```python
# Thêm mới ở đầu module
#: Phiên bản prompt sinh giả thuyết — tăng khi đổi nội dung prompt trong steward_insights_node.
HYPOTHESIS_PROMPT_VERSION = "1.0.0"


def _resolve_model_name() -> str:
    """Tên model thực tế theo cấu hình provider hiện hành."""
    try:
        from src.config import get_settings

        settings = get_settings()
        return str(getattr(settings, f"{settings.llm_provider}_model_name", settings.llm_provider))
    except Exception:
        return "unknown"


            # Model thật do steward_insights_node dùng, KHÔNG phải hằng số hardcode.
            # Bản cũ luôn ghi "gemini-3.5-flash" trong khi hệ thống gọi provider cấu hình
            # trong settings (mặc định OpenAI) — toàn bộ cột observability là dữ liệu bịa.
            metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
            model_name = metadata.get("model_name") or _resolve_model_name()
            hypothesis_latency_ms = int(metadata.get("hypothesis_latency_ms") or 0)

            # Create AnomalyHypotheses
            for h in hypotheses:
                hyp_record = AnomalyHypothesisModel(
                    id=f"hyp-{uuid.uuid4().hex[:12]}",
                    anomaly_run_id=anomaly_run_id,
                    hypothesis_type=h["hypothesis_type"],
                    summary=h["summary"],
                    confidence=float(h["confidence"]),
                    ...
                    model_name=model_name,
                    prompt_version=HYPOTHESIS_PROMPT_VERSION,
                    latency_ms=hypothesis_latency_ms,
                    fallback_used=state.get("hypothesis_status") == "FALLBACK_USED"
                )
```

#### Trước — `steward_insights_node.py`

```python
        logger.info("Invoking LLM for structured hypotheses...")
        response = await structured_llm.ainvoke(prompt)

        ...

    return {
        "hypotheses": hypotheses_list,
        "hypothesis_status": "FALLBACK_USED" if fallback_used else "SUCCEEDED"
    }
```

#### Sau — `steward_insights_node.py`

```python
        logger.info("Invoking LLM for structured hypotheses...")
        llm_started_at = time.perf_counter()
        response = await structured_llm.ainvoke(prompt)
        latency_ms = int((time.perf_counter() - llm_started_at) * 1000)

        ...

    # Ghi lại model thật và độ trễ thật để persist_analysis_node lưu đúng vào
    # anomaly_hypotheses thay vì hằng số hardcode.
    model_name = str(
        getattr(settings, f"{settings.llm_provider}_model_name", settings.llm_provider)
    )
    return {
        "hypotheses": hypotheses_list,
        "hypothesis_status": "FALLBACK_USED" if fallback_used else "SUCCEEDED",
        "metadata": {
            **(state.get("metadata") or {}),
            "model_name": model_name,
            "hypothesis_latency_ms": latency_ms,
        },
    }
```

#### Trước / Sau — `graph.py`, hàm `run_anomaly_graph()`

```python
# Trước
    initial_state = {
        "anomaly_run_id": anomaly_run_id,
        "execution_run_id": execution_run_id,
        "dataset_id": dataset_id,
        "detector_config_version": "anomaly-v1",
        "metadata": {
            "model_name": "gemini-3.5-flash",
        }
    }

# Sau
    initial_state = {
        "anomaly_run_id": anomaly_run_id,
        "execution_run_id": execution_run_id,
        "dataset_id": dataset_id,
        "detector_config_version": "anomaly-v1",
        # KHÔNG hardcode model ở đây: steward_insights_node ghi lại model thật nó đã gọi
        # (theo settings.llm_provider) vào metadata để persist_analysis_node lưu chính xác.
        "metadata": {},
    }
```

#### Cách kiểm chứng

```bash
venv/Scripts/python.exe -c "
from src.agents.nodes.persist_analysis_node import _resolve_model_name
print('model thuc te =', _resolve_model_name())
"
```

**Kết quả:** `model thuc te = gpt-4o-mini` — đúng với `.env` (`PROVIDER=openai`, `OPENAI_MODEL=gpt-4o-mini`). Trước khi sửa, DB luôn ghi `gemini-3.5-flash`.

Sau khi chạy Run 3 thật:

```sql
SELECT model_name, latency_ms, prompt_version
FROM anomaly_hypotheses ORDER BY id DESC LIMIT 5;
-- kỳ vọng: gpt-4o-mini, latency > 0, 1.0.0
```

```bash
venv/Scripts/python.exe -m pytest tests/test_agents/test_steward_insights_node.py -q
```

---

### NEW-7 · NEW-8 — Node báo cáo mới: hardcode provider và docstring nói sai

**File:** `src/agents/nodes/report_writer_node.py` — `report_writer_node()` và `_write_report_file()`

#### Tại sao phải sửa

Đây là hai lỗi do *chính bản pull mới* mang vào, cùng file với tính năng viết báo cáo Steward.

**NEW-7:** node hardcode `get_llm("openai")` trong khi node anh em ngay cạnh (`steward_insights_node`) dùng đúng `settings.llm_provider`. Nếu đội chuyển sang Gemini/Mistral hoặc thiếu `OPENAI_API_KEY`, exception bị nuốt bởi `except` phía dưới ⇒ báo cáo **âm thầm rơi về template fallback** mà người dùng tưởng là báo cáo AI.

**NEW-8:** docstring của node cam kết "File output idempotent theo execution_run_id (overwrite khi retry)", nhưng tên file lại chèn timestamp ⇒ mỗi lần retry đẻ ra một file mới. Code và tài liệu mâu thuẫn nhau; tôi sửa code theo cam kết vì đó mới là hành vi đúng.

#### Trước

```python
    # NEW-7 — trong report_writer_node()
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = get_llm("openai", temperature=0.2)


    # NEW-8 — trong _write_report_file()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = report_dir / f"steward_report_{timestamp}_{execution_run_id}.md"
```

#### Sau

```python
    # NEW-7
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        # Dùng provider đã cấu hình (settings.llm_provider) như mọi node LLM khác.
        # Hardcode "openai" khiến node phớt lờ cấu hình: khi đội chuyển sang Gemini/Mistral
        # hoặc không có OPENAI_API_KEY, exception bị nuốt ở dưới và báo cáo LLM âm thầm
        # rơi về template fallback mà người dùng không hề biết.
        from src.config import get_settings

        llm = get_llm(get_settings().llm_provider, temperature=0.2)


    # NEW-8
    # Tên file KHÔNG chứa timestamp: docstring của node cam kết idempotent theo
    # execution_run_id (ghi đè khi retry). Có timestamp thì mỗi lần chạy lại đẻ ra một file mới.
    out_path = report_dir / f"steward_report_{execution_run_id}.md"
```

#### Cách kiểm chứng

```bash
venv/Scripts/python.exe -m pytest tests/test_agents/test_report_writer_node.py -q
```

**Bộ test có sẵn của node** (do bản pull mới thêm) vẫn phải pass — nó mock `get_llm` nên không phụ thuộc provider, và mock `_write_report_file` nên không phụ thuộc tên file.

**Kiểm chứng NEW-8 bằng tay:** chạy Run 3 hai lần cho cùng một `execution_run_id`, rồi đếm file:

```bash
ls output/reports/steward_report_*.md | wc -l
# kỳ vọng: 1 file duy nhất (trước đây: 2)
```

**Kiểm chứng NEW-7:** đổi `PROVIDER=mistral` trong `.env`, chạy Run 3, và xem log — dòng `Gọi LLM để viết báo cáo` phải đi kèm lời gọi Mistral, không phải OpenAI.

---

### BUG-03 — Tiêu đề rule bị cắt từ `rule_id` thành chuỗi vô nghĩa

**File:** `src/agents/nodes/persist_report_node.py` — `_save_decoupled_run()`

#### Tại sao phải sửa

`rule_id.split(".")[-1]` biến `"yellow_tripdata.fare_amount.RANGE"` thành `"RANGE"`. Đây không phải tên rule, mà là tên *loại* rule.

Hệ quả dây chuyền: `steward_insights_node` truy vấn `DqResultModel.rule_title` để làm ngữ cảnh cho LLM chẩn đoán nguyên nhân. LLM nhận được một tiêu đề vô nghĩa thay vì mô tả nghiệp vụ ⇒ giả thuyết kém chất lượng. Chuỗi lỗi này rất khó phát hiện vì nó chỉ biểu hiện thành "AI trả lời không hay".

Sửa bằng cách tra ngược từ `state["approved_rules"]` — nơi có tên nghiệp vụ thật do LLM đề xuất và Steward đã duyệt. Vẫn giữ chuỗi fallback nhiều tầng để không bao giờ ghi rỗng.

#### Trước

```python
            for res in test_results:
                ...
                dq_res = DqResultModel(
                    run_id=test_run_id,
                    rule_id=res.get("rule_id", ""),
                    rule_title=res.get("rule_id", "").split(".")[-1] or "Rule",
                    status=r_status,
                    ...
                )
```

#### Sau

```python
            # Tiêu đề rule lấy từ luật đã duyệt. Trước đây cắt đuôi rule_id
            # ("yellow_tripdata.fare_amount.RANGE" -> "RANGE") nên steward_insights_node
            # gửi cho LLM một tiêu đề vô nghĩa thay vì mô tả nghiệp vụ thật.
            titles_by_rule_id = {
                rule.get("rule_id"): (
                    rule.get("rule_name")
                    or rule.get("rule_description")
                    or rule.get("title")
                )
                for rule in (state.get("approved_rules") or [])
                if rule.get("rule_id")
            }

            for res in test_results:
                ...
                rule_id_value = res.get("rule_id", "")
                dq_res = DqResultModel(
                    run_id=test_run_id,
                    rule_id=rule_id_value,
                    rule_title=(
                        titles_by_rule_id.get(rule_id_value)
                        or res.get("rule_title")
                        or rule_id_value
                        or "Rule"
                    ),
                    status=r_status,
                    ...
                )
```

#### Cách kiểm chứng

Chạy Run 2 rồi so sánh trực tiếp trong DB:

```sql
SELECT rule_id, rule_title FROM dq_results ORDER BY id DESC LIMIT 5;
-- trước: rule_title = "RANGE", "NOT_NULL"
-- sau  : rule_title = "Cước phí từ 0 đến 100", "Cước phí không được rỗng"
```

```bash
venv/Scripts/python.exe -m pytest tests/test_agents/test_persist_report_node.py tests/test_agents/test_execution_nodes.py -q
```

**Ghi chú:** test hiện tại của `persist_report_node` không truyền `approved_rules` nên đi vào nhánh fallback (dùng `rule_id` đầy đủ thay vì chuỗi cắt) — vẫn tốt hơn hành vi cũ. Nên bổ sung một test truyền `approved_rules` để khoá nhánh chính.

---

### BUG-10 — Ba runner mặc định về dataset NYC taxi

**File:** `src/agents/graph.py` — `run_proposal_graph()`, `run_execution_graph()`, `run_anomaly_graph()`, `main()`

#### Tại sao phải sửa

Nếu caller quên truyền `dataset_id`, cả ba pipeline sẽ âm thầm chạy trên dataset NYC taxi — không có cảnh báo nào. Với một nền tảng hướng đa dataset, đây là loại lỗi cực khó truy vết.

Tôi bắt buộc tham số ở tầng thư viện, nhưng **giữ** giá trị mặc định cho CLI (nơi nó là tiện ích hợp lý và hiển thị rõ ràng), chuyển nó thành hằng số có tên để chỉ còn một nguồn. Tôi đã kiểm tra: **mọi call site hiện tại đều truyền `dataset_id`**, nên thay đổi này không phá vỡ gì.

#### Trước

```python
logger = logging.getLogger("graph_runner")

async def run_proposal_graph(
    dataset_id: str = "dataset-nyc-yellow-taxi-50k",
    connection_string: str | None = None,
    ...

async def run_execution_graph(
    dataset_id: str = "dataset-nyc-yellow-taxi-50k",
    proposal_run_id: str | None = None,
) -> dict:

async def run_anomaly_graph(
    execution_run_id: str,
    dataset_id: str = "dataset-nyc-yellow-taxi-50k",
) -> dict:

# trong main()
    dataset_id = args[1] if len(args) > 1 else "dataset-nyc-yellow-taxi-50k"
```

#### Sau

```python
logger = logging.getLogger("graph_runner")

#: Dataset mặc định CHỈ dùng cho CLI (`python -m src.agents.graph`). Các hàm runner bên
#: dưới bắt buộc truyền dataset_id — trước đây cả ba đều mặc định về dataset NYC taxi nên
#: caller quên truyền sẽ âm thầm chạy trên dataset sai mà không có cảnh báo nào.
DEFAULT_CLI_DATASET_ID = "dataset-nyc-yellow-taxi-50k"

async def run_proposal_graph(
    dataset_id: str,
    connection_string: str | None = None,
    ...

async def run_execution_graph(
    dataset_id: str,
    proposal_run_id: str | None = None,
) -> dict:

async def run_anomaly_graph(
    execution_run_id: str,
    dataset_id: str,
) -> dict:

# trong main()
    dataset_id = args[1] if len(args) > 1 else DEFAULT_CLI_DATASET_ID
```

#### Cách kiểm chứng

```bash
venv/Scripts/python.exe -m pytest tests/test_agents/test_graph.py -q
```

Kiểm tra không còn call site nào thiếu tham số:

```bash
grep -rn "run_proposal_graph(\|run_execution_graph(\|run_anomaly_graph(" --include=*.py src tests
```

Mọi dòng kết quả phải có `dataset_id=`. Nếu thiếu, Python sẽ ném `TypeError` ngay khi gọi — chuyển một lỗi âm thầm thành một lỗi ồn ào, đúng như mong muốn.

---

### BUG-01 — Standalone harness là code chết

**File:** `src/agents/nodes/test_runner_node.py` — hàm `main()`

#### Tại sao phải sửa

Harness này có **ba tầng lỗi** chồng nhau, khiến nó không thể chạy:

1. State không có `dbt_validation_valid` ⇒ `test_runner_node` ném `RuntimeError` ngay dòng đầu.
2. Đếm status theo từ vựng cũ `PASSED/FAILED` ⇒ luôn ra 0.
3. In kết quả bằng khoá cũ `total_rows`, `violation_count` ⇒ `KeyError`.

Việc không ai phát hiện ra điều này là bằng chứng harness đã không được dùng từ lâu. Tôi chọn **sửa thay vì xoá** để giữ công cụ debug, và ghi rõ trong comment rằng việc bỏ qua chốt chặn dbt ở đây là có chủ đích.

#### Trước

```python
    state: AgentState = {
        "dataset_id": "yellow_tripdata",
        "test_run_id": "exec_standalone_test",
        "generated_tests": valid_tests,
    }

    res = await test_runner_node(state)

    results = res.get("test_results", [])
    passed_count = sum(1 for r in results if r["status"] == "PASSED")
    failed_count = sum(1 for r in results if r["status"] == "FAILED")
    error_count = sum(1 for r in results if r["status"] == "ERROR")

    print(f"\n📊 Kết quả thực thi ({len(results)} rules): ...")
    for idx, r in enumerate(results[:10], 1):
        status_icon = "✅ PASSED" if r["status"] == "PASSED" else ("❌ FAILED" if r["status"] == "FAILED" else "⚠️ ERROR")
        print(f"\n[{idx}] Rule: {r['rule_id']} ➔ {status_icon}")
        print(f"    Tổng dòng: {r['total_rows']} | Vi phạm: {r['violation_count']} | Tỷ lệ lỗi: {r['violation_rate']:.2%}")
        print(f"    Thời gian: {r['duration_ms']} ms")
        if r.get("sample_failures"):
            print(f"    Dòng lỗi mẫu (tối đa 5 dòng): {json.dumps(r['sample_failures'], ensure_ascii=False, default=str)}")
```

#### Sau

```python
state: AgentState = {
    "dataset_id": "yellow_tripdata",
    "test_run_id": "exec_standalone_test",
    "generated_tests": valid_tests,
    # Harness chạy tay bỏ qua chốt chặn dbt một cách CÓ CHỦ ĐÍCH: nó đọc thẳng file
    # test đã sinh sẵn. Thiếu cờ này, test_runner_node ném RuntimeError ngay dòng đầu
    # nên toàn bộ harness là code chết, không ai chạy được.
    "dbt_validation_valid": True,
}

res = await test_runner_node(state)

# test_runner_node trả về status ĐÃ chuẩn hoá (PASS/FAIL), không phải PASSED/FAILED.
results = res.get("test_results", [])
passed_count = sum(1 for r in results if r["status"] == "PASS")
failed_count = sum(1 for r in results if r["status"] == "FAIL")
error_count = sum(1 for r in results if r["status"] == "ERROR")

print(f"\n📊 Kết quả thực thi ({len(results)} rules): ...")
for idx, r in enumerate(results[:10], 1):
    status_icon = "PASS" if r["status"] == "PASS" else ("FAIL" if r["status"] == "FAIL" else "ERROR")
    print(f"\n[{idx}] Rule: {r['rule_id']} -> {status_icon}")
    print(f"    Tong dong: {r['checked_count']} | Vi pham: {r['failed_count']} | Ty le loi: {r['violation_rate']:.2%}")
    print(f"    Thoi gian: {r['duration_ms']} ms")
    if r.get("sample_refs"):
        print(
            f"    ID dong loi mau (toi da {SAMPLE_FAILURE_LIMIT}): {json.dumps(r['sample_refs'], ensure_ascii=False, default=str)}"
        )
```

#### Cách kiểm chứng

Chạy trực tiếp harness (cần có sẵn file test đã sinh trong `output/test_generator/`):

```bash
venv/Scripts/python.exe -m src.agents.nodes.test_runner_node
```

- **Trước:** `RuntimeError: test_runner requires a successfully validated dbt artifact`
- **Sau:** in ra bảng kết quả với số PASS/FAIL khác 0 và danh sách ID dòng lỗi.

Kiểm tra cú pháp nhanh:

```bash
venv/Scripts/python.exe -c "import ast,pathlib; ast.parse(pathlib.Path('src/agents/nodes/test_runner_node.py').read_text(encoding='utf-8')); print('OK')"
```

---

## 6 · Lỗi phát hiện thêm khi chạy test

Ba lỗi này không có trong danh sách ban đầu. Chúng lộ ra khi chạy full suite lần đầu và thấy 9 test fail sẵn từ trước — hai trong số đó là **lỗi crash ở production**, không chỉ trong test.

---

### PRE-2 — ORM model thiếu cột mà migration đã tạo và API đang đọc

**File:** `src/models/database.py` — class `RuleProposalModel`

#### Tại sao phải sửa

Ba nguồn không đồng bộ với nhau:

- `rule_store.py:420` — migration *tạo* cột `parameter_provenance` và `assumptions` trong bảng vật lý.
- `routes.py:978, 1021, 1056, 1215` — API *đọc/ghi* hai cột này.
- `database.py` — ORM model **không khai báo** chúng.

Kết quả: mọi truy cập `prop.parameter_provenance` ném `AttributeError`, tức `GET /rule-proposals` và `PATCH /rule-proposals/{id}` **lỗi 500 ở production**, không chỉ trong test.

#### Trước

```python
    rule_name: Mapped[str] = mapped_column(String(256), nullable=False, default="Rule proposal")
    business_rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    proposal_basis: Mapped[str] = mapped_column(String(32), nullable=False, default="DATA_PROFILE")
    evidence: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    confidence_breakdown: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
```

#### Sau

```python
    rule_name: Mapped[str] = mapped_column(String(256), nullable=False, default="Rule proposal")
    business_rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    proposal_basis: Mapped[str] = mapped_column(String(32), nullable=False, default="DATA_PROFILE")
    evidence: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # Hai cột dưới đây được `_migrate_local_proposal_columns` tạo trong bảng vật lý và được
    # routes.py đọc/ghi (parameter_provenance, assumptions), nhưng trước đây không được khai
    # báo trong ORM model — mọi truy cập `prop.parameter_provenance` đều ném AttributeError.
    parameter_provenance: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    assumptions: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    confidence_breakdown: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
```

#### Cách kiểm chứng

```bash
venv/Scripts/python.exe -m pytest tests/test_proposals.py tests/test_agents/test_rule_proposal_core_evidence.py -q
```

**Trước khi sửa:** `TypeError: 'parameter_provenance' is an invalid keyword argument for RuleProposalModel`

Kiểm tra ORM khớp bảng vật lý:

```bash
venv/Scripts/python.exe -c "
from sqlalchemy import inspect
from src.services.rule_store import get_engine, init_db
from src.models.database import RuleProposalModel
init_db()
db_cols = {c['name'] for c in inspect(get_engine()).get_columns('rule_proposals')}
orm_cols = {c.name for c in RuleProposalModel.__table__.columns}
print('DB thieu:', orm_cols - db_cols)
print('ORM thieu:', db_cols - orm_cols)
"
```

Cả hai dòng phải in ra `set()`. **Đây là kiểu kiểm tra nên đưa vào CI** để chặn schema drift tái diễn.

---

### PRE-1 — Cột NOT NULL mà không call site nào truyền giá trị

**File:** `src/models/database.py` — class `RuleConfigurationModel`

#### Tại sao phải sửa

`model_name` khai `nullable=False` và không có giá trị mặc định, nhưng cả ba nơi tạo bản ghi (`routes.py:1108, 1178, 1302`) đều không truyền nó, và `RuleConfigurationSchema` cũng không phơi nó ra API. Nghĩa là cột này vô dụng nhưng **chặn mọi lần tạo cấu hình rule** bằng `IntegrityError`.

Class còn khai `updated_at` **hai lần** — lỗi copy-paste.

Tôi chọn thêm `default` thay vì đổi sang `nullable=True`: giá trị mặc định thoả mãn ràng buộc NOT NULL trên *cả* schema cũ đang tồn tại lẫn schema mới, nên **không cần migration** cho các DB đã chạy.

#### Trước

```python
last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
next_run_at: Mapped[datetime | None] = mapped_column(DateTime)
updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)
model_name: Mapped[str] = mapped_column(String(128), nullable=False)
created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)
```

#### Sau — bỏ khai báo trùng, thêm default

```python
last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
next_run_at: Mapped[datetime | None] = mapped_column(DateTime)
# Cột NOT NULL nhưng không call site nào truyền giá trị (routes.py:1108, 1178, 1302) và
# RuleConfigurationSchema cũng không phơi ra — mọi lần tạo cấu hình đều ném IntegrityError.
# Đặt default để insert hợp lệ trên cả schema cũ (đã NOT NULL) lẫn schema mới.
model_name: Mapped[str] = mapped_column(String(128), nullable=False, default="unspecified")
created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)
```

#### Cách kiểm chứng

```bash
venv/Scripts/python.exe -m pytest \
  tests/test_admin_config.py \
  tests/test_runner.py::test_dq_run_and_failed_ids_capped_at_20 -q
```

**Trước khi sửa:** `sqlite3.IntegrityError: NOT NULL constraint failed: rule_configurations.model_name`

**Kiểm tra qua API:** gọi `PATCH /api/v1/rule-proposals/{id}/configuration` trên một rule đã APPROVED — trước đây trả 500, giờ phải trả 200.

---

### NEW-9 — Tham số độ tin cậy được khai báo nhưng không dòng nào dùng

**File:** `src/services/dashboard_anomaly.py` — hàm `detect_dashboard_anomalies()`

#### Tại sao phải sửa

Chữ ký hàm khai bốn tham số điều chỉnh độ nhạy — `minimum_history`, `static_threshold`, `z_score_threshold`, `minimum_checked_count` — nhưng sau khi hàm được viết lại thành adapter gọi `anomaly_service`, **không tham số nào còn được dùng**. Hệ quả: một rule chạy trên 50 dòng vẫn nổi lên như bất thường thật.

Test `test_small_checks_do_not_raise_unreliable_anomaly` đã fail sẵn từ trước, ghi lại đúng yêu cầu này. Tôi khôi phục hành vi mà chữ ký hàm vẫn đang hứa hẹn.

#### Trước

```python
        checked_count = res_model.checked_count
        failed_count = res_model.failed_count
        current_rate = failed_count / checked_count if checked_count > 0 else 0.0

        baseline = sig.get("baseline", {})
```

#### Sau

```python
checked_count = res_model.checked_count
failed_count = res_model.failed_count

# Mẫu quá nhỏ thì tỷ lệ vi phạm không đủ tin cậy để báo động cho Steward.
# `minimum_checked_count` đã được khai báo trong chữ ký hàm từ đầu nhưng không
# dòng nào dùng tới — một rule chạy trên 50 dòng vẫn nổi lên như bất thường thật.
if checked_count < minimum_checked_count:
    logger.debug(
        "Bỏ qua signal %s: chỉ kiểm tra %d dòng (< %d), độ tin cậy không đủ.",
        rule_id,
        checked_count,
        minimum_checked_count,
    )
    continue

current_rate = failed_count / checked_count if checked_count > 0 else 0.0

baseline = sig.get("baseline", {})
```

#### Cách kiểm chứng

```bash
venv/Scripts/python.exe -m pytest tests/test_dashboard_anomaly.py -q
```

**Hai test đối nghịch nhau, cả hai đều phải pass:**

- `test_small_checks_do_not_raise_unreliable_anomaly` — 50/50 dòng vi phạm (100%) nhưng mẫu nhỏ ⇒ danh sách bất thường phải rỗng.
- `test_warm_history_uses_z_score` — 120/1000 dòng, mẫu đủ lớn ⇒ vẫn phải báo bất thường bình thường.

Cặp test này khoá cả hai chiều: không quá nhạy, cũng không quá điếc.

---

## 7 · Không sửa — và tại sao

### 4 test vẫn fail, đều nằm ngoài phạm vi

**3 test `parameter_provenance`** (`tests/test_agents/test_rule_proposal_core_evidence.py`) — mô tả hành vi validator *chưa được cài đặt*: `active_parameters` hiện tính cả tham số mặc định; validator dùng `set` nên không phát hiện entry trùng; `_stamp_rule` không xuất khoá này ra dict. Sửa đòi hỏi quyết định thiết kế "tham số nào được coi là đang sử dụng" — không nên đoán thay đội.

**`test_loopback_cors_accepts_127_origin`** — *không phải lỗi code*. File `.env` khai `CORS_ORIGINS` hai lần (dòng 30 và 31), dòng sau đè dòng trước thành `http://localhost:8000`. Hệ quả thật ngoài test: **frontend Vite ở cổng 5173 đang bị CORS chặn**. Không sửa `.env` vì đó là file cá nhân, không nằm trong git — xoá dòng 31 là xong.

### Ngoài phạm vi, cần quyết định kiến trúc trước khi code

- Router `/api/v1/dq/*` — 13 endpoint không có xác thực, CSRF hay phân quyền. Cần quyết trước: giữ hay xoá (nó phục vụ đường agent vốn không dùng trong sản phẩm).
- `dq_score` / `dq_grade` — không được tính ở đâu cả; cần chốt công thức trước khi cài.
- `llm_repair_node`, `validate_sql_node`, `chroma_rag_tool` — code chết, không graph nào import. Nối vào hay xoá?
- Hai pipeline song song — Graph 3 và node `report_writer` mới đều không đến được Web UI.

### Hai mục xác nhận KHÔNG phải lỗi

- **BUG-07** — `rule_id` *có* kết thúc bằng `.FRESHNESS` (công thức tại `rule_proposer_node.py:440`), nên phép lọc freshness signal hoạt động đúng thiết kế.
- **LOGIC-01** — `dbt_validation_valid` luôn là `bool` thật (từ `result.returncode == 0`), nên `is not True` an toàn, thậm chí an toàn hơn `not x`.

---

## 8 · Thay đổi hợp đồng — đọc trước khi deploy

| Thay đổi                                                           | Ảnh hưởng                                                                                                                                                                | Hành động cần làm                                                                                              |
| -------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `sample_failures` / `failed_row_ids` giờ là `list[str]`      | Đồng thời*sửa* một lỗi API tiềm ẩn: `routes.py:246` vốn khai `list[str]` nhưng đường agent ghi dict ⇒ `GET /dq-runs/{id}/results` sẽ lỗi validation | Không cần làm gì; frontend đã kỳ vọng đúng kiểu này                                                     |
| `hitl_semantic_gate_node` trả `pause_reason` thay vì `error` | Bất kỳ đoạn code nào đọc`state["error"] == "AWAITING_SEMANTIC_REVIEW"`                                                                                             | Đã cập nhật test tương ứng; grep xác nhận không còn chỗ nào khác                                      |
| 3 hàm runner bắt buộc truyền`dataset_id`                       | Caller thiếu tham số sẽ ném`TypeError`                                                                                                                                | Mọi call site hiện tại đã truyền — không có breakage                                                       |
| Điểm anomaly sẽ**cao hơn** với cùng dữ liệu            | Nhiều cảnh báo hơn xuất hiện — đây là hành vi đúng, không phải hỏng                                                                                         | Báo trước cho Data Steward. Ngưỡng 0.45 / 0.70 giữ nguyên; nên hiệu chỉnh lại sau vài tuần chạy thật |
| Dữ liệu`dq_runs` cũ vẫn lệch 7 giờ                           | Fix chỉ áp dụng cho bản ghi mới                                                                                                                                        | Quyết định: migrate dữ liệu cũ hay chấp nhận một mốc cắt                                                 |
| `dbt_status` giờ có thể là `SKIPPED`                         | Dashboard nào đọc cột này cần xử lý giá trị thứ ba                                                                                                               | Kiểm tra UI hiển thị đúng; hoặc cài`dbt-core` để gate chạy thật                                        |

---

## 9 · Cách chạy toàn bộ kiểm chứng

> **Lưu ý môi trường:** `pytest` *không* có trong `.venv/`, chỉ có trong `venv/`. Mọi lệnh dưới đây dùng `venv/Scripts/python.exe`. Không chạy hai tiến trình pytest song song — chúng dùng chung thư mục `.pytest_tmp/` và sẽ tranh file SQLite của nhau.

### Toàn bộ suite

```bash
venv/Scripts/python.exe -m pytest -q -p no:randomly
# kỳ vọng: 4 failed, 197 passed, 2 skipped  (~2 phút)
# 4 failed = 3 test parameter_provenance + 1 test CORS, đều nằm ngoài phạm vi
```

### Chỉ các test regression đã thêm

```bash
venv/Scripts/python.exe -m pytest \
  tests/test_agents/test_persist_report_node.py \
  tests/test_agents/test_proposal_run_status.py \
  tests/test_agents/test_runner_safety.py \
  tests/test_services/test_anomaly_baseline.py \
  tests/test_services/test_anomaly_service.py -q
# kỳ vọng: tất cả pass
```

### Kiểm tra chốt chặn bảo mật vẫn sống dưới cờ tối ưu

```bash
venv/Scripts/python.exe -O -m pytest tests/test_agents/test_runner_safety.py -q
# Với mã cũ dùng assert, lệnh này sẽ FAIL vì guard bị xoá khỏi bytecode
```

### Kiểm tra import sạch toàn bộ module đã sửa

```bash
DISABLE_TRACING=1 venv/Scripts/python.exe -c "
import importlib
for m in ['src.services.anomaly_service','src.services.dashboard_anomaly',
          'src.agents.nodes.persist_report_node','src.agents.nodes.test_runner_node',
          'src.agents.nodes.dbt_validation','src.agents.nodes.validate_dbt_project_node',
          'src.agents.nodes.hitl_semantic_gate_node','src.agents.nodes.rule_proposer_node',
          'src.agents.graph','src.agents.nodes.persist_analysis_node',
          'src.agents.nodes.steward_insights_node','src.agents.nodes.report_writer_node',
          'src.agents.state','src.models.database']:
    importlib.import_module(m)
print('OK')
"
```

### Kiểm chứng end-to-end bằng CLI (cần API key thật)

```bash
# Run 1 → duyệt tự động → Run 2 → Run 3
venv/Scripts/python.exe -m src.agents.graph all dataset-nyc-yellow-taxi-50k
```

Sau đó kiểm tra ba thứ:

1. `output/reports/test_run_*.json` → `passed_count` / `failed_count` **khác 0**
2. `output/reports/steward_report_<run_id>.md` → đúng **1 file**, không có timestamp
3. `SELECT dbt_status, created_at FROM dq_runs ORDER BY created_at DESC LIMIT 1;` → `dbt_status = SKIPPED`, `created_at` là giờ UTC (không lệch 7h)

### Đề xuất cho CI

Thêm bước kiểm tra schema drift (script ở mục PRE-2) và chạy suite với cả `-O` lẫn không, để hai lớp lỗi vừa sửa không quay lại.

---

*Bản HTML tương tác của tài liệu này: https://claude.ai/code/artifact/d166953d-95fc-4076-9a7b-f52619b5468d*
