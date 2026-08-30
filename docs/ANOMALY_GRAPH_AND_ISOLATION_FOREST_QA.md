# Q&A Chi Tiết Về Kiến Trúc Anomaly Graph & Isolation Forest (Graph 3)

Tài liệu này tổng hợp toàn bộ các câu hỏi và câu trả lời chuyên sâu về kiến trúc luồng **Graph 3 (Anomaly Investigation Graph)**, cơ chế hoạt động của các bộ **Detectors**, luồng dữ liệu trong Database, và chính sách triển khai **Rollout Policy (Isolation Forest)** trong hệ thống RidePulse DQ.

---

## 📑 Mục Lục
1. [Câu hỏi 1: Muốn chạy thử nghiệm phần Isolation Forest thì chạy Graph 3 hay chạy luồng nào?](#câu-hỏi-1)
2. [Câu hỏi 2: Trong Isolation Forest và Graph 3 hiện tại có những loại traces/telemetry nào?](#câu-hỏi-2)
3. [Câu hỏi 3: Giải thích chi tiết bằng lời về luồng Anomaly Graph (Run 3) để đối chiếu với sơ đồ kiến trúc](#câu-hỏi-3)
4. [Câu hỏi 4: Giải thích chi tiết các bộ Detectors: Business Invariant, Volume Drift, Freshness, Execution Health (Mục 1, 3, 4, 5 trong Bước 3.1)](#câu-hỏi-4)
5. [Câu hỏi 5: Phân tích chi tiết về Code của Business Invariant Detector: lấy rule từ bảng nào trong Database và nhãn BUSINESS_RULE được thêm vào ở đâu, bởi ai?](#câu-hỏi-5)
6. [Câu hỏi 6: Phân tích tương tự với Volume Drift Detector: lấy dữ liệu từ bảng nào trong Database và trường row_count được thêm vào ở đâu, bởi ai?](#câu-hỏi-6)
7. [Câu hỏi 7: Rollout Policy là gì? Sự khác biệt giữa SHADOW, ADVISORY và CALIBRATED?](#câu-hỏi-7)

---

<a name="câu-hỏi-1"></a>
## Câu hỏi 1
> **User:** *Oke giờ tôi muốn chạy thử phần isolation forest đấy thì chạy graph 3 à hay sao?*

### Câu trả lời:
Đúng vậy, module **Isolation Forest** nằm trong node `anomaly_detector` của **Graph 3 (Anomaly Investigation Graph)**. Bạn có thể chạy thử nghiệm theo 3 cách tùy theo nhu cầu:

#### Cách 1: Chạy CLI trực tiếp Graph 3 (Nhanh nhất)
Lấy `execution_run_id` mới nhất trong CSDL (hoặc chỉ định) và chạy qua toàn bộ pipeline của Graph 3 (`anomaly_detector` ➔ `hypothesis_agent` ➔ `persist_analysis` ➔ `report_writer`):
```powershell
python -m src.agents.graph 3
```
*Hoặc chỉ định dataset:*
```powershell
python -m src.agents.graph 3 dataset-nyc-yellow-taxi-50k
```
*(Thêm flag `--legacy` nếu muốn chạy ở chế độ Single-Shot Prompt thay vì DeepAgent).*

#### Cách 2: Chạy toàn bộ luồng End-to-End (Run 1 ➔ Publish ➔ Run 2 ➔ Run 3)
```powershell
python -m src.agents.graph all
```

#### Cách 3: Chạy script kiểm thử trực tiếp hàm `detect_anomalies`
```powershell
python -c "
from src.services.rule_store import get_engine
from sqlalchemy.orm import Session
from src.services.anomaly_service import detect_anomalies
from src.models.database import DqRunModel

with Session(get_engine()) as db:
    latest_run = db.query(DqRunModel).order_by(DqRunModel.created_at.desc()).first()
    if latest_run:
        res = detect_anomalies(db, latest_run.id, detector_config_version='anomaly-v2-iforest')
        print('Decision:', res['decision'])
        print('Rollout Mode:', res['rollout_mode'])
        ml_signals = [s for s in res['signals'] if s['family'] == 'ML']
        print(f'Total ML Signals: {len(ml_signals)}')
        for sig in ml_signals:
            print(' - Rule:', sig['target_id'], '| Score:', sig['score'], '| Status:', sig['baseline'].get('status', 'OK'))
    else:
        print('Chưa có run nào trong DB, hãy chạy graph 1 và 2 trước!')
"
```

---

<a name="câu-hỏi-2"></a>
## Câu hỏi 2
> **User:** *Hiện tại trong isolation có traces gì không? Với cả bạn cập nhật cho tôi agent workflow bản mới v4 vào file architecture_diagram.md đi.*

### Câu trả lời:
Hiện tại, hệ thống Isolation Forest và Graph 3 được trang bị **4 tầng traces/telemetry** độc lập phục vụ việc kiểm toán, gỡ lỗi và giám sát:

1. **Trace JSON xuất ra file (Debug Trace Export):**
   - **Đường dẫn:** `output/anomaly_detector/debug_anomalies_{timestamp}_{execution_run_id}.json`.
   - **Nội dung:** `detector_config_version`, `rollout_mode`, `anomaly_decision`, danh sách `signals` (5 chiều vector đặc trưng, raw score, calibration threshold, training median/min/max, lịch sử sạch, tham số n_estimators/contamination), và `signal_errors`.
2. **Durable Telemetry trong CSDL:**
   - Bảng **`anomaly_runs`**: Lưu `detector_config_version`, `rollout_mode`, `dominant_family`, `score`, `confidence`, `severity`.
   - Bảng **`anomaly_signals`**: Lưu từng tín hiệu riêng biệt kèm trường `baseline_json` chứa đầy đủ metadata hiệu chuẩn của Isolation Forest.
3. **DeepAgent Investigation Trace:**
   - **Đường dẫn:** `output/anomaly_investigations/investigation_trace_{anomaly_run_id}.json`.
   - **Nội dung:** Ghi lại chuỗi suy luận (tool calls, truy vấn SQL, trích xuất RAG context) của Deep Agent khi tìm nguyên nhân gốc rễ.
4. **Observability & LangSmith Tracing:**
   - Theo dõi độ trễ (latency), token và state transitions giữa các node trong LangGraph StateGraph.

*(Agent Workflow v4 đã được cập nhật trực tiếp vào file `docs_kien-01033/architecture_diagram.md`).*

---

<a name="câu-hỏi-3"></a>
## Câu hỏi 3
> **User:** *Phần anomaly graph trong architecture_diagram.md nó vẫn khó hiểu quá, bạn giải thích rõ hơn được không. Ý là tôi không cần bạn vẽ lại graph mà là giải thích bằng lời để tôi nhìn đối chiếu với graph và hiểu.*

### Câu trả lời:

#### Mục tiêu tổng thể của Run 3 (Anomaly Graph)
Sau khi Run 2 chạy xong các bài kiểm thử chất lượng dữ liệu (dbt tests), Run 3 được kích hoạt để trả lời 3 câu hỏi lớn:
1. **Có điều gì bất thường trong lần chạy này không?** (Kết hợp cả Luật cứng, Thống kê Robust và Học máy Isolation Forest).
2. **Mức độ nghiêm trọng ra sao?** (Tổng hợp điểm và phân loại: `NORMAL`, `WATCH`, `ANOMALY`, hay `CRITICAL`).
3. **Tại sao lại bị lỗi và xử lý thế nào?** (AI Agent suy luận tìm nguyên nhân gốc rễ và viết báo cáo cho Data Steward).

#### Chi tiết từng bước bên trong Anomaly Graph (đối chiếu theo sơ đồ):

* **Bước 3.1: Động cơ phát hiện bất thường đa tầng (`Canonical Anomaly Service`):**
  - Chạy ensemble 6 bộ detector chuyên biệt: (1) `BUSINESS_INVARIANT_DETECTOR`, (2) `ROBUST_MAD_DETECTOR / COLD_START`, (3) `VOLUME_DRIFT_DETECTOR`, (4) `FRESHNESS_DETECTOR`, (5) `EXECUTION_HEALTH_DETECTOR`, (6) `Multivariate Isolation Forest Engine`.
  - Isolation Forest đánh giá trên 5 chiều đặc trưng: Tỷ lệ vi phạm hiện tại, Delta so với lần trước, Log violation count, Log total rows, Log duration ms.
  - Có các chốt chặn: Causal boundary (`created_at < current_run.created_at`), History check ($\ge 30$ mẫu sạch), Degenerate history check (lịch sử phẳng/không có biến thiên).

* **Bước 3.2: Tổng hợp điểm số bảo thủ (`Deterministic Aggregator`):**
  - Lấy điểm cơ sở (Base Score) là điểm cao nhất từ các nhóm phi-ML.
  - Áp dụng Rollout Policy: `SHADOW` (chạy ngầm, không tác động điểm cuối) hoặc `ADVISORY` (tăng trưởng đơn điệu có chặn).
  - Corroboration Guardrail: ML đứng một mình chỉ được phép tạo mức `WATCH`, không thể tự nâng lên `ANOMALY` hay `CRITICAL`.

* **Bước 3.3: Điều tra tìm nguyên nhân gốc rễ (`Investigation Mode Choice`):**
  - Nhánh 3.3a (`Legacy Insights Node`): Dùng single-shot prompt ngắn gọn.
  - Nhánh 3.3b (`DeepAgent Investigation Node`): Agent tự động gọi Tool tra cứu CSDL, truy vấn vector store ChromaDB (RAG), kiểm tra phân phối dữ liệu để tạo các giả thuyết kèm bằng chứng.

* **Bước 3.4 & 3.5: Lưu trữ và Viết báo cáo (`Persist & Report Writer`):**
  - `persist_analysis_node`: Lưu quyết định, tín hiệu, giả thuyết vào DB (`anomaly_runs`, `anomaly_signals`).
  - `report_writer_node`: Tổng hợp thành báo cáo Markdown gửi tới Data Steward.

---

<a name="câu-hỏi-4"></a>
## Câu hỏi 4
> **User:** *Mục 1, 3, 4, 5 trong bước 3.1, hãy giải thích rõ hơn.*

### Câu trả lời:

### 1. `BUSINESS_INVARIANT_DETECTOR` (Bộ phát hiện vi phạm luật nghiệp vụ cốt lõi)
* **Bài toán:** Các quy tắc logic sống còn không bao giờ được phép sai (ví dụ: `fare_amount >= 0`, `passenger_count > 0`).
* **Cách thức:** Quét các rule có nhãn `BUSINESS_RULE` hoặc tiêu đề chứa `BUSINESS_` / `invariant`. Nếu vi phạm (`status == FAIL`), lập tức gán $\text{Score} = 1.0, \text{Reliability} = 1.0$.
* **Đặc quyền:** Kích hoạt cơ chế **Critical Override**, đẩy toàn bộ quyết định lên mức **`CRITICAL`** để báo động khẩn cấp.

### 3. `VOLUME_DRIFT_DETECTOR` (Bộ phát hiện đột biến dung lượng / số lượng dòng)
* **Bài toán:** Bảng dữ liệu có thể đúng logic từng cột nhưng tổng số dòng hôm nay bị rơi rụng nặng (Source Data Drop) hoặc bị nhân bản bất thường (Duplicate ETL).
* **Cách thức:** Lấy `row_count` hiện tại từ Profile và so sánh với lịch sử 20 lần chạy trước đó bằng **Robust Z-Score (Median/MAD)**. Nếu $|\text{Robust Z}| \ge 3.0 \rightarrow$ đánh dấu đột biến nghiêm trọng với $\text{Score} \ge 0.80$.

### 4. `FRESHNESS_DETECTOR` (Bộ phát hiện độ trễ / dữ liệu bị cũ)
* **Bài toán:** Dữ liệu không bị lỗi giá trị nhưng là dữ liệu cũ chưa được cập nhật mới (pipeline ETL bị kẹt, vi phạm SLA).
* **Cách thức:** Quét các rule kiểm tra độ trễ (hậu tố `.FRESHNESS`), phân loại vào nhóm `family = "FRESHNESS"` với trọng số ưu tiên cao ($0.8$), cảnh báo khi dữ liệu vượt ngưỡng SLA cho phép.

### 5. `EXECUTION_HEALTH_DETECTOR` (Bộ phát hiện lỗi kỹ thuật / sức khỏe thực thi pipeline)
* **Bài toán:** Phân biệt rõ giữa "Dữ liệu sai giá trị" và "Hạ tầng kỹ thuật bị sập" (đứt kết nối DB, crash SQL, timeout).
* **Cách thức:** Quét các bài test có trạng thái `status == "ERROR"` hoặc kiểm tra trường `error_message` của run. Nếu phát hiện crash, gán $\text{Score} = 1.0, \text{Family} = \text{"EXECUTION"}$ và kích hoạt **`CRITICAL` override**.

---

<a name="câu-hỏi-5"></a>
## Câu hỏi 5
> **User:** *Mục 1 hãy giải thích về code và nó lấy những rule ở trong bảng nào trong database và nhãn BUSINESS_RULE được thêm vào ở đâu, bởi ai?*

### Câu trả lời:

### 1. Giải thích về Code phát hiện (`src/services/anomaly_service.py`)
```python
rule_title = str(getattr(res, "rule_title", ""))
is_business_rule = (
    rule_title.startswith("BUSINESS_")
    or "invariant" in rule_title.lower()
    or res.rule_id.endswith(".BUSINESS_RULE")
)

if is_business_rule and res.status in ("FAIL", "FAILED"):
    score = 1.0
    detector_name = "BUSINESS_INVARIANT_DETECTOR"
    explanation_code = f"Vi phạm nghiêm trọng luật nghiệp vụ (Business Invariant): {rule_title}."

signals.append({
    "signal_id": f"sig-{uuid.uuid4().hex[:12]}",
    "family": "BUSINESS_RULE" if is_business_rule else "STATISTICAL",
    "target_type": "RULE",
    "target_id": rule_id,
    "score": round(score, 4),
    "reliability": round(reliability, 4),
    "detector_name": detector_name,
    "explanation_code": explanation_code,
    ...
})
```

### 2. Lấy rule ở bảng nào trong Database?
Hàm `detect_anomalies` nạp từ 2 bảng kết quả kiểm thử:
- **Bảng `test_results` (`TestResultModel` - Graph 2 mới):** Query theo `test_run_id`.
- **Bảng `dq_results` (`DqResultModel` - Graph cũ):** Query theo `run_id`.

### 3. Nhãn `BUSINESS_RULE` được thêm vào ở đâu, bởi ai?
- **Giai đoạn 1: Do LLM (Rule Proposer) sinh ra trong Run 1:** Node `rule_proposer_node` phân tích ngữ nghĩa bảng dữ liệu và tự động gán category là `BUSINESS_RULE` (hoặc rule_title có tiền tố `BUSINESS_INVARIANT:`), lưu tạm vào bảng `proposed_rules`.
- **Giai đoạn 2: Do Data Steward (Con người - HITL):** Trên Giao diện Web, Steward duyệt (Approve) hoặc tự tạo rule thủ công với nhãn `BUSINESS_RULE`. Khi bấm **Publish**, rule được lưu vào bảng **`active_rules`** (hoặc `ruleset_versions`) để Run 2 thực thi.

---

<a name="câu-hỏi-6"></a>
## Câu hỏi 6
> **User:** *Hãy làm tương tự với mục 3 đi.*

### Câu trả lời:

### 1. Giải thích về Code phát hiện (`src/services/anomaly_service.py`)
```python
profile = (
    db.query(ProfileModel)
    .filter(ProfileModel.dataset_id == current_run.dataset_id)
    .order_by(ProfileModel.generated_at.desc())
    .first()
)

if profile:
    current_rows = profile.row_count
    hist_profiles = (
        db.query(ProfileModel)
        .filter(
            ProfileModel.dataset_id == current_run.dataset_id,
            ProfileModel.generated_at < profile.generated_at,
        )
        .order_by(ProfileModel.generated_at.desc())
        .limit(_VOLUME_HISTORY_WINDOW)
        .all()
    )
    hist_rows = [p.row_count for p in hist_profiles]
    sufficient_vol_history = len(hist_rows) >= 5

    if sufficient_vol_history:
        vol_z, vol_median, vol_mad = calculate_robust_zscore(
            float(current_rows), [float(x) for x in hist_rows]
        )
        if abs(vol_z) >= 3.0:
            vol_score = min(1.0, 0.8 + (abs(vol_z) - 3.0) * 0.05)
            vol_explanation = f"Số lượng dòng ({current_rows}) đột biến so với baseline lịch sử với Robust Z = {vol_z:.2f}."
        else:
            vol_score = 0.0
            vol_explanation = "Số lượng dòng bình thường."

    signals.append({
        "signal_id": f"sig-{uuid.uuid4().hex[:12]}",
        "family": "VOLUME",
        "target_type": "DATASET",
        "target_id": current_run.dataset_id,
        "score": round(vol_score, 4),
        "reliability": round(vol_reliability, 4),
        "observed_value": str(current_rows),
        "baseline": vol_baseline,
        "sufficient_history": sufficient_vol_history,
        "detector_name": "VOLUME_DRIFT_DETECTOR",
        "explanation_code": vol_explanation,
        ...
    })
```

### 2. Lấy dữ liệu ở bảng nào trong Database?
Lấy từ bảng **`profiles`** (`ProfileModel` trong `src/models/database.py`), bao gồm: `dataset_id`, `row_count`, `generated_at`.

### 3. Dữ liệu bảng `profiles` và trường `row_count` được thêm vào ở đâu, bởi ai?
- **Ai tạo?** **Profiler Sub-Agent / Worker** tự động chạy câu lệnh `SELECT COUNT(*)` trên bảng nguồn.
- **Ở đâu?** Trong Run 1 (`raw_profiler_node` / `dataset_profiler.py`) hoặc trong luồng nạp dữ liệu định kỳ (Ingestion Pipeline). Mỗi lần chạy khảo sát, Profiler ghi một bản ghi mới vào bảng `profiles`.

---

<a name="câu-hỏi-7"></a>
## Câu hỏi 7
> **User:** *Đoạn rollout policy là gì, advisory/calibrated và shadow khác gì nhau ?*

### Câu trả lời:

### 1. Rollout Policy là gì?
Là cơ chế **kiểm soát mức độ ảnh hưởng của mô hình Machine Learning (Isolation Forest) theo từng giai đoạn an toàn**, tránh trường hợp AI bị dương tính giả (false positive) gây hoang mang và gửi cảnh báo sai cho người dùng.

### 2. So sánh 4 cấp độ Rollout Policy

```text
[DISABLED] ────────► [SHADOW] ────────► [ADVISORY] ────────► [CALIBRATED]
(Tắt hoàn toàn)     (Chạy ngầm ẩn)     (Tư vấn có chặn)     (Đã chuẩn hóa)
```

| Tiêu chí | `DISABLED` | `SHADOW` (Mặc định) | `ADVISORY` | `CALIBRATED` |
|---|---|---|---|---|
| **Mô hình có chạy không?** | ❌ Không chạy | ✅ Có chạy ngầm | ✅ Có chạy ngầm | ✅ Có chạy ngầm |
| **Có lưu DB & Trace JSON?** | ❌ Không | ✅ Có lưu đầy đủ | ✅ Có lưu đầy đủ | ✅ Có lưu đầy đủ |
| **Có ảnh hưởng điểm cuối (`final_score`)?** | ❌ Không | ❌ **Tuyệt đối không** | ✅ **Có (cộng thêm điểm tăng trưởng có chặn)** | ✅ **Có (theo chính sách chuẩn hóa)** |
| **Có hiển thị trên Dashboard?** | ❌ Không | ❌ **Ẩn hoàn toàn** | ✅ Hiển thị `ISOLATION_FOREST_OUTLIER` | ✅ Hiển thị `ISOLATION_FOREST_OUTLIER` |
| **Mức quyết định tối đa ML có thể tạo?** | Không có | Không tham gia | ⚠️ Tối đa chỉ là **`WATCH`** (nếu đứng một mình) | ⚠️ Tối đa chỉ là **`WATCH`** (nếu đứng một mình) |

### 3. Chi tiết từng chế độ:
- **`SHADOW` (Bóng ma):** Chạy ngầm, tính điểm, ghi log file và lưu DB để kỹ sư kiểm toán, nhưng **không làm thay đổi điểm số và không hiển thị cho người dùng**.
- **`ADVISORY` (Tư vấn có kiểm soát):** Được phép cộng điểm tăng trưởng có chặn:
  $$\text{Uplift} = \text{Trọng số ML (0.15)} \times \text{Độ tin cậy} \times \max(0, \text{Điểm ML} - \text{Điểm Base})$$
  $$\text{Final Score} = \min(1.0, \text{Điểm Base} + \text{Uplift})$$
  *Chốt chặn:* ML đứng một mình không thể tự tạo `ANOMALY` hay `CRITICAL`, chỉ tối đa đạt mức `WATCH`.
- **`CALIBRATED` (Chuẩn hóa):** Giai đoạn mô hình đã qua tinh chỉnh tối ưu và thu thập đủ phản hồi của Data Steward (`TRUE_ANOMALY` vs `FALSE_POSITIVE`).
