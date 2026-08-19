# RidePulse DQ — Frontend Implementation Plan

> **Status:** Planning only — chưa implement trong tài liệu này  
> **Branch mục tiêu:** `codex/phong-frontend`  
> **Base sau khi merge:** `main` tại commit `978cee7`  
> **Mục tiêu:** cập nhật frontend theo các thay đổi đã thống nhất trong [PROJECT_PLAN_AND_TASKS_REVISED.md](./PROJECT_PLAN_AND_TASKS_REVISED.md)

---

## 1. Kết quả cần đạt

Frontend phải trình diễn được một luồng đa dataset hoàn chỉnh:

```text
Chọn/Upload dataset
→ Xem schema và profile
→ Xem/chỉnh semantic contract
→ Trigger Rule Proposer
→ Review/approve/edit/reject rule
→ Chạy rule đã approve
→ Xem kết quả, violation và trend
```

Giao diện cần thể hiện rõ ba giá trị của sản phẩm:

1. Agent hiểu dataset trước khi đề xuất rule.
2. Rule có evidence và con người vẫn kiểm soát quyết định.
3. Kết quả DQ có thể theo dõi và giải thích được.

Không mở rộng frontend sang các tính năng future work như ChromaDB UI, multi-model comparison, Airflow monitoring hoặc Isolation Forest configuration trong critical path.

---

## 2. Audit frontend hiện tại sau khi merge

### 2.1 Những gì đã có

- React + TypeScript + Vite.
- `frontend/src/App.tsx` hiện chứa toàn bộ shell, page và phần lớn component, khoảng 2.175 dòng.
- Đã có các luồng đăng nhập/session, dataset, profile, rule proposals, HITL cơ bản, DQ run, anomaly, trends và admin.
- API client và mock API đã dùng chung `ApiClient` interface.
- CSS hiện tại có design tokens, dark/light guardrail và một số layout responsive.
- Backend contract hiện có các endpoint cho dataset list, profile, proposals, DQ run, anomalies, trends, rows và admin.

### 2.2 Khoảng trống cần xử lý

- App đang lấy dataset bằng `datasets[0]`, chưa có dataset picker/workspace state rõ ràng.
- `DatasetRow` và Data Explorer gắn với các field taxi như `trip_distance`, `fare_amount`, `payment_type`, `pickup_at`.
- Mock API chỉ có một dataset `dataset-nyc-yellow-taxi-50k`.
- TypeScript types chưa có dataset version, schema registry, semantic contract, context payload hoặc evidence object có cấu trúc.
- Chưa có UI upload CSV/Parquet và trạng thái upload.
- Chưa có màn hình semantic contract.
- Chưa có cách trình bày rõ Agent mode, prompt/model version và latency.
- Chưa có review board với evidence detail và bulk action đúng nghĩa.
- `package.json` hiện chưa dùng Ant Design, Recharts hoặc thư viện state/query nào; không được giả định các dependency này đã tồn tại.
- Logic UI, API loading và domain state đang trộn trong `App.tsx`, làm tăng rủi ro khi đổi API.

### 2.3 Nguyên tắc từ audit

- Giữ lại visual language và các luồng đang chạy; refactor từng phần.
- Ưu tiên API contract và dataset selection trước polish.
- Không thêm UI framework lớn chỉ để thay thế CSS đang có.
- Dùng native CSS/SVG cho chart đơn giản; chỉ thêm chart library nếu API và nhu cầu drill-down đã ổn định.
- Mọi page đều phải hoạt động với dataset không có field taxi.

---

## 3. Phạm vi frontend

### 3.1 P0 — Bắt buộc cho MVP public

- Dataset Catalog và dataset selection.
- Upload dataset CSV/Parquet hoặc upload state tương thích backend.
- Schema/Profile Explorer generic.
- Semantic Contract Viewer với edit/save.
- Rule Proposal Review Board.
- Execute approved rules và hiển thị progress/result.
- DQ Dashboard cơ bản theo dataset đang chọn.
- Mock API hỗ trợ cùng contract với real API.
- Loading/error/empty states.
- Responsive layout cho laptop và mobile width cơ bản.
- Hiển thị rõ mock/real mode và API error.

### 3.2 P1 — Nên hoàn thành sau core flow

- Evidence drawer và profile metric detail.
- Bulk approve/reject có confirmation.
- Semantic contract version/diff.
- Generic Data Explorer hiển thị dynamic columns.
- Trend filter theo date range và dimension.
- Export JSON/CSV cho results và contract.
- Job status polling có cancel/retry presentation.
- Accessibility pass cho keyboard, focus và contrast.

### 3.3 P2 — Future work

- WebSocket real-time nếu polling chưa đủ.
- ChromaDB/RAG history explorer.
- Multi-model evaluation dashboard.
- Dagster run monitor.
- Advanced anomaly configuration.
- User-defined dashboard builder.

---

## 4. Information architecture

Frontend dùng một `AppShell` với context dataset hiện tại và session hiện tại.

```text
AppShell
├── TopBar
│   ├── Brand
│   ├── DatasetSwitcher
│   ├── AgentModeBadge
│   └── UserMenu
├── PrimaryNav
│   ├── Overview
│   ├── Dataset
│   ├── Semantic Contract
│   ├── Rules
│   ├── Runs
│   ├── Data Explorer
│   └── Admin (role-gated)
└── PageContent
```

### 4.1 Overview

Mục đích: trả lời nhanh “dataset đang khỏe không và cần làm gì tiếp theo?”.

- Active dataset/version.
- Pipeline status stepper.
- DQ score và pass rate.
- Rules pending review.
- Latest run và latest anomalies.
- CTA tiếp theo duy nhất, ví dụ `Profile dataset`, `Review rules` hoặc `Run approved rules`.

### 4.2 Dataset Catalog

- Danh sách tất cả dataset.
- Dataset name, version, file type, row count, status, updated time.
- Upload action.
- Select active dataset.
- View schema/profile action.
- Error status nếu ingest/profile fail.

### 4.3 Dataset Workspace

Workspace giữ `datasetId` và `datasetVersionId` trong state, không đọc dataset đầu tiên từ array.

- Header hiển thị dataset/version.
- Tabs hoặc sub-navigation cho Profile, Contract, Rules, Runs và Data.
- Nếu chưa chọn dataset, hiển thị empty state có CTA tới catalog.

### 4.4 Semantic Contract

- Column table: name, physical type, semantic type, description, confidence, evidence.
- Contract summary và warnings.
- AI-generated badge và user-reviewed badge.
- Edit form có validation.
- Save draft và confirm contract actions.
- Diff/version view ở P1.

### 4.5 Rules

- Filter by status, dimension, severity, rule type.
- Table/card view cho proposal.
- Evidence drawer.
- Edit parameters theo rule type.
- Approve/reject/bulk review.
- Hiển thị `model_name`, prompt version, confidence và agent mode.

### 4.6 Runs và Dashboard

- Run status và progress.
- Summary cards.
- Result table theo rule.
- Failed row/sample count có giới hạn.
- Anomaly signal và trend.
- Link ngược tới rule và evidence.

### 4.7 Data Explorer

Data Explorer phải render từ `schema.columns` và row projection do API trả về.

- Dynamic column list.
- Column type-aware formatting.
- Sort/filter chỉ trên field API công bố.
- Không tự tính quality issue bằng danh sách taxi cố định.
- Nếu backend chưa có generic row endpoint, hiển thị profile/sample preview thay vì giả định field.

---

## 5. Component architecture

### 5.1 Cấu trúc thư mục đề xuất

```text
frontend/src/
├── App.tsx                         # wiring shell, route/view selection
├── main.tsx
├── types.ts                        # shared API/domain types
├── api/
│   ├── index.ts
│   ├── client.ts                   # real API adapter
│   ├── mockApi.ts                  # same contract, multi-dataset fixture
│   └── errors.ts
├── components/
│   ├── layout/
│   │   ├── AppShell.tsx
│   │   ├── TopBar.tsx
│   │   ├── PrimaryNav.tsx
│   │   └── DatasetSwitcher.tsx
│   ├── dataset/
│   │   ├── DatasetCatalog.tsx
│   │   ├── DatasetUploadDialog.tsx
│   │   ├── DatasetStatusBadge.tsx
│   │   ├── SchemaTable.tsx
│   │   └── ProfileSummary.tsx
│   ├── semantic/
│   │   ├── SemanticContractPage.tsx
│   │   ├── SemanticColumnTable.tsx
│   │   ├── SemanticColumnEditor.tsx
│   │   ├── EvidenceDrawer.tsx
│   │   └── ContractVersionBadge.tsx
│   ├── rules/
│   │   ├── RuleReviewPage.tsx
│   │   ├── RuleProposalTable.tsx
│   │   ├── RuleProposalCard.tsx
│   │   ├── RuleEditorDialog.tsx
│   │   ├── RuleEvidenceDrawer.tsx
│   │   ├── RuleStatusBadge.tsx
│   │   └── BulkReviewBar.tsx
│   ├── runs/
│   │   ├── RunStatusPanel.tsx
│   │   ├── RunResultTable.tsx
│   │   ├── RunSummaryCards.tsx
│   │   └── RunDetailDrawer.tsx
│   ├── dashboard/
│   │   ├── DqOverview.tsx
│   │   ├── QualityTrendChart.tsx
│   │   ├── FailureBreakdown.tsx
│   │   └── AnomalyPanel.tsx
│   ├── data/
│   │   └── GenericDataExplorer.tsx
│   └── common/
│       ├── AsyncState.tsx
│       ├── EmptyState.tsx
│       ├── ErrorState.tsx
│       ├── Modal.tsx
│       ├── StatusPill.tsx
│       └── Tooltip.tsx
├── hooks/
│   ├── useSession.ts
│   ├── useDatasetWorkspace.ts
│   ├── useDatasetCatalog.ts
│   ├── useSemanticContract.ts
│   ├── useRuleReview.ts
│   ├── useDqRun.ts
│   └── useDashboardData.ts
└── styles/
    ├── tokens.css
    ├── layout.css
    ├── components.css
    └── responsive.css
```

Không cần tạo toàn bộ file ngay từ đầu. Tách theo vertical slice để mỗi slice vẫn chạy được.

### 5.2 Phân ranh giới component

- Page component điều phối data và action.
- Presentational component chỉ nhận props, không gọi API.
- Hook chịu trách nhiệm fetch, loading, mutation và refresh.
- API adapter không chứa UI decision.
- Formatters nằm ngoài JSX.
- Domain types không được khai báo lại trong component.

---

## 6. Frontend data contract

### 6.1 Types cần bổ sung/cập nhật

```ts
export type DatasetFileType = "csv" | "parquet";
export type DatasetPipelineStatus =
  | "REGISTERED"
  | "UPLOADING"
  | "PROFILE_PENDING"
  | "PROFILE_READY"
  | "UNDERSTANDING_READY"
  | "FAILED";

export interface DatasetVersion {
  id: string;
  dataset_id: string;
  file_type: DatasetFileType;
  storage_path?: string;
  row_count?: number;
  schema_hash?: string;
  created_at: string;
}

export interface SchemaColumn {
  name: string;
  data_type: string;
  nullable?: boolean;
  semantic_type?: string;
  description?: string;
}

export interface DatasetSchema {
  dataset_id: string;
  dataset_version_id: string;
  columns: SchemaColumn[];
}

export interface SemanticColumn {
  column_name: string;
  business_name?: string;
  description?: string;
  semantic_type: string;
  confidence?: number;
  evidence_keys: string[];
  warnings?: string[];
}

export interface SemanticContract {
  id: string;
  dataset_id: string;
  dataset_version_id: string;
  status: "AI_GENERATED" | "DRAFT" | "CONFIRMED";
  columns: SemanticColumn[];
  dataset_summary?: string;
  domain?: string;
  warnings: string[];
  generated_at: string;
  updated_at: string;
}

export interface EvidenceItem {
  key: string;
  label: string;
  value: string | number | boolean | null;
  source: "PROFILE" | "SCHEMA" | "SEMANTIC_CONTRACT" | "HISTORY";
}
```

Existing `Dataset`, `DatasetProfile`, `RuleProposal` and `DqResult` types should be extended compatibly. Do not silently rename fields consumed by the current backend; introduce mapping functions at the API boundary if the new backend contract differs.

### 6.2 API client additions

Extend `ApiClient` with:

```ts
listDatasetVersions(datasetId: string): Promise<DatasetVersion[]>;
getSchema(datasetId: string, versionId?: string): Promise<DatasetSchema>;
uploadDataset(input: UploadDatasetInput): Promise<DatasetVersion>;
startUnderstanding(datasetId: string, versionId?: string): Promise<CreateJobResponse>;
getSemanticContract(datasetId: string, versionId?: string): Promise<SemanticContract | null>;
updateSemanticContract(id: string, input: SemanticContractUpdate): Promise<SemanticContract>;
listEvidence(datasetId: string, keys: string[]): Promise<EvidenceItem[]>;
```

API names must be aligned with actual backend routes before implementation. The frontend plan does not authorize inventing routes that backend does not expose.

### 6.3 Mock adapter requirements

`mockApi.ts` phải:

- Có ít nhất hai dataset khác schema.
- Hỗ trợ dataset switching.
- Có semantic contract cho mỗi dataset.
- Trả proposal khác nhau theo schema.
- Mô phỏng loading và failed job.
- Giữ cùng response shape với real API.
- Không dùng taxi columns trong generic screens.

Mock data là demo/test fixture, không phải logic production.

---

## 7. State và data loading

### 7.1 Workspace state

Tạo `useDatasetWorkspace` quản lý:

```text
selectedDatasetId
selectedVersionId
selectedView
profile
schema
semanticContract
proposals
latestRun
```

Khi dataset/version thay đổi:

1. Clear các data phụ thuộc dataset cũ.
2. Fetch schema/profile/contract/proposals theo key mới.
3. Không hiển thị dữ liệu cũ trong khi chờ response mới.
4. Hiển thị skeleton hoặc stale-data indicator phù hợp.

### 7.2 Job polling

Trước mắt dùng polling có backoff:

- 0–5 giây: 1 giây/lần.
- 5–30 giây: 2 giây/lần.
- Sau 30 giây: 5 giây/lần.
- Dừng ở `SUCCEEDED` hoặc `FAILED`.
- Có timeout và nút retry.

WebSocket chỉ bổ sung sau khi polling đã ổn định.

### 7.3 Error model

Chuẩn hóa lỗi thành:

```ts
type FrontendErrorKind =
  | "AUTH_REQUIRED"
  | "PERMISSION_DENIED"
  | "VALIDATION"
  | "NETWORK"
  | "JOB_FAILED"
  | "API_NOT_CONFIGURED"
  | "UNKNOWN";
```

Thông báo phải nói rõ:

- Hành động nào thất bại.
- Dataset nào bị ảnh hưởng.
- Có thể retry hay cần chỉnh input.
- Request/correlation id nếu backend trả về.

---

## 8. UX và visual direction

### 8.1 Hierarchy

- Mỗi page có một heading và một primary action.
- DQ score là summary, không lấn át evidence.
- Rule review ưu tiên status, target column, severity và action.
- Reasoning đặt cạnh evidence, không ẩn toàn bộ trong tooltip.
- Không dùng quá nhiều card lồng nhau.

### 8.2 Design tokens

Giữ token hiện có trong `styles.css`, sau đó chuẩn hóa thành:

- Canvas/surface/border.
- Text primary/secondary/muted.
- Success/warning/danger/info.
- Spacing scale.
- Radius scale.
- Focus ring.
- Typography scale.

Không thêm gradient, shadow hoặc màu accent mới nếu không cần cho trạng thái.

### 8.3 Interaction rules

- Primary action có một style thống nhất.
- Destructive action luôn có confirmation.
- Button trong trạng thái loading không được tạo duplicate request.
- Inline edit có cancel rõ ràng.
- Toast chỉ dùng cho kết quả ngắn; lỗi dài dùng inline error panel.
- `PENDING`, `RUNNING`, `WAITING_FOR_REVIEW`, `SUCCEEDED`, `FAILED` có màu và label nhất quán.

### 8.4 Responsive

- Desktop: workspace 2-column nơi phù hợp.
- Tablet: panel xếp dọc, table có horizontal scroll có kiểm soát.
- Mobile: table chuyển thành stacked row/card; CTA vẫn nhìn thấy.
- Không để chart hoặc code evidence làm tràn viewport.
- Keyboard focus phải nhìn thấy ở mọi interactive element.

### 8.5 Visualization

P0 chỉ cần:

- DQ score dial/progress.
- Pass/fail breakdown.
- Violation bar theo rule/dimension.
- Quality trend line bằng SVG/CSS hoặc thư viện nhỏ nếu cần.
- Anomaly panel có severity và reasoning.

Không xây dashboard nhiều biểu đồ trước khi API trả được time-series ổn định.

---

## 9. Các vertical slices cần implement

### Slice 1 — App shell và dataset context

**Mục tiêu:** mọi page dùng dataset đã chọn.

- Tách `AppShell`, `TopBar`, `PrimaryNav`, `DatasetSwitcher`.
- Thay `datasets[0]` bằng selected dataset state.
- Đưa view selection và refresh logic vào hook.
- Giữ admin/session behavior hiện có.

**Acceptance criteria:**

- Chuyển dataset không làm hiển thị dữ liệu dataset cũ.
- Refresh browser vẫn có fallback chọn dataset hợp lệ.
- Không có query API nào thiếu `datasetId` khi endpoint yêu cầu.

### Slice 2 — Dataset Catalog và upload

**Mục tiêu:** người dùng bắt đầu workflow từ dataset.

- `DatasetCatalog` table/list.
- `DatasetUploadDialog` với file validation.
- Upload progress/status.
- Version selector.
- Empty/error state.

**Acceptance criteria:**

- CSV/Parquet được hiển thị đúng file type.
- File sai extension hoặc quá lớn bị chặn trước request.
- Upload failure có retry.
- Dataset mới tự động được chọn hoặc có feedback rõ ràng.

### Slice 3 — Schema/Profile Explorer generic

**Mục tiêu:** chứng minh hệ thống không chỉ dành cho taxi dataset.

- `SchemaTable` dynamic columns.
- Profile metrics theo data type.
- Column detail drawer.
- Highlight null/uniqueness/range signals.

**Acceptance criteria:**

- Dataset có columns hoàn toàn khác taxi vẫn render được.
- Column không có sample value không làm crash UI.
- Numeric/date/string formatting có fallback.

### Slice 4 — Semantic Contract Viewer

**Mục tiêu:** người dùng nhìn thấy kết quả Dataset Understanding và có quyền sửa.

- Table semantic columns.
- Contract summary/warnings.
- Edit modal/drawer.
- Save draft/confirm.
- Evidence detail.

**Acceptance criteria:**

- UI phân biệt AI output với user-edited output.
- Save error không làm mất bản đang hiển thị.
- Rule propose CTA bị disable nếu contract/profile chưa sẵn sàng.

### Slice 5 — Rule Review Board

**Mục tiêu:** HITL là bước trung tâm của sản phẩm.

- Proposal table/card.
- Status/severity/dimension filters.
- Rule parameter editor theo rule type.
- Evidence drawer.
- Approve/reject/edit.
- Bulk review.

**Acceptance criteria:**

- Rule reject bắt buộc có review note.
- Edit hiển thị before/after.
- Rule chưa approve không có nút execute active.
- Duplicate submit bị ngăn ở UI và backend contract.

### Slice 6 — Runs và result detail

**Mục tiêu:** nối quyết định HITL với kết quả thực tế.

- Execute CTA từ approved rules.
- Job status panel và polling.
- Result table.
- Result detail drawer.
- Retry failed run.

**Acceptance criteria:**

- User biết đang ở profile/propose/review/execute phase.
- Kết quả được scope theo `runId` và dataset/version.
- Failed job không hiển thị như success.

### Slice 7 — Dashboard và trend

**Mục tiêu:** hiển thị value sau khi run.

- Summary cards.
- Quality trend.
- Failure breakdown.
- Anomaly panel.
- Filter by run/date/dimension nếu API hỗ trợ.

**Acceptance criteria:**

- Dashboard thay đổi khi đổi dataset.
- Không có data thì chart có empty state.
- Anomaly có link về rule/result detail.

### Slice 8 — Generic Data Explorer và polish

**Mục tiêu:** xem sample data mà không hardcode domain.

- Dynamic columns từ schema/API.
- Type-aware renderer.
- API-supported filters.
- Bounded rows/pagination.
- Accessibility/responsive polish.

**Acceptance criteria:**

- Không còn `trip_distance`, `fare_amount`, `payment_type` trong generic renderer.
- Dữ liệu nhạy cảm không render ngoài policy.
- Table không làm chậm page với dataset lớn.

---

## 10. API integration checklist

Trước khi code từng slice, cần xác nhận backend contract tương ứng:

| Frontend need | Existing/required backend contract | Status to verify |
|---|---|---|
| List datasets | `GET /api/v1/datasets` | Đã có, kiểm tra multi-dataset |
| Profile | `GET /api/v1/datasets/{id}/profile` | Đã có |
| Proposals | `GET /api/v1/rule-proposals?dataset_id=` | Đã có |
| Review | `PATCH /api/v1/rule-proposals/{id}` | Đã có |
| DQ run | `POST /api/v1/dq-runs` | Đã có |
| Results | `GET /api/v1/dq-runs/{id}/results` | Đã có |
| Trends | `GET /api/v1/datasets/{id}/quality-trends` | Đã có |
| Rows | `GET /api/v1/datasets/{id}/rows` | Hiện có projection taxi, cần generic hóa |
| Upload | Dataset upload endpoint | Cần backend contract |
| Versions/schema | Version/schema endpoints | Cần backend contract |
| Semantic contract | Understand/get/update endpoints | Cần backend contract |
| Evidence detail | Profile/evidence endpoint | Có thể derive trước, cần chốt |

Nếu endpoint mới chưa sẵn sàng, frontend dùng feature flag/disabled state, không tự giả lập production success.

---

## 11. Dependency policy

### Giữ nguyên trong P0

- React.
- TypeScript.
- Vite.
- Existing CSS tokens/layout.

### Chỉ thêm khi có lý do

- `react-router`: chỉ khi route/deep-link là yêu cầu thật; không thêm chỉ để tách component.
- Chart library: chỉ khi SVG hiện tại không đáp ứng trend/drill-down.
- Form/schema library: chỉ khi rule editor có nhiều schema động hơn khả năng của controlled inputs.
- UI framework như Ant Design: không migrate toàn bộ trong scope này; nếu dùng, chỉ dùng isolated component sau khi đánh giá bundle/visual consistency.

Mọi dependency mới phải có:

- Lý do.
- Bundle/performance impact.
- License check.
- Build/test verification.

---

## 12. Testing plan cho frontend

### 12.1 Type/build checks

- `npm run build` phải pass.
- Không có implicit `any` mới.
- API response mapping có type guard cho payload không tin cậy.
- Mock API compile cùng `ApiClient` interface.

### 12.2 Component behavior

- Dataset switching.
- Upload validation.
- Loading/error/empty states.
- Contract edit/save failure.
- Rule filters and status transitions.
- Reject note required.
- Bulk action confirmation.
- Run polling timeout/retry.
- Dashboard no-data state.

### 12.3 Integration smoke

```text
Login
→ List datasets
→ Select dataset A
→ View profile
→ View/edit contract
→ Load proposals
→ Approve/edit/reject
→ Run approved rules
→ View results/dashboard
→ Select dataset B
→ Verify all views update to B
```

### 12.4 Visual QA

- Desktop width 1440px.
- Laptop width 1280px.
- Tablet width 768px.
- Mobile width 375px.
- Light/dark theme.
- Long dataset name, long column name, zero proposals, many proposals, API error.
- Keyboard focus and readable contrast.

### 12.5 Demo QA

- Public URL không bật mock ngoài ý muốn.
- Agent mode badge khớp thực tế.
- Dataset demo đã seed sẵn.
- Không lộ credentials, raw PII hoặc debug payload.
- Workflow có thể hoàn thành trong thời lượng video demo.

---

## 13. Phân công frontend

### Đạt — owner frontend/agent integration

- App shell và workspace state.
- Component extraction từ `App.tsx`.
- Dataset catalog, contract, rules, dashboard.
- API client/type integration.
- Frontend smoke test và visual QA.

### Kiên — backend contract support

- Confirm dataset/schema/upload/semantic endpoints.
- Generic row/profile response.
- Stable error/status payload.
- CORS, session và deployment configuration.

### Chiến — evaluation-facing UI

- Evidence shape và evaluation metric response.
- Dataset fixture thứ hai.
- Benchmark result presentation.
- Verify terminology giữa DeepEval report và UI.

### Phong — integration/deployment

- Mock/real env configuration.
- Public frontend/API deployment.
- Build pipeline và health checks.
- Demo seed data và release checklist.

---

## 14. Acceptance criteria tổng hợp

Frontend được xem là đạt khi:

- User có thể chọn ít nhất hai dataset khác schema.
- Không có flow chính nào phụ thuộc `datasets[0]`.
- Upload/status/error state được thể hiện rõ.
- Schema/profile render dynamic.
- Semantic contract có thể xem và chỉnh sửa.
- Rule proposal hiển thị evidence, confidence, status và parameters.
- Approve/reject/edit hoạt động và có confirmation/error feedback.
- Chỉ approved rule được execute.
- Run progress/result/dashboard gắn đúng dataset/version/run.
- Data Explorer không hardcode taxi fields.
- Mock và real adapter cùng interface.
- `npm run build` pass.
- E2E smoke với dataset A và B pass.
- Responsive/visual QA không có blocker cho demo.
- Public URL không leak secret hoặc chạy nhầm mock mode.

---

## 15. Tự đánh giá và điều chỉnh kế hoạch

### 15.1 Kiểm tra phạm vi ban đầu

Bản kế hoạch ban đầu có nguy cơ:

- Tách quá nhiều component cùng lúc.
- Thêm Ant Design/Recharts dù package chưa có.
- Đòi hỏi WebSocket và route framework trước khi API contract ổn định.
- Xây semantic contract UI khi backend endpoint chưa chốt.
- Bao gồm quá nhiều dashboard và advanced visualization.

### 15.2 Điều chỉnh sau review

Các quyết định đã được đưa vào bản này:

1. Chia frontend theo vertical slice, không refactor toàn bộ `App.tsx` một lần.
2. P0 chỉ dùng React/TypeScript/Vite/CSS hiện có.
3. Polling trước, WebSocket để P2.
4. Generic dataset selection và API contract được ưu tiên hơn visual polish.
5. Data Explorer fallback về schema/profile nếu backend chưa hỗ trợ row projection generic.
6. Chỉ thêm dependency sau khi có lý do và kiểm tra bundle.
7. Mock API phải có dataset thứ hai, nếu không thì không thể chứng minh multi-dataset.
8. Semantic contract và evidence là UI riêng, không nhồi vào Rule table.
9. Dashboard chỉ hiển thị metric mà backend có nguồn dữ liệu xác định.
10. Mọi trạng thái Agent/mock/error phải hiển thị minh bạch.

### 15.3 Kết luận tự đánh giá

Kế hoạch sau điều chỉnh phù hợp hơn với code hiện tại và thời gian ngắn vì:

- Có thể triển khai từng slice và luôn giữ được bản chạy được.
- Không phụ thuộc vào việc cài thêm một UI framework lớn.
- Xác định rõ các blocker backend trước khi frontend chờ vô hạn.
- Tách MVP khỏi future work.
- Có acceptance criteria kiểm chứng được bằng build, smoke test và visual QA.

Rủi ro còn lại lớn nhất là backend chưa có upload/version/semantic contract API. Nếu các API này chưa được chốt, P0 frontend phải dùng contract mock rõ ràng và đánh dấu feature chưa kết nối, không trình bày như đã hoàn thiện.

### 15.4 Scope score sau review

| Tiêu chí | Đánh giá | Ghi chú |
|---|---:|---|
| Bám code hiện tại | 8/10 | Có tính tới `App.tsx`, `ApiClient`, mock API và CSS hiện có |
| Khả năng demo | 8/10 | Có vertical slices và acceptance flow xuyên suốt |
| Phụ thuộc backend | 6/10 | Upload, version và semantic contract cần contract mới |
| Rủi ro dependency | 9/10 | P0 không bắt buộc thêm Ant Design, Recharts hoặc state library |
| Khả năng kiểm thử | 8/10 | Có build, mock/real contract, E2E và visual QA |
| Tổng thể | **8/10** | Đủ chi tiết để implement, nhưng cần chốt API trước các slice mới |

### 15.5 Critical path và fallback

Critical path frontend chỉ gồm năm khối:

```text
Dataset context
→ Generic schema/profile
→ Semantic contract
→ Rule review
→ Run result/dashboard
```

Upload UI, version picker và evidence drawer có thể phát triển song song nhưng không được chặn việc tách workspace state.

Nếu backend mới chưa sẵn sàng tại thời điểm implement:

- Dùng mock contract có cùng response shape.
- Hiển thị badge `Preview` hoặc `Not connected` trên feature tương ứng.
- Không đưa dữ liệu mock thành kết quả Agent thật.
- Giữ các endpoint cũ làm đường demo ổn định.
- Chỉ bật CTA upload/understanding khi capability flag từ config/API cho phép.

Như vậy frontend vẫn có thể hoàn thiện shell, dataset switching, contract viewer, rule board và dashboard mà không tạo cảm giác tính năng đã được tích hợp khi backend chưa có API.

---

## 16. Definition of Ready trước khi implement

Một slice chỉ được bắt đầu khi có:

- API request/response example hoặc mock contract.
- TypeScript types đã thống nhất.
- Loading/success/error behavior.
- Owner và reviewer.
- Acceptance criteria.
- Dataset fixture để test.

## 17. Definition of Done sau mỗi slice

- Component đã tách đúng boundary.
- Real và mock adapter cùng compile.
- `npm run build` pass.
- Happy path và error path đã test.
- Không có hardcode dataset-specific trong generic component.
- Responsive check ở ít nhất desktop và mobile.
- Có screenshot/video evidence nếu slice là một phần của hồ sơ demo.
