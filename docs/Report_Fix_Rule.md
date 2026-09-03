# BÁO CÁO PHÂN TÍCH VÀ PHƯƠNG ÁN KHẮC PHỤC: LỖI BẰNG CHỨNG (EVIDENCE) & TRÍCH XUẤT TÊN RULE TRÊN WEB UI (MOCK & GRAPH 1B)

> **Mã tài liệu:** `DOCS-REPORT-FIX-RULE-01`  
> **Phiên bản:** `2.0` (Cập nhật đối soát chuyên sâu cả 2 chế độ: **Mock Offline** và **Graph 1B LangGraph**)  
> **Ngày cập nhật:** 02/09/2026  
> **Dự án:** RidePulse DQ (AI-Powered Data Quality & HITL Governance Platform)  
> **Phạm vi phân tích:** 
> - Backend: `src/services/dashboard_agent_workflow.py`, `src/services/rule_proposer_workflow.py`, `src/agents/nodes/rule_proposer_node.py`, `src/agents/nodes/templates.py`.
> - Frontend: `frontend/src/App.tsx`, `frontend/src/types.ts`, `frontend/src/styles.css`.  
> **Trạng thái đối soát:** Đã xác thực 100% dựa trên 2 ảnh chụp giao diện người dùng thực tế (`agent-mock-v1` và `langgraph-openai`).

---

## 1. TỔNG QUAN VẤN ĐỀ VÀ ĐỐI SOÁT HAI ẢNH THỰC TẾ

### 1.1. Trường hợp 1 (Ảnh 1 - Chế độ Mock Fallback: `agent-mock-v1`)
- **Tập dữ liệu:** Dataset phim ảnh (8.807 dòng, cột `director` có 2.634 dòng null ~ 29.8%).
- **Thẻ hiển thị:**
  - Tiêu đề: `director must not be null`
  - Mô tả: `Ensure every row contains a valid director.`
  - Độ tin cậy: `90%`
  - Bằng chứng: `Aggregate persisted profile evidence: profile.column.director.null_rate (rows: 8807). profile.column.director.null_rate`
  - Mở rộng: `agent-mock-v1` ("Deterministic policy candidate fallback").
- **Vấn đề cốt lõi:** Sinh luật `NOT NULL` mù quáng cho cột tùy chọn rỗng tới ~30% dữ liệu, độ tin cậy 90% vô căn cứ; phần bằng chứng rỗng số liệu và lặp key kỹ thuật.

### 1.2. Trường hợp 2 (Ảnh 2 - Chế độ Graph 1B Thật: `langgraph-openai`)
- **Tập dữ liệu:** NYC Taxi Trip Records (10.000 dòng, cột `pickup_at` có tỷ lệ null = 0.0%).
- **Thẻ hiển thị:**
  - Tiêu đề: `pickup_at must not be null` *(Tiếng Anh máy móc)*
  - Mô tả: `Ensure every row contains a valid pickup_at.` *(Tiếng Anh máy móc)*
  - Độ tin cậy tổng thể: `90%`
  - Dòng Bằng chứng:  
    `BẰNG CHỨNG Aggregate persisted profile evidence: profile.column.pickup_at.null_rate (rows: 10000). Selection basis: Column pickup_at observed null rate is 0.0%. profile.column.pickup_at.null_rate`
  - Mở rộng "Vì sao có luật này": Tag `langgraph-openai`
    - Lý do nghiệp vụ: *"Thời điểm đón là thông tin nền tảng để xác định thời gian hoạt động, tính thời lượng chuyến và phân tích vận hành."* *(Tiếng Việt rất tự nhiên)*
    - Độ tin cậy chi tiết:
      - Sức mạnh bằng chứng: **99%**
      - Ủng hộ từ nghiệp vụ: **97%**
      - Tính đại diện của mẫu: **95%**
    - Chú thích: *"Tỷ lệ thiếu quan sát được là 0,0% trên toàn bộ 10.000 dòng; lịch sử phê duyệt cũng có rule NOT_NULL cho pickup_at với mức độ HIGH."* *(Tiếng Việt)*
- **Vấn đề cốt lõi:** Mặc dù logic nghiệp vụ của Graph 1B đã đúng và sinh ra văn bản Tiếng Việt chuẩn, **toàn bộ phần hiển thị bên ngoài của thẻ Card trên Frontend vẫn bị vỡ giao diện, mâu thuẫn ngôn ngữ và mâu thuẫn toán học**.

---

## 2. BẢNG PHÂN TÍCH TOÀN BỘ 5 KHIẾM KHUYẾT TRÊN FRONTEND (GRAPH 1B)

| STT | Thành phần giao diện | Hiện trạng hiển thị thực tế trên ảnh 2 | Bản chất nguyên nhân trong mã nguồn | Mức độ nghiêm trọng |
| :---: | :--- | :--- | :--- | :---: |
| **1** | **Tiêu đề & Mô tả** (`<h3>`, `<p>`) | Tiêu đề: `pickup_at must not be null`<br>Mô tả: `Ensure every row contains a valid pickup_at.` *(Tiếng Anh thô)* | - Backend `_normalise_graph_rule` vứt bỏ `rule_name` tiếng Việt của LLM và gán đè `title = candidate.title`.<br>- Frontend `App.tsx` (dòng 4285) chỉ đọc `title`, **hoàn toàn không dùng `rule_name`**. | 🔴 **CAO** (Trải nghiệm nửa nạc nửa mỡ) |
| **2** | **Dòng BẰNG CHỨNG** (`.evidence-row`) | `Aggregate persisted profile evidence: ... (rows: 10000). Selection basis: Column pickup_at observed null rate is 0.0%. profile.column.pickup_at.null_rate` | - Nối chuỗi cứng tiếng Anh ở backend.<br>- Frontend vừa in `evidenceSummary` vừa map in tiếp thẻ `<code>`, khiến key `profile.column.pickup_at.null_rate` bị **lặp lại 2 lần liên tiếp**. | 🔴 **CAO** (Lỗi UI rõ ràng) |
| **3** | **Độ tin cậy tổng thể vs. Chi tiết** | Điểm tổng: **90%**<br>Nhưng 3 thanh con bên dưới: **99% - 97% - 95%** | Backend áp trần cứng `confidence_ceiling = 0.9` vào điểm tổng thể, nhưng giữ nguyên điểm con từ LLM. Steward nhìn vào thấy **toán học bất nhất và vô lý**. | 🟡 **TRUNG BÌNH** (Gây nghi ngờ độ chính xác của AI) |
| **4** | **Nguồn gốc tham số & Giả định** | Không hiển thị bảng `Parameter provenance` và `Assumptions` trong accordion | Trong `rule_proposer_workflow.py` (dòng 784-785), backend hardcode lưu vào DB là `parameter_provenance="[]"`, `assumptions="[]"`. | 🟡 **TRUNG BÌNH** (Thiếu tính giải trình HITL) |
| **5** | **Loại luật** | `LOẠI LUẬT NOT NULL · pickup_at` | Hàm `formatRule(rule)` trong `App.tsx` hardcode các từ khóa thô tiếng Anh (`NOT NULL`, `RANGE`, `VALUES`). | 🟢 **THẤP** (Cần Việt hóa đồng bộ) |

---

## 3. PHÂN TÍCH KỸ THUẬT CHI TIẾT TỪNG LỖI

### 3.1. Lỗi 1: Xung đột ngôn ngữ giữa Tiêu đề/Mô tả và Lý do nghiệp vụ

#### Hiện tượng:
- Lý do nghiệp vụ (do LLM sinh ra): *"Thời điểm đón là thông tin nền tảng để xác định thời gian hoạt động, tính thời lượng chuyến và phân tích vận hành."* -> **Tiếng Việt 100%**.
- Ghi chú độ tin cậy (do LLM sinh ra): *"Tỷ lệ thiếu quan sát được là 0,0% trên toàn bộ 10.000 dòng; lịch sử phê duyệt cũng có rule NOT_NULL cho pickup_at với mức độ HIGH."* -> **Tiếng Việt 100%**.
- Nhưng Tiêu đề lại là: `pickup_at must not be null` -> **Tiếng Anh thô**.
- Và Mô tả lại là: `Ensure every row contains a valid pickup_at.` -> **Tiếng Anh thô**.

#### Mã nguồn gây lỗi:
1. **Ở Backend:** [`src/services/dashboard_agent_workflow.py` (dòng 932 - 944)](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/src/services/dashboard_agent_workflow.py#L932-L944)
   ```python
   def _normalise_graph_rule(
       raw: dict[str, Any], evidence: ProposalEvidence, candidate: DashboardRuleCandidate
   ) -> DashboardProposal | None:
       ...
       return DashboardProposal(
           id=f"proposal-{uuid.uuid4().hex}",
           title=candidate.title,              # <-- GHI ĐÈ CỨNG: "pickup_at must not be null"
           description=candidate.description,  # <-- GHI ĐÈ CỨNG: "Ensure every row contains a valid pickup_at."
           severity=candidate.severity,
           rule_type=candidate.dashboard_rule_type,
           rule_spec=candidate.rule_spec,
           evidence_refs=candidate.evidence_refs,
           ...
           rule_name=str(raw.get("rule_name") or candidate.title), # LLM trả về tiếng Việt ở đây nhưng bị bỏ xó!
           business_rationale=str(raw.get("business_rationale") or candidate.description),
       )
   ```
2. **Ở Frontend:** [`frontend/src/App.tsx` (dòng 4285, 4325)](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/frontend/src/App.tsx#L4285)
   ```tsx
   const title = isVi ? (proposal.title_vi || proposal.title) : proposal.title;
   const description = isVi ? (proposal.description_vi || proposal.description) : proposal.description;
   ...
   <h3>{title}</h3>
   <p>{description}</p>
   ```
   - Frontend tìm kiếm `proposal.title_vi` và `proposal.description_vi`. Hai trường này **không hề tồn tại trong API response của Backend**.
   - Trường `proposal.rule_name` (chứa tên tiếng Việt nghiệp vụ chuẩn) **hoàn toàn không được tham chiếu hay hiển thị ở bất kỳ đâu trên thẻ Card**!

---

### 3.2. Lỗi 2: Dòng Bằng chứng bị lặp key và nối chuỗi tiếng Anh thô

#### Hiện tượng:
```text
BẰNG CHỨNG Aggregate persisted profile evidence: profile.column.pickup_at.null_rate (rows: 10000). Selection basis: Column pickup_at observed null rate is 0.0%. profile.column.pickup_at.null_rate
```
- Chuỗi `profile.column.pickup_at.null_rate` xuất hiện **2 lần**.
- Đoạn `Selection basis: Column pickup_at observed null rate is 0.0%.` là tiếng Anh máy móc chắp vá.

#### Mã nguồn gây lỗi:
1. **Ở Backend:** [`dashboard_agent_workflow.py` (dòng 938 - 940)](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/src/services/dashboard_agent_workflow.py#L938-L940)
   ```python
   evidence_summary=(
       f"{_safe_evidence_summary(evidence, candidate.evidence_refs)} Selection basis: {candidate.selection_reason}"
   )
   ```
   - `_safe_evidence_summary` trả về: `Aggregate persisted profile evidence: profile.column.pickup_at.null_rate (rows: 10000).`
   - `candidate.selection_reason` nối tiếp: `Selection basis: Column pickup_at observed null rate is 0.0%.`
2. **Ở Frontend:** [`frontend/src/App.tsx` (dòng 4340 - 4346)](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/frontend/src/App.tsx#L4340-L4346)
   ```tsx
   <div className="evidence-row">
     <span className="evidence-label">{isVi ? "BẰNG CHỨNG" : "EVIDENCE"}</span>
     <span>{evidenceSummary}</span>
     {proposal.evidence_refs.map((ref) => (
       <code key={ref}>{ref}</code>  {/* <-- LẶP LẠI LẦN THỨ 2! */}
     ))}
   </div>
   ```

---

### 3.3. Lỗi 3: Xung đột toán học giữa Độ tin cậy tổng thể (90%) và Độ tin cậy thành phần (99%, 97%, 95%)

#### Hiện tượng:
- Điểm tổng thể: **90%** (thanh tiến trình ở góc trên bên phải).
- 3 thanh tiến trình thành phần bên dưới:
  - Sức mạnh bằng chứng (Evidence strength): **99%**
  - Ủng hộ từ nghiệp vụ (Business support): **97%**
  - Tính đại diện của mẫu (Sample representativeness): **95%**
- Một người dùng bất kỳ nhìn vào sẽ thắc mắc: *"Tại sao cả 3 chỉ số đều trên 95% mà điểm tổng thể lại tụt xuống 90%?"*

#### Mã nguồn gây lỗi:
Tại [`dashboard_agent_workflow.py` (dòng 918, 929)](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/src/services/dashboard_agent_workflow.py#L918):
```python
capped_confidence = min(confidence, candidate.confidence_ceiling)
...
normalized_breakdown["overall"] = capped_confidence
```
Trong hàm sinh candidate (dòng 1141):
```python
confidence_ceiling = 0.9  # Gán cứng mức trần 90% cho mọi rule NOT_NULL!
```
- Khi LLM của Graph 1B phân tích rằng `pickup_at` là cột cốt lõi, tỷ lệ null là `0.0%` hoàn hảo, LLM chấm điểm tin cậy tổng thể là `98%` (với các tiêu chí 99%, 97%, 95%).
- Nhưng hàm `_normalise_graph_rule` đã **chặt cụt (cap) điểm tổng xuống 0.9 (90%)**, trong khi **giữ nguyên các điểm thành phần 99%, 97%, 95%**.
- Khi Frontend hiển thị, nó lấy `proposal.confidence = 0.9` và các thanh thành phần từ `breakdown`, tạo nên sự bất hợp lý toán học trên giao diện.

---

### 3.4. Lỗi 4: Không hiển thị Nguồn gốc tham số (Provenance) và Giả định (Assumptions)

#### Hiện tượng:
Trong phần mở rộng "Vì sao có luật này", sau phần "Độ tin cậy đến từ đâu", giao diện bị cụt, không hề có bảng "Tham số lấy từ đâu" (`Parameter provenance`) và danh sách "Giả định" (`Assumptions`).

#### Mã nguồn gây lỗi:
Tại [`src/services/rule_proposer_workflow.py` (dòng 784 - 785)](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/src/services/rule_proposer_workflow.py#L784-L785):
```python
db.add(
    RuleProposalModel(
        ...
        parameter_provenance="[]",  # <-- BỊ GÁN CỨNG MẢNG RỖNG!
        assumptions="[]",           # <-- BỊ GÁN CỨNG MẢNG RỖNG!
        ...
    )
)
```
Dù LLM trong Graph 1B ([`CandidateProposedRuleDraft`](file:///c:/Users/PC%20ACER/OneDrive/Desktop/AI%20ML%20Engineer/P-028/src/agents/nodes/rule_proposer_node.py#L93-L94)) có trả về `parameter_provenance` và `assumptions`, luồng workflow bước 3 lại bỏ qua và lưu mảng rỗng `[]` vào cơ sở dữ liệu. Frontend thấy mảng rỗng nên không render.

---

## 4. PHƯƠNG ÁN KHẮC PHỤC TRIỆT ĐỂ (END-TO-END FIX)

### 4.1. Sửa Backend (`src/services/dashboard_agent_workflow.py` & `src/services/rule_proposer_workflow.py`)

#### Bước 1: Ưu tiên `rule_name` và `rule_description` từ LLM của Graph 1B
Trong `_normalise_graph_rule`:
```python
# Giữ lại tên và mô tả nghiệp vụ của LLM nếu có
rule_name_val = str(raw.get("rule_name") or candidate.title).strip()
rule_desc_val = str(raw.get("rule_description") or raw.get("business_rationale") or candidate.description).strip()

return DashboardProposal(
    id=f"proposal-{uuid.uuid4().hex}",
    title=rule_name_val,                # ƯU TIÊN RULE_NAME CỦA LLM LÀM TIÊU ĐỀ
    description=rule_desc_val,          # ƯU TIÊN MÔ TẢ NGHIỆP VỤ CỦA LLM
    severity=candidate.severity,
    rule_type=candidate.dashboard_rule_type,
    rule_spec=candidate.rule_spec,
    evidence_refs=candidate.evidence_refs,
    evidence_summary=_vietnamese_evidence_summary(evidence, candidate),
    confidence=capped_confidence,
    model_name=model_name,
    rule_name=rule_name_val,
    business_rationale=str(raw.get("business_rationale") or candidate.description),
    proposal_basis=str(raw.get("proposal_basis") or "MIXED"),
    evidence=_build_rich_evidence_payload(candidate, evidence),
    confidence_breakdown=normalized_breakdown,
)
```

#### Bước 2: Chuẩn hóa hàm tóm tắt bằng chứng tiếng Việt có số liệu (`_vietnamese_evidence_summary`)
```python
def _vietnamese_evidence_summary(evidence: ProposalEvidence, candidate: DashboardRuleCandidate) -> str:
    col_map = {c.name: c for c in evidence.columns}
    col = col_map.get(candidate.column)
    if col and candidate.dashboard_rule_type == "not_null":
        null_cnt = int(round(col.null_rate * evidence.row_count))
        pct = col.null_rate * 100
        return f"Hồ sơ dữ liệu ({evidence.row_count:,} dòng): Cột '{col.name}' ghi nhận tỷ lệ khuyết thiếu {pct:.1f}% ({null_cnt:,} dòng rỗng)."
    elif col and candidate.dashboard_rule_type == "numeric_range":
        return f"Hồ sơ dữ liệu ({evidence.row_count:,} dòng): Cột '{col.name}' có giá trị dao động từ {col.min_value} đến {col.max_value}."
    return f"Bằng chứng tổng hợp từ hồ sơ {evidence.row_count:,} dòng."
```

#### Bước 3: Không gán cứng `confidence_ceiling = 0.9` khi bằng chứng hoàn hảo
Với các cột có `null_rate == 0.0` và có lịch sử phê duyệt cao, nâng trần `confidence_ceiling` lên `0.98` hoặc tính trung bình có trọng số từ `breakdown` để tránh mâu thuẫn giữa điểm tổng và các thanh con.

#### Bước 4: Lưu đầy đủ `parameter_provenance` và `assumptions` vào DB
Trong `rule_proposer_workflow.py` dòng 784-785:
```python
parameter_provenance=json.dumps(getattr(proposal, "parameter_provenance", []) or []),
assumptions=json.dumps(getattr(proposal, "assumptions", []) or []),
```

---

### 4.2. Sửa Frontend (`frontend/src/App.tsx`)

#### Bước 1: Ưu tiên hiển thị `rule_name` làm tiêu đề thẻ
Tại dòng 4285:
```tsx
// Ưu tiên: title_vi -> rule_name (tên tiếng Việt do LLM sinh) -> title
const displayTitle = isVi 
  ? (proposal.title_vi || proposal.rule_name || proposal.title)
  : (proposal.title || proposal.rule_name);

const displayDescription = isVi 
  ? (proposal.description_vi || proposal.description) 
  : proposal.description;
```
Và render:
```tsx
<h3>{displayTitle}</h3>
<p>{displayDescription}</p>
```

#### Bước 2: Sửa lỗi hiển thị lặp ở dòng BẰNG CHỨNG
Tại dòng 4340 - 4346:
```tsx
<div className="evidence-row">
  <span className="evidence-label">{isVi ? "BẰNG CHỨNG" : "EVIDENCE"}</span>
  <span className="evidence-text">{evidenceSummary}</span>
  {/* Không in lặp danh sách thô nếu evidenceSummary đã chứa ngữ nghĩa đầy đủ */}
</div>
```

#### Bước 3: Đồng bộ Việt hóa cho nhãn Loại luật (`formatRule`)
```tsx
function formatRule(rule: RuleSpec, isVi: boolean = false) {
  if (rule.type === "not_null") 
    return isVi ? `BẮT BUỘC CÓ GIÁ TRỊ · ${rule.column}` : `NOT NULL · ${rule.column}`;
  if (rule.type === "numeric_range")
    return isVi ? `KHOẢNG HỢP LỆ · ${rule.column} ≥ ${rule.min_value}` : `RANGE · ${rule.column} ≥ ${rule.min_value}`;
  if (rule.type === "accepted_values")
    return isVi ? `BỘ GIÁ TRỊ · ${rule.column} ∈ ${(rule.allowed_values ?? []).join(", ")}` : `VALUES · ${rule.column} ∈ ${(rule.allowed_values ?? []).join(", ")}`;
  if (rule.type === "cross_field_comparison")
    return isVi ? `SO SÁNH CỘT · ${(rule.columns ?? []).join(` ${rule.operator ?? "≤"} `)}` : `COMPARE · ${(rule.columns ?? []).join(` ${rule.operator ?? "≤"} `)}`;
  return isVi ? `TRÙNG LẶP · ${(rule.fingerprint_columns ?? []).join(" + ")}` : `DUPLICATE · ${(rule.fingerprint_columns ?? []).join(" + ")}`;
}
```

---

## 5. KẾT LUẬN VÀ BƯỚC TIẾP THEO

- **Ảnh 1 (Mock):** Lỗi xuất phát từ backend sinh candidate bừa bãi không check `null_rate`.
- **Ảnh 2 (Graph 1B):** Logic của Agent LangGraph đã chạy **hoàn toàn chính xác**, suy luận ra lý do nghiệp vụ và độ tin cậy tiếng Việt rất hay. Nhưng **tầng chuyển đổi Normalizer của Backend và tầng hiển thị của Frontend đã làm "hỏng" kết quả tốt này** khi:
  1. Ép ngược tiêu đề/mô tả về tiếng Anh kỹ thuật (`pickup_at must not be null`).
  2. Ghép chuỗi bằng chứng tiếng Anh thô và lặp key 2 lần.
  3. Cắt cụt độ tin cậy tổng thể xuống 90% gây bất nhất với các tiêu chí con 99%, 97%, 95%.
- Thực hiện áp dụng các bước sửa tại Mục 4 sẽ đảm bảo trải nghiệm thống nhất, chuyên nghiệp 100% bằng Tiếng Việt chuẩn mực trên giao diện Web UI.
