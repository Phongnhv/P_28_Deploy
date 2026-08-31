# Báo cáo: sửa lỗi Import dataset và dựng lại Bước 1 · Chuẩn bị dữ liệu

Ngày: 29/08/2026 · Nhánh: `chien` (HEAD `a2ee821`)

> Lưu ý trạng thái: phần A1 và A2 (`session_service.py`, `rule_store.py`) đã
> được commit vào `a2ee821`. Toàn bộ phần còn lại vẫn đang nằm ở working tree,
> chưa commit.

Tài liệu này ghi lại hai việc làm liên tiếp trong cùng một phiên: (A) sửa lỗi
import dataset đang chặn toàn bộ luồng, và (B) dựng lại màn hình Bước 1 theo bố
cục mới. Phần A là điều kiện cần của phần B — không import được thì không có gì
để chuẩn bị.

---

## Phần A — Import dataset bị lỗi

Triệu chứng người dùng thấy: banner đỏ *"Action failed — The service is
temporarily unavailable. Retry when it is ready."* mỗi khi bấm Import.

Thông báo đó **sai sự thật**. Backend vẫn chạy bình thường. Có 4 lỗi xếp chồng.

### A1. Không có workspace nào tồn tại trong DB

`POST /api/v1/workspaces/{id}/datasets/import` bắt buộc caller phải có
membership `ACTIVE`:

```python
membership = db.query(WorkspaceMembershipModel).filter_by(
    workspace_id=workspace_id, user_id=..., status="ACTIVE").first()
if not account or not membership:
    raise HTTPException(404, {"code": "WORKSPACE_NOT_FOUND", ...})
```

Truy vấn `data/gate2_mvp.db` cho thấy `workspaces`, `workspace_memberships`,
`dataset_versions` đều **0 dòng**. `grep "WorkspaceModel("` trên toàn `src/`
không có chỗ nào tạo workspace — chỉ có trong `tests/`. Cũng không có endpoint
`POST /workspaces`. Luồng import versioned chưa từng chạy được ngoài test.

`ensure_demo_steward` có sẵn đoạn gắn membership nhưng bọc trong `if workspace:`
— nó chỉ gắn ghế khi workspace đã tồn tại, mà không ai tạo.

**Sửa:** thêm `ensure_default_workspace()` vào `src/services/session_service.py`.
Hàm tạo workspace `ws-browser` (lấy từ `DEMO_WORKSPACE_ID`, mặc định sẵn có) và
cấp ghế `ACTIVE` cho **mọi** tài khoản STEWARD/ADMIN — không chỉ `demo-steward`,
vì UI cho đăng nhập bằng tài khoản nào cũng được.

### A2. `ensure_demo_steward` được import nhưng không bao giờ gọi

`src/services/rule_store.py:33` import hàm này rồi bỏ đó. Import chết.

**Sửa:** gọi `ensure_demo_steward()` và `ensure_default_workspace()` trong
`init_db()`, đặt **sau** `ensure_default_users()` vì bảng `workspaces` cần
`created_by` trỏ tới một user có thật.

### A3. Thiếu `VITE_WORKSPACE_ID` — đây là thứ tạo ra đúng dòng chữ trong ảnh

`frontend/src/api/client.ts:65` đọc biến này; dòng 161 chặn ngay nếu rỗng:

```ts
const workspaceId = (import.meta.env.VITE_WORKSPACE_ID ?? "").trim();
if (!workspaceId) throw new ApiError(503, "WORKSPACE_NOT_CONFIGURED", ...);
```

Biến này **không có** trong `.env`, `.env.local` lẫn `.env.example`. Request
chưa hề rời khỏi trình duyệt. Và vì lỗi giả mang status `503`, nó rơi vào nhánh
cuối của `getErrorMessage` trong `App.tsx`:

```ts
if (error.status >= 500)
  return "The service is temporarily unavailable. Retry when it is ready.";
```

→ lỗi cấu hình phía client bị nguỵ trang thành sự cố server, đẩy người sửa đi
sai hướng hoàn toàn.

**Sửa:** thêm `VITE_WORKSPACE_ID=ws-browser` vào cả 3 file env, và cho
`getErrorMessage` trả về thông báo thật của lỗi khi `code` là
`WORKSPACE_NOT_CONFIGURED`.

### A4. Worker profiling trỏ vào hostname chỉ có trong Docker

Phát hiện khi smoke test. `src/services/gcp_run.py:22` mặc định
`LOCAL_WORKER_URL="http://worker:8001/run"`. Chạy trực tiếp trên máy thì
`getaddrinfo failed` → job chuyển `FAILED_RETRYABLE` → dataset import xong vẫn
kẹt profiling vĩnh viễn.

**Sửa (bản đầu, đã thay):** bật `WORKER_DISPATCH_MODE=inline` trong `.env`.
Cách này chạy được nhưng là một hack cấu hình — nó ép bỏ qua worker kể cả khi
worker đang chạy, và có nguy cơ bị mang nhầm lên production.

**Sửa (bản cuối):** vá đúng gốc trong `src/services/job_dispatch.py`. Khi
`dispatch_cloud_run_job` thất bại và `APP_ENV != "production"`, job được chạy
ngay trong tiến trình thay vì bị đánh `FAILED_RETRYABLE`:

```python
if dispatch_cloud_run_job(job_id, job_type):
    return True
if settings.app_env != "production":
    logger.warning("Worker dispatch failed for job %s; running it in-process.", job_id)
    return _run_persisted_job(job_id, job_type)
return False
```

Nhờ vậy: chạy trong docker-compose thì vẫn dùng worker container như cũ; chạy
thẳng trên máy thì tự xoay sở; còn **production vẫn fail to tiếng**, vì ở đó
thiếu worker là sự cố thật chứ không phải chuyện môi trường dev.

`WORKER_DISPATCH_MODE=inline` đã được **gỡ khỏi `.env`** — không còn cần nữa.

### Kiểm chứng phần A

Smoke test qua `TestClient` với tài khoản `steward`:

```
import: 202 | version: READY | job: SUCCEEDED
```

DB sau seed: workspace `ws-browser` + 4 membership `ACTIVE`. Dataset rác do
smoke test tạo đã được xoá.

### A5. Description hardcode nói sai trạng thái

Dòng *"Aggregate profiling is in progress"* trên thẻ `nyc yellow demo` và
`train` là **description hardcode** từ endpoint legacy `/datasets/import`. Nó
được đóng băng lúc import và không bao giờ được cập nhật, nên vẫn báo "đang
profiling" rất lâu sau khi profiling đã xong — trong DB cả hai đều đã
`PROFILE_READY`.

**Sửa:** bỏ hẳn mệnh đề tiến độ khỏi description (`"Imported CSV/Parquet
dataset."`). Tiến độ đã có trường `status` phụ trách; một câu chữ tĩnh không nên
tranh việc đó rồi nói ngược lại. Đồng thời **backfill 2 dòng** đã lưu trong
`data/gate2_mvp.db` để dữ liệu cũ không còn hiển thị sai.

---

## Phần B — Dựng lại Bước 1 · Chuẩn bị dữ liệu

### Bối cảnh trước khi sửa

Màn hình cũ chạy mọi thứ cùng lúc: chọn dataset là lập tức refresh workspace,
còn catalogue chất lượng và observatory thì **luôn hiện, cho mọi dataset một
lượt**. Không cách nào biết con số đang xem thuộc về dataset nào.

### Phát hiện quyết định thiết kế

Ba điều tìm được khi khảo sát, làm thay đổi hẳn khối lượng công việc:

1. **Nhánh LLM cho data dictionary đã có sẵn.** `src/agents/graph.py:65`:
   nếu state có `normalized_data_dictionary` thì bỏ qua node
   `data_dictionary_generator`, không thì chạy nó. Tôi chỉ cần đổ dữ liệu upload
   vào state đó — không phải viết lại logic phân nhánh.
2. **Chưa có bảng DB hay API nào để lưu dictionary** → phải tạo mới toàn bộ.
3. **`GET /datasets/{id}/rows` đã trả `schema`**, nhưng `DataExplorerDialog`
   đang **hardcode cột taxi** (`vendor_id`, `pickup_at`, `fare_amount`…). Vì vậy
   mọi dataset generic — kể cả `ToP 250 movies on imdb` — mở Data Explorer ra chỉ
   thấy bảng rỗng: dòng dữ liệu có đủ, nhưng không key nào khớp tên cột mà bảng
   hỏi.

### B1. Backend — lưu trữ data dictionary

**Bảng mới** `dataset_data_dictionaries` (`src/models/database.py`):
`dataset_id`, `dataset_version_id`, `source`, `source_filename`, `column_count`,
`payload_json`, `uploaded_by`. Ràng buộc unique theo `(dataset_id,
dataset_version_id)`. Chỉ lưu bản **upload**; bản LLM suy luận vẫn là workflow
artifact như cũ.

**Service mới** `src/services/data_dictionary_store.py`:

- Parse cả **CSV lẫn JSON**, chuẩn hoá về đúng schema `InferredDictionaryTable`
  đã có sẵn.
- Nhận nhiều cách đặt tên header thực tế: `column_name`, `field`, `ten_cot`,
  `description`, `mo_ta`, `nullable`, `ghi_chu`… vì bản export từ dbt, Excel hay
  sheet viết tay hiếm khi trùng tên trường của chúng ta.
- JSON nhận 4 dạng: `{"tables":[...]}`, `{"columns":[...]}`, list cột, và
  object `{tên_cột: mô_tả}`.
- File không có đuôi thì **sniff nội dung** thay vì từ chối.
- Ô ghi chú chứa nhiều mục được tách theo `;` `|` xuống dòng.

**3 endpoint** (`src/api/routes.py`):

| Method | Path | Ghi chú |
|---|---|---|
| `GET` | `/api/v1/datasets/{id}/data-dictionary` | Trả `null` khi chưa có — đây là *trạng thái bình thường*, không phải lỗi, nên trả 200 chứ không 404 |
| `POST` | `/api/v1/datasets/{id}/data-dictionary` | Upload thay thế bản cũ; giới hạn 5 MB; 422 kèm `INVALID_DATA_DICTIONARY` nếu parse hỏng |
| `DELETE` | `/api/v1/datasets/{id}/data-dictionary` | Gỡ bản upload để trả việc lại cho agent |

**Nối vào Graph 1A** (`src/services/graph1_workflow.py`): khi tạo run, nếu có
bản upload thì seed `normalized_data_dictionary` + `data_dictionary_source:
"supplied"` vào `state_json`, để graph tự đi nhánh bypass sẵn có.

### B2. Frontend — bố cục mới

Component mới `frontend/src/components/wizard/Step1DataPreparation.tsx`, chia
thành 5 phần đánh số, mỗi phần một hành động:

| # | Phần | Hành vi |
|---|---|---|
| 1 | Nạp bộ dữ liệu | Dropzone CSV/Parquet |
| 2 | Từ điển dữ liệu (tuỳ chọn) | Badge cho biết đang dùng **BẢN TẢI LÊN** hay **AGENT TỰ SINH**; upload / gỡ bỏ |
| 3 | Dataset đã Upload | Card gọn, đủ status · version · rows · source · Data Explorer · Delete |
| 4 | Profile dataset | Nút bấm mới hiện panel; panel **chỉ chứa dataset đang chọn** |
| 5 | Data Quality Observability | Thu gọn mặc định, bấm mới mở |

Các quyết định đáng ghi:

- **Chọn dataset không kích hoạt gì.** Vốn `selectDataset` đã không chạy phân
  tích, nhưng vì không có phản hồi nào nên cú bấm trông như trượt. Đã thêm toast
  xác nhận: *"Đã chọn X. Chọn Profile dataset để tiếp tục."*
- **Đổi dataset thì thu gọn lại phần 4 và 5.** Nếu không, panel sẽ hiện số liệu
  của dataset trước dưới tên dataset mới.
- **Panel profile bị thu hẹp phạm vi**: truyền `datasets={dataset ? [dataset] :
  []}` thay vì cả danh sách.

### B3. Data Explorer viết lại

`DataExplorerDialog.tsx` giờ **schema-driven**: lấy cột từ `response.schema`,
fallback về hợp các key của row nếu version cũ chưa có schema. Bỏ hẳn danh sách
cột taxi hardcode.

Thêm 3 tab và nút `✕`:

- **Dữ liệu mẫu** — 20 dòng đầu
- **Chi tiết cột** — thứ tự, tên, kiểu, giá trị mẫu, mô tả (ghép từ dictionary)
- **Data dictionary** — bảng đầy đủ, hoặc thông báo *"Agent sẽ tự sinh ở Graph
  1A"* nếu chưa upload

Lỗi khi tải dictionary **không chặn** phần xem dữ liệu — thiếu dictionary là
chuyện thường, còn xem dữ liệu mới là việc người dùng mở dialog để làm.

### B4. Dọn dẹp

Xoá `DatasetsPage` trong `App.tsx` (107 dòng) đã thành code chết sau khi thay
bằng component mới, và sửa comment đầu file vốn còn trỏ tới nó.

---

## Phần C — Vòng chỉnh giao diện sau khi chạy thử

Năm điểm phát hiện khi bạn dùng thật. Hai trong số đó là **lỗi chức năng**, không
phải chuyện thẩm mỹ.

### C1. Thanh 5 bước bị ghim, trôi theo khi cuộn

`.wizard-header-container` có `position: sticky; top: 0`. Ghim ở đỉnh, nó ăn mất
một dải ngang của mọi màn hình bên dưới — đúng chỗ mà lưới dataset và panel
profile cần khoảng trống.

**Sửa:** bỏ `sticky`, hạ `z-index` từ `100` xuống `1`. Đồng thời ẩn hẳn thanh này
khi có trang overlay đang mở (thêm điều kiện `!stepOverlay`), bên cạnh hai điều
kiện `!showAdmin && !showGraphs` vốn đã có.

### C2. Thừa khoảng trắng hai bên

`.page-container` giới hạn `min(100%, 1360px)`. Trên màn hình desktop thường,
mức trần đó để lại lề trắng rộng hai bên trong khi lưới dataset bên trong lại
chật.

**Sửa:** nới lên `min(100%, 1680px)`.

### C3. Phần 1 và 2 xếp dọc, đẩy danh sách dataset xuống dưới màn hình

**Sửa:** bọc hai phần đầu vào `.prep-supply-row` — grid 2 cột, tự rơi về 1 cột
dưới 980px. Hai dropzone được ép cao bằng nhau (`flex: 1 1 auto`) để không bị
lệch khi thẻ bên phải có thêm dòng badge.

### C4. Bốn nút bấm không làm gì cả — đây là lỗi thật

Các nút **"Dataset catalog →"**, **"Open observatory →"**, **"Open full view →"**,
**"View audit trail"** trong panel profile gọi `onNavigate(v)`. Nhưng handler mà
tôi truyền vào ở Bước 1 (phần B) chỉ xử lý hai giá trị:

```ts
if (v === "datasets") setWizardStep(1);
if (v === "rules") setWizardStep(3);
```

`"visualization"` và `"audit"` rơi vào khoảng không — bấm không có phản hồi nào.
Đây là thiếu sót của chính phần B, không phải lỗi có sẵn.

**Sửa:** không nối chúng vào bộ chuyển bước của wizard. Rời Bước 1 chỉ để đọc một
biểu đồ thì mất luôn bộ dữ liệu đang chuẩn bị dở — tệ hơn cả nút chết. Nội dung
được mở **đè lên trang**, đóng lại là về đúng chỗ cũ.

Component mới `components/wizard/DetailOverlay.tsx`:

- Nút `✕` ở góc phải, đóng được cả bằng phím `Esc`.
- Khoá cuộn của trang nền khi mở, khôi phục khi đóng — nếu không, đóng overlay
  sẽ trả bạn về một vị trí cuộn khác lúc mở.
- Ẩn phần eyebrow + `<h1>` trùng lặp của trang bên trong, **nhưng giữ nguyên**
  phần còn lại của khối heading — vì ở trang observatory khối đó còn chứa vòng
  tròn điểm chất lượng.

Ánh xạ nút → nội dung:

| Nút | Mở ra |
|---|---|
| Dataset catalog → | `DatasetCatalogView` (*mới*) |
| Open observatory → / Open full view → | `VisualizationPage` |
| View audit trail | `AuditPage` |

### C5. "Dataset catalog" chưa từng có nội dung riêng

Nút này trước trỏ về chính Bước 1. Vì panel profile đã bị thu hẹp về đúng một
dataset (phần B), phần so sánh chéo nhiều dataset không còn chỗ nào để ở.

**Sửa:** viết `components/wizard/DatasetCatalogView.tsx` — bảng đầy đủ: tên, mô
tả, trạng thái, số dòng, số cột, độ đầy đủ, nguồn, phiên bản. Bấm một dòng là
chọn dataset đó rồi đóng overlay.

### Điểm giữ nguyên có chủ đích

**"Generate proposals →"** và **"Open review queue →"** *không* chuyển thành
overlay. Nút đầu là một hành động thật (kích hoạt job sinh luật), nút sau đưa
pipeline sang Bước 3 — đó là bước tiến hợp lệ của quy trình, không phải màn hình
chỉ để đọc. Bọc chúng vào overlay sẽ che mất việc chúng đang thay đổi trạng thái.

---

## Phần D — Vòng chỉnh thứ hai

### D1. Chuông thông báo trên thanh trên cùng

Toast tự tắt sau 3,5 giây. Việc gì chạy xong trong lúc bạn đang đọc panel khác,
hay một lỗi vừa trôi qua, là mất hẳn, không có đường xem lại.

**Sửa:** component `components/NotificationBell.tsx` — chuông có badge đếm số
chưa đọc, bấm mở panel danh sách, có "Xoá tất cả", đóng bằng `Esc` hoặc bấm ra
ngoài. Toast vẫn hiện như cũ; chuông chỉ **giữ lại** chúng.

Điểm đáng ghi về cách nạp dữ liệu: thay vì sửa cả ~20 chỗ gọi `setToast`, tôi
dùng `useEffect` theo dõi chính giá trị `toast` và `error` đã render. Nhờ vậy một
`setToast` viết thêm sau này **tự động** vào chuông, thay vì lặng lẽ đi vòng qua
nó.

Bộ đếm chưa đọc được giữ thành biến riêng chứ không suy ra từ độ dài danh sách.
Danh sách bị chặn ở 50 mục, nên khi đầy thì độ dài đứng yên và một bộ đếm suy ra
sẽ kẹt ở 0 dù thông báo mới vẫn về.

**Không cần API mới** — đây là sự kiện phía client, không phải dữ liệu server.

### D2. Thẻ dataset bỏ bớt trường, lấp đầy hàng

Bỏ hai dòng **SỐ DÒNG** và **NGUỒN**. Chúng là chi tiết của từng bộ dữ liệu, lặp
trên mọi thẻ làm lưới vừa cao vừa loãng; Data Explorer và Dataset catalog đều đã
hiển thị chúng đúng ngữ cảnh. Thẻ giờ chỉ còn trạng thái, phiên bản, tên, mô tả
và hai nút.

Lưới đổi từ `auto-fill` sang **`auto-fit`**: track rỗng sẽ co lại, nên một danh
mục ít bộ dữ liệu trải đều cả hàng thay vì dồn sang trái để lại mảng trắng bên
cạnh — đúng hiện tượng trong ảnh 3.

### D3. Bề rộng co giãn theo màn hình

`.page-container` vẫn còn trần cứng `1680px` từ vòng trước, nên màn hình lớn vẫn
thừa lề. Đổi sang `min(100%, 2400px)` với padding `clamp(18px, 2.4vw, 44px)` —
thực tế là chiếm gần trọn bề ngang, trần rất cao chỉ để màn siêu rộng không kéo
một hàng thẻ dài vô tận.

### D4. Bỏ nút "Open observatory →" trùng lặp

Panel profile có nút này, mà phần 5 của Bước 1 ngay bên dưới cũng mở đúng panel
đó. Hai lối vào cách nhau một màn hình thì đọc thành hai thứ khác nhau. Đã bỏ nút
trên panel, giữ phần 5.

### D5. Làm dịu màu

Phần chrome của Bước 1 đang dùng màu tín hiệu bão hoà đầy đủ cho những thứ chỉ có
vai trò trang trí — badge, status pill, số thứ tự phần, viền thẻ. Đã hạ xuống tông
nhạt hơn (`#e7effb`/`#2f5fa8` cho xanh, `#e8f2ec`/`#3f7d59` cho xanh lá,
`#f7efe3`/`#92693a` cho cam). Màu tươi để dành cho thứ thật sự cần báo động.

---

## Phần E — Trang 2 (Graph 1A · Hiểu ngữ nghĩa): làm các nút chạy được

### E1. "Run Understand Agent" phải bấm hai lần

`selectDataset` xoá workflow (`setWorkflow(null)`), nên cú bấm đầu tiên vào nút
chạy luôn rơi vào nhánh `!currentWorkflow` của `startWorkflowStep`. Nhánh đó tạo
workflow rồi **`return` sớm** — không chạy bước nào cả:

```ts
if (!currentWorkflow) {
  currentWorkflow = await workflowApi.createWorkflow(dataset.id, fresh);
  ...
  setToast(`Ready to ...`);
  return;          // <- bước được yêu cầu không bao giờ chạy
}
```

Người dùng bấm, không thấy gì xảy ra, tưởng nút hỏng. Phải bấm lần hai mới chạy.

Comment cũ giải thích early return là để "vào từ màn chọn dataset không được tự
chạy stage". Nhưng cả **ba** chỗ gọi `startWorkflowStep` đều là hành động bấm nút
tường minh, không có chỗ nào là "vào từ màn chọn dataset".

**Sửa:** bỏ `return`, tạo workflow xong thì chạy tiếp đúng bước được yêu cầu. Đã
kiểm chứng backend cho phép: `POST /workflows/{id}/steps/UNDERSTAND_DATA` trên
workflow vừa tạo trả `200`, không có ràng buộc `current_step`.

### E2. Không có nút xác nhận hợp đồng ngữ nghĩa

Backend có sẵn **hai** endpoint confirm:

- `POST /api/v1/workflows/{workflow_run_id}/semantic-contract/confirm`
- `POST /api/v1/datasets/{id}/semantic-contract/confirm`

`grep "semantic"` trên `frontend/src/api/client.ts` ra **0 kết quả** — chưa bao
giờ có phía client nào gọi chúng. Hợp đồng ngữ nghĩa sinh ra rồi nằm mãi ở
`DRAFT`, không có đường ký duyệt.

**Sửa:** thêm `confirmSemanticContract` vào `types.ts`, `client.ts`, `mockApi.ts`
và một nút **"Xác nhận hợp đồng"** trong panel Bước 2, dùng endpoint theo
workflow (nó có sẵn `artifact_id` và `expected_version` để chống ghi đè). Nút
"Run Understand Agent" đổi nhãn thành "↻ Chạy lại" khi đã có hợp đồng, để hai nút
không cùng trông như hành động chính.

### E3. Type union thiếu giá trị mà backend thật sự trả về

Phát hiện khi smoke test: endpoint confirm trả `status: "CONFIRMED"`, nhưng union
trong `types.ts` chỉ có `"DRAFT" | "VALIDATED" | "APPROVED" | "REJECTED" |
"STALE"`. Điều kiện "đã xác nhận" tôi viết ban đầu so với `"APPROVED"` nên sẽ
không bao giờ đúng — nút vẫn mời bấm lại sau khi đã ký.

**Sửa:** thêm `"CONFIRMED"` vào union và cho điều kiện chấp nhận cả hai giá trị
(`APPROVED` là thứ luồng review artifact chung ghi ra).

### Kiểm chứng trang 2 — chạy thật qua API

```
createWorkflow:                      200   current_step: UNDERSTAND_DATA
runWorkflowStep UNDERSTAND_DATA:     200   job PENDING
artifacts:                           200   3 artifact, SEMANTIC_CONTRACT v1 DRAFT
confirm:                             200   DRAFT -> CONFIRMED, workflow -> PROPOSE_RULES
confirm voi expected_version sai:    409   (chong ghi de dung nhu thiet ke)
```

---

## Phần F — Vì sao "các nút không chạy được": một job lỗi làm chết cả trang

Rà lại toàn bộ nút trên Bước 2 thì phần graph (`GraphStagePanel`, `GraphFlow`,
`NodeCard`, `NodeTimeline`, `NodeDetailDrawer`) đều **bình thường** — thẻ node là
`<button>` thật, drawer mở/đóng được, và ba API đứng sau chúng đều trả 200:

```
graph/catalog:      200   G1A nodes: build_profile_digest, data_dictionary_generator, dataset_understanding
graph/node-runs:    200   9 run
node-run detail:    200
```

Nguyên nhân nằm chỗ khác, và nó không giới hạn ở Bước 2.

### F1. `pollJob` không xoá `activeJob` khi job thất bại

```ts
if (finalStatus === "SUCCEEDED") {
  await onComplete();
  setActiveJob(null);        // <- chỉ có ở nhánh thành công
  ...
} else {
  setRetryAction(...);       // <- nhánh thất bại KHÔNG xoá activeJob
  setError(...);
}
```

`activeJob` khống chế **14 chỗ** trong `App.tsx`, gồm cả các early-return:

```ts
if (!dataset || !canOperate || workflowActionBusy || activeJob) return;   // startWorkflowStep
if (!workflow || !canOperate || workflowActionBusy || activeJob) return;  // confirmSemanticContract
busy={workflowActionBusy || Boolean(activeJob)}                           // panel Bước 2
profiling={Boolean(activeJob)}                                           // Bước 1
```

Nên chỉ cần **một** job hỏng là mọi nút chạy trên mọi bước bị vô hiệu vĩnh viễn,
và bấm vào cũng không làm gì vì handler return sớm. Phải tải lại trang mới thoát.
Đây đúng là triệu chứng "các nút không chạy được".

Đáng nói: trước khi có bản vá worker ở A4, mọi job đều rơi vào
`FAILED_RETRYABLE` — nghĩa là gần như chắc chắn bạn đã rơi vào trạng thái này.

**Sửa:** gọi `setActiveJob(null)` trên **cả hai** nhánh. Job đã kết thúc thì cờ
"đang chạy" phải tắt, bất kể kết quả. Vòng lặp poll cũng thoát sau 600 lần thử
(10 phút) mà không có trạng thái kết thúc — trường hợp đó nay cũng được dọn, và
có thông báo riêng thay vì dùng chung câu lỗi của job hỏng.

### F2. `retryAction` được ghi vào nhưng không bao giờ hiển thị

`grep "retryAction"` ra đúng **một** dòng: khai báo state. `pollJob` ghi vào nó,
`startWorkflowStep` xoá nó — nhưng **không nơi nào render**. Người dùng nhận câu
"Retry the operation when ready" trong khi không hề có nút retry nào tồn tại.

**Sửa:** hiện nút **"↻ Thử lại"** trên banner lỗi khi có `retryAction`. Nút này
tách bạch với dấu `×` đóng banner, để "khôi phục sau lỗi" không bị nhầm thành
"giấu thông báo đi".

Tiện thể dịch luôn nhãn "Action failed" sang tiếng Việt khi đang ở chế độ VI —
trước đó nó là chuỗi cứng tiếng Anh.

---

## Phần G — "This workflow step is not ready to run": import versioned không được workflow công nhận

Lỗi bạn gặp khi bấm **Chạy agent hiểu dữ liệu** trên `ToP 250 movies on imdb in
2026`. Trạng thái workflow trong DB:

```
current_step: UPLOAD_PROFILE
  UPLOAD_PROFILE  -> READY
  UNDERSTAND_DATA -> LOCKED
```

Trong khi dataset đã `PROFILE_READY` và Bước 1 hiển thị đầy đủ 250 dòng / 27 cột
/ 95,4% completeness.

### Nguyên nhân gốc: hai đường profiling, hai bảng khác nhau

| Đường import | Ghi profile vào | Khoá theo |
|---|---|---|
| Legacy `/datasets/import` | `profiles` + `column_profiles` | `dataset_id` |
| **Versioned** `/workspaces/{id}/datasets/import` | `profile_runs` | `dataset_version_id` |

`GET /datasets/{id}/profile` **có** fallback đọc `profile_runs` — nên giao diện
Bước 1 hiển thị đúng. Nhưng `rule_proposer_workflow.py` thì không:

```python
profile_ready = dataset.status == "PROFILE_READY" and db.get(ProfileModel, dataset.id) is not None
```

Với dataset versioned, `ProfileModel` luôn `None` → `profile_ready = False` →
workflow sinh ra với `UNDERSTAND_DATA` LOCKED → route trả **409 "This workflow
step is not ready to run."**

Nhánh reconcile (dành cho workflow đã tồn tại) mắc **cùng một** điều kiện, nên
tạo lại workflow cũng không cứu được.

Nói cách khác: mọi dataset tải lên qua đúng đường chuẩn — chính là đường tôi đã
sửa cho chạy được ở phần A — đều không bao giờ vào được Graph 1A.

### Lỗi có ba tầng, không phải một

Mở khoá được bước rồi vẫn chưa xong. Ba hàm cùng giả định "profile nghĩa là
`ProfileModel`":

1. `_has_completed_profile` (điều kiện tạo/reconcile workflow) → khoá bước.
2. `_profile_snapshot` → *"A completed profile is required before understanding
   data."*
3. `_semantic_payload` → cùng lỗi trên, phát hiện muộn nhất vì nó chỉ chạy khi
   job đã được nhận. Lần chạy thử đầu tiên sau khi sửa (1) vẫn `FAILED` ở đây.

`_raw_profile_for_graph` cũng đọc `ColumnProfileModel` trực tiếp.

**Sửa:** thêm hai helper trong `rule_proposer_workflow.py` —
`_versioned_profile_snapshot_row` và `_snapshot_from_versioned_profile` — rồi cho
`_profile_snapshot` fallback sang snapshot versioned. `_semantic_payload` và
`_raw_profile_for_graph` được viết lại để dùng chung kết quả đã chuẩn hoá đó thay
vì tự truy vấn `ColumnProfileModel`. Nhờ vậy chỉ còn **một** định nghĩa "đã
profile" cho cả hai đường import.

Cột nào snapshot tổng hợp không có (quantile, sample_value…) được trả `None` chứ
không phải `0` — số 0 bịa ra sẽ bị agent coi là dữ kiện thật.

### Kiểm chứng trên chính dataset đang lỗi

```
createWorkflow:      200   current_step: UNDERSTAND_DATA
                           UPLOAD_PROFILE  -> COMPLETED
                           UNDERSTAND_DATA -> READY
run UNDERSTAND_DATA: 200   job SUCCEEDED
artifacts:                 PROFILE_SNAPSHOT VALIDATED / DATA_DICTIONARY DRAFT / SEMANTIC_CONTRACT DRAFT
so cot suy luan:     27    id->identifier, url->text, primaryTitle->text ...
confirm:             200   DRAFT -> CONFIRMED, workflow -> PROPOSE_RULES
```

Thêm `tests/unit/test_versioned_profile_workflow_gate.py` (4 test) chốt lại: một
snapshot versioned phải được tính là profile hoàn chỉnh, và dataset không có
profile nào vẫn phải bị báo là chưa profile.

### Ghi chú về quyền truy cập

Khi kiểm chứng, tài khoản `steward` bị **403 DATASET_ACCESS_FORBIDDEN** với
dataset này: import versioned chỉ cấp `MANAGE` cho người tải lên (`admin`), khác
với đường legacy. Đây là hành vi đúng theo thiết kế, không phải lỗi — nhưng nghĩa
là **bạn phải đăng nhập đúng tài khoản đã tải dataset lên**, nếu không mọi thao
tác trên nó đều bị chặn.

---

## Phần H — Graph 1A: dàn đều 3 node và mở chi tiết node thành một trang

### H1. Ba node dồn sang trái, thừa mảng trắng bên phải

`.graph-flow-track` là flex mặc định căn trái, còn `.graph-flow-item` để
`flex: 0 0 auto`. Ba thẻ node rộng cố định 208px nên trên màn hình rộng chúng
huddle sang trái, phần còn lại của panel bỏ trống.

**Sửa:** căn giữa track và cho **mũi tên nối co giãn**, thay vì phóng to thẻ:

```css
.graph-flow-track { justify-content: safe center; }
.graph-flow-item  { flex: 1 1 auto; }
.graph-flow-item:last-child { flex: 0 0 auto; }
.graph-flow-arrow { flex: 1 1 auto; min-width: 46px; max-width: 160px; }
```

Thẻ giữ một bề rộng dễ đọc, khoảng cách giữa các node giãn đều — đúng cách một
sơ đồ luồng nên hành xử. Dùng `safe center` chứ không phải `center`: khi hàng
rộng hơn khung nhìn, `center` sẽ đẩy mép trái của node đầu tiên ra ngoài vùng
cuộn và không thể với tới được nữa.

### H2. Chi tiết node là drawer trượt cạnh, giờ thành một trang

Panel cũ rộng `min(520px, 100vw)` trượt từ mép phải. Trong bề ngang đó, bảng tóm
tắt vào/ra bị vỡ xuống gần như mỗi dòng một từ và cần tới hai thanh cuộn mới đọc
được — thấy rõ trong ảnh bạn gửi.

**Sửa:** `.graph-drawer` chuyển thành trang phủ toàn màn hình (`inset: 0`), nút
`✕` giữ nguyên vị trí góc phải header nhưng được tạo khung rõ ràng hơn. Phần thân
được căn giữa và giới hạn `min(100%, 1040px)` — full-bleed trên màn rộng thì
không đọc nổi.

Hai bảng **Vào / Ra** đổi sang `repeat(auto-fit, minmax(300px, 1fr))` nên nằm
cạnh nhau thay vì xếp chồng, mỗi bảng có khung riêng, và `overflow-wrap: anywhere`
xoá hẳn thanh cuộn ngang.

Hai dọn dẹp đi kèm:

- **Bỏ `graph-drawer-scrim`.** Trang mới phủ kín khung nhìn nên lớp
  "bấm-nền-để-đóng" nằm dưới nó vĩnh viễn không thể bấm được — giữ lại chỉ là
  code chết. Đã xoá cả JSX lẫn CSS.
- **Khoá cuộn nền khi trang mở**, giống `DetailOverlay`, để đóng lại thì về đúng
  vị trí cuộn cũ.

---

## Phần I — Thanh tiến trình đứng im ở 20%

### Nguyên nhân

Backend chỉ ghi `job.progress` tại vài mốc rời rạc. Với `PROPOSE_RULES`
(`src/services/job_runner.py`):

```
job.progress = 20.0    # dong 813 — truoc khi goi LLM
job.progress = 60.0    # dong 820 — sau khi LLM tra ve
job.progress = 100.0   # dong 862
```

Khoảng giữa 20 và 60 chính là lời gọi LLM, có thể mất vài phút. Suốt quãng đó
thanh nằm im ở 20% và trông như bị treo.

### Điều tôi cố ý KHÔNG làm

Không cho thanh tự bò dần theo thời gian. Đó là bịa ra tiến độ mà không ai đo
được: người dùng sẽ tin vào một con số không có thật, và khi job treo thật thì
thanh vẫn bò — chính xác là lúc nó phải đứng yên để báo động.

### Cách sửa: dùng tiến độ node có thật

`src/services/node_telemetry.py` đã ghi **một dòng `RUNNING` ngay khi mỗi node
bắt đầu** (kèm `started_at`), rồi cập nhật thành `SUCCEEDED`/`FAILED` khi xong.
Đây là bằng chứng đo được, mịn hơn hẳn, về **cùng một công việc** — chỉ là chưa
ai dùng nó cho thanh tiến trình.

`ProgressPanel` nay nhận thêm `nodeProgress`, tính trong `App.tsx` từ `nodeRuns`:

- Ánh xạ job type → graph key (`PROPOSE_RULES` → `G1B`, `UNDERSTAND_DATA` →
  `G1A`, …).
- **Chỉ lấy graph run mới nhất** — danh sách còn chứa các lần chạy cũ của cùng
  dataset, đếm cả vào thì số sẽ sai.
- Tổng số node lấy từ `graphCatalog`, số node xong = đếm `SUCCEEDED`/`SKIPPED`.

Công thức: `percent = max(job.progress, min(95, done / total * 100))`

Chặn trên ở **95** là có chủ đích: node chạy hết không đồng nghĩa job xong (còn
khâu ghi dữ liệu sau đó), và chỉ job mới được quyền tuyên bố 100%.

Panel cũng hiển thị thêm:

- **`Node k/n · <tên node đang chạy>`** — lấy từ dòng `RUNNING`.
- **Đồng hồ thời gian đã chạy**, tính từ `started_at` của node đầu tiên.
- **Vệt sáng chuyển động** trên thanh khi job còn chạy. Vệt này **không mang con
  số nào** — nó chỉ nói "job còn sống", để một quãng chưa có phép đo mới không bị
  đọc thành treo máy. Có tắt theo `prefers-reduced-motion`.

`ProgressPanel` lấy ngôn ngữ từ `useI18n()` thay vì nhận prop, nên cả ba chỗ gọi
không phải truyền gì thêm; riêng `WorkflowPage` (Bước 3 — nơi chạy
`PROPOSE_RULES`, đúng job trong ảnh) được thêm prop `nodeProgress`.

### Kiểm chứng — quan sát trong lúc job đang chạy

Lấy mẫu `node-runs` mỗi 3 giây song song với một lần chạy Graph 1A thật:

```
(1, 'data_dictionary_generator')   -> thanh 33%
(2, 'dataset_understanding')       -> thanh 66%
(3, None)                          -> thanh 95%, roi 100% khi job bao SUCCEEDED
```

Thay vì đứng im ở 20%, thanh đi qua các mốc đo được và luôn kèm tên node đang
chạy.

---

## Phần K — Chỉnh lại Graph 1A sau vòng H

Vòng H tôi sửa quá tay ở một điểm và chọn sai hướng ở một điểm. Cả hai được sửa
lại ở đây.

### K1. Thẻ node vẫn nhỏ, chỉ mũi tên giãn ra

Ở phần H tôi cho `.graph-flow-arrow` giãn tới `160px` còn `.graph-node-card` giữ
`width: 208px` cố định. Kết quả: ba thẻ nhỏ nằm rải rác giữa những mũi tên rất
dài — dàn đều thì đúng, nhưng khoảng trống chuyển từ mép phải vào giữa các thẻ,
không giải quyết được gì.

**Sửa:** đảo lại. Phần dư đi vào **thẻ**, không vào đường nối:

```css
.graph-flow-item  { flex: 1 1 0; }          /* mỗi node một phần bằng nhau */
.graph-flow-arrow { flex: 0 0 auto; width: 56px; }
.graph-node-card  { flex: 1 1 auto; width: auto; min-width: 208px; max-width: 420px; }
```

`min-width` giữ cho mã node không bị xuống dòng; `max-width` giữ cho dòng mô tả
không kéo dài thành một hàng chữ khó đọc trên màn siêu rộng.

### K2. Chi tiết node: trang riêng → hộp thoại nổi trên trang

Phần H đổi drawer thành trang phủ toàn màn hình. Đó là hiểu sai yêu cầu: mở toàn
trang thì mất luôn sơ đồ mà nội dung đang giải thích, và người đọc mất chỗ đang
đứng.

**Sửa:** hộp thoại nổi trên nền mờ, theo đúng mẫu `explorer-dialog` đã có sẵn
trong dự án — `width: min(1080px, 100vw - 40px)`, `max-height: 100vh - 72px`,
nút `✕`, đóng bằng `Esc` hoặc bấm nền. Sơ đồ vẫn nhìn thấy phía sau.

Lớp `graph-drawer-scrim` đã bị xoá ở phần H nay được khôi phục — lần này nó có
tác dụng thật vì hộp thoại không còn phủ kín khung nhìn.

### K3. Bảng "Tóm tắt vào / ra" vỡ thành từng ký tự một dòng

Đây là lỗi thật, thấy rõ trong ảnh: cột bên phải hiển thị `d i c t 4` theo chiều
dọc.

Nguyên nhân ở `SummaryTree` — nó đệ quy, và **mỗi tầng lồng nhau lại chia đôi bề
ngang còn lại**:

```css
.graph-summary-entry { grid-template-columns: minmax(90px, 34%) 1fr; }
```

Xuống bốn tầng, cột giá trị chỉ còn vài pixel nên mỗi ký tự rơi xuống một dòng.

**Sửa:** chỉ tầng ngoài cùng mới đặt khoá cạnh giá trị; từ tầng 1 trở vào thì xếp
dọc và dựa vào đường kẻ thụt lề để thể hiện độ sâu:

```css
.graph-summary-object:not(.depth-0) > .graph-summary-entry {
  grid-template-columns: minmax(0, 1fr);
}
```

Đường kẻ thụt lề đổi sang màu nhấn nhạt (`color-mix` 22%) thay vì viền xám, cho
cấu trúc dễ đọc mà không nặng nề.

---

## Phần L — Bố cục thẻ node và cấu trúc Vào / Ra

### L1. Thẻ node: rộng nhưng thưa

Sau vòng K thẻ giãn tới `420px` nhưng nội dung vẫn ngắn, nên thẻ trông rỗng và
các mốc thời lượng ở chân thẻ không thẳng hàng nhau.

**Sửa:**

- Hạ `max-width` xuống `340px`. Quá mức đó thì dòng mô tả hai hàng duỗi thành
  một hàng dài và thẻ đọc ra như bị bỏ trống.
- Dựng lại lưới trong thẻ: `grid-template-rows: auto auto auto 1fr auto` —
  badge / tên / mã / mô tả / chân thẻ. Hàng `1fr` nằm ở mô tả nên **chân thẻ bị
  đẩy xuống đáy**, thời lượng của cả hàng thẻ thẳng nhau bất kể mô tả dài ngắn.
  (Trước đó tôi đặt 4 hàng cho 5 phần tử con — lỗi của chính vòng này, phát hiện
  khi đối chiếu lại JSX.)
- Chân thẻ tách bằng một đường kẻ mảnh; mã node (`build_profile_digest`…) đổi
  thành chip nền nhạt thay vì chữ xám trôi nổi.
- Viền thẻ đổi màu nhẹ khi hover thay vì chỉ nhấc lên.

### L2. Vào / Ra: sửa đúng chỗ gây rối

Đây không phải chuyện thẩm mỹ. `summarize()` trong
`src/services/node_telemetry.py` thay mọi container bị lược bỏ bằng một
**descriptor**:

```python
{"type": "dict",    "keys":  n}
{"type": "records", "count": n, "fields": [...]}
{"type": "list",    "count": n, "sample": [...]}
```

Giao diện lại vẽ chúng như key/value thường, nên **một dữ kiện biến thành hai ba
dòng**: `type / dict`, rồi `keys / 4`. Nhân với hàng chục nhánh lồng nhau thì ra
đúng bãi chữ trong ảnh bạn gửi.

**Sửa:** `SummaryTree` nhận diện descriptor và phát biểu dữ kiện **một lần**,
dưới dạng chip:

| Trước | Sau |
|---|---|
| `type/list` + `count/0` | `0 phần tử` |
| `type/records` + `count/21` + `fields/[5 mục]` | `21 bản ghi` + chip từng tên trường |
| `type/dict` + `keys/4` | `4 khoá` |

Chống nhận nhầm: chỉ coi là descriptor khi **mọi** khoá của object đều nằm trong
tập `{type, keys, count, fields, sample}`. Một payload thật có trường tên `type`
(ví dụ mô tả cột `{name, type, role}`) vẫn được vẽ như bình thường vì nó còn
`name` và `role`.

Thêm vào:

- `sample` của list được vẽ lồng dưới nhãn "mẫu" thay vì thành một nhánh ngang
  hàng, để phân biệt "đây là ví dụ" với "đây là dữ liệu".
- Boolean có chip riêng (xanh / hổ phách) thay vì chữ `true` trôi.
- Khoá `…` mà summariser dùng để đánh dấu cắt bớt được in nghiêng mờ — nó là ghi
  chú về danh sách, không phải một trường của payload.
- Hai khối **VÀO** / **RA** có tiêu đề gạch chân riêng.

Màu chip chọn theo tông nhạt cùng hệ với phần còn lại: xanh dương cho `dict`,
xanh lá cho `records`, tím cho `list`, xám cho kiểu không xác định.

### Kiểm chứng — chạy bộ nhận diện trên payload thật

Lấy `input_summary` / `output_summary` thật của node `data_dictionary_generator`
rồi duyệt cây:

```
== VAO ==
  dataset_profile_digest...cross_column_hints  => list    -> 0 phan tu
  dataset_profile_digest...sample              => dict    -> 2 khoa
  dataset_profile_digest...schema_constraints  => dict    -> 3 khoa
  dataset_profile_digest...columns             => records -> 21 ban ghi, fields=[name,type,role,null_pct,signals]
  dataset_profile...table_metadata             => dict    -> 5 khoa
  dataset_profile...columns                    => dict    -> 21 khoa
  target_tables                                => list    -> 1 phan tu
== RA ==
  normalized_data_dictionary.tables...         => dict    -> 4 khoa
  data_dictionary_inference_errors             => list    -> 0 phan tu
```

Đúng những nhánh gây rối trong ảnh, và không có nhánh nào bị gom nhầm.

---

## Phần M — Ba bước của Graph 1A và bảng đặc tả Vào / Ra

### M1. Khoảng trống bên phải: nguyên nhân là hai giới hạn đánh nhau

Ở vòng L tôi đặt `max-width: 340px` cho **thẻ**, trong khi `.graph-flow-item` vẫn
`flex: 1 1 0` nên **ô chứa** vẫn giãn hết cỡ. Phần dư nằm lại bên trong ô, dồn
thành dải trắng ở cuối hàng — đúng hiện tượng trong ảnh.

**Sửa:** bỏ giới hạn ở thẻ (thẻ lấp đầy ô của nó), và chuyển giới hạn lên **cả
hàng**:

```css
.graph-flow-track { width: 100%; max-width: 1240px; margin: 0 auto; }
.graph-node-card  { flex: 1 1 auto; min-width: 208px; }   /* khong con max-width */
```

Giới hạn đặt ở hàng thì phần dư nằm ngoài hàng và hàng được căn giữa, thay vì
nằm bên trong từng ô.

### M2. Ba node đọc thành ba bước

Ba node vốn chỉ là ba thẻ ngang hàng, không có gì nói đây là một trình tự.

- `NodeCard` nhận thêm `step` / `totalSteps`, hiện **①②③** dạng `1/3` ở góc trái
  hàng đầu.
- `.graph-node-top` đổi từ `space-between` sang `flex-start` + đẩy dấu trạng thái
  sang phải bằng `margin-left: auto`. Giữ `space-between` với ba phần tử sẽ làm
  badge LLM/DETERMINISTIC trôi ra giữa thẻ.
- Đường nối đổi sang màu nhấn nhạt (`color-mix` 30%) thay vì xám mờ gần như
  không thấy; nhãn điều kiện nhánh (`no dictionary supplied`) thành chip nền
  xanh nhạt nằm hẳn trên đường nối, không còn lẫn vào chữ của thẻ bên cạnh.

### M3. Vào / Ra: từ cây thụt lề thành bảng đặc tả

Vòng L đã gom descriptor thành chip, nhưng phần khung vẫn là một cây thụt lề nên
vẫn khó dò: không rõ đâu là ranh giới giữa hai trường cấp 1.

**Sửa:**

- **Cấp ngoài cùng thành bảng**: mỗi trường là một hàng có `padding` và đường kẻ
  ngăn dưới, khoá bên trái (đậm hơn), giá trị bên phải. Đọc như một bảng đặc tả
  thay vì một khối văn bản.
- **Cấp lồng thành khối lõm**: nền nhạt dần theo độ sâu (`depth-1` đậm nhất,
  `depth-3` trong suốt) kèm viền trái màu nhấn, nên nhìn ra ngay một cấu trúc con
  bắt đầu và kết thúc ở đâu.
- **Hai khối Vào / Ra được phân biệt bằng màu và hướng**, không chỉ bằng chữ:
  khối vào viền xanh dương + mũi tên `↓`, khối ra viền xanh lá + mũi tên `↑`.
  Nhãn đổi thành "Dữ liệu vào" / "Dữ liệu ra" cho rõ đây là dữ liệu, không phải
  tên tham số.

Bảng màu giữ cùng hệ nhạt đã dùng cho chip ở vòng L: xanh dương `#3f6ea8`
/`#e9f0fa`, xanh lá `#3f7d59`/`#e8f2ec`.

---

## Phần N — Bước 3 (Graph 1B · Sinh luật)

### N1. Bỏ rail hai phase bên trái

Rail "Propose & review / Publish & monitor" là các `<button disabled>` — không
bấm được, và nó nhắc lại đúng thông tin mà thanh 5 bước ở đầu trang đã có. Nó
chiếm một cột chiều ngang của phần nội dung thật.

**Sửa:** xoá `<aside className="workflow-stepper">`, `.workflow-layout` chuyển
sang một cột. Hàm `phaseStatus` chỉ phục vụ rail đó nên cũng được xoá theo thay
vì để lại code chết.

### N2. Bước 3 chia thành ba phần đánh số

Trước đó trang mở thẳng vào hàng đợi bốn mươi luật, chôn mất hợp đồng ngữ nghĩa
mà những luật đó được suy ra từ.

| Phần | Nội dung |
|---|---|
| ① | Hợp đồng ngữ nghĩa + bằng chứng profile — **hiện sẵn** |
| ② | Nút **Sinh Rule** (đổi thành "↻ Sinh lại luật" khi đã có đề xuất) |
| ③ | Hàng đợi duyệt — **chỉ mở sau khi bấm**, hoặc bấm "Xem N đề xuất" nếu đã có sẵn từ lần chạy trước |

Ảnh 3 (`INFERRED SCHEMA`) và ảnh 4 (`PROFILE EVIDENCE`) vốn đã nằm chung một
artifact panel; phần ① giữ nguyên cặp đó và đưa lên trước nút sinh luật.

### N3. Nút Approve luôn hiện

`canApprove = proposal.status !== "APPROVED"` khiến nút **biến mất hoàn toàn** với
luật đã duyệt — nên trong ảnh chỉ còn hai nút. Đó là hành vi đúng về logic (không
duyệt lại thứ đã duyệt) nhưng đọc ra như thiếu chức năng.

**Sửa:** nút luôn ở đó, chuyển sang trạng thái vô hiệu **"✓ Đã duyệt"** nền xanh
lá nhạt. Hàng hành động giữ nguyên ba nút ở mọi trạng thái, và trạng thái hiện
tại đọc được ngay trên nút.

### N4. Duyệt / từ chối hàng loạt — API mới

**Kiểm tra API có sẵn trước:** có `POST /api/v1/dq/runs/{run_id}/rules/bulk-review`,
nhưng nó khoá theo `run_id` của bảng `proposed_rules` legacy, còn giao diện đọc
đề xuất qua `GET /api/v1/rule-proposals?dataset_id=…` (bảng `rule_proposals`).
Hai đường dữ liệu khác nhau nên không dùng lại được.

Phương án thay thế là để frontend gọi `PATCH` 41 lần. Không chọn: chậm, và
**không nguyên tử** — hỏng giữa chừng sẽ để hàng đợi ở trạng thái không ai chọn.

**Tạo mới:** `POST /api/v1/rule-proposals/bulk-review`

```json
{ "dataset_id": "...", "action": "approve" | "reject", "pending_only": true }
```

- `pending_only` mặc định **true**: một thao tác hàng loạt không được âm thầm lật
  ngược những quyết định Steward đã cân nhắc từng cái một.
- Logic duyệt/từ chối được tách khỏi handler `PATCH` thành
  `_apply_proposal_approval` / `_apply_proposal_rejection` và **dùng chung** cho
  cả hai đường, nên duyệt hàng loạt tạo `RuleVersion` + `RuleConfiguration` y hệt
  duyệt lẻ — không có đường tắt bỏ sót bước nào.
- Tiện thể gom phần serialize proposal (24 dòng lặp) thành `_serialize_proposal`
  dùng chung cho cả route listing.

Giao diện: nút **Duyệt tất cả / Từ chối tất cả** đặt ở **cả đầu và cuối** danh
sách — sau khi cuộn qua bốn mươi luật thì nút ở đầu trang đã trôi mất. Có hộp xác
nhận nêu rõ số lượng, vì đây là thao tác khó hoàn tác.

### Kiểm chứng

Chạy trên dataset thật của bạn với `pending_only` — **không đụng vào 41 luật đã
duyệt**:

```
truoc:                          Counter({'APPROVED': 41})
bulk approve (pending_only):    200, tra ve 41
sau:                            Counter({'APPROVED': 41})
action sai ("delete"):          422 INVALID_BULK_ACTION
```

Phần thay đổi trạng thái được chứng minh bằng
`tests/unit/test_bulk_proposal_review.py` (4 test, DB riêng trong bộ nhớ): duyệt
tạo đúng `RuleVersion` + `RuleConfiguration`, duyệt hai lần không nhân bản
version, từ chối thu hồi version đã cấp (nếu không thì luật bị từ chối vẫn được
lần chạy sau nhặt lên), và từ chối một đề xuất chưa từng duyệt không phải lỗi.

---

## Phần O — Năm lỗi báo sau vòng N

### O1. Signal gộp vào đúng dòng cột

Khoá bằng chứng được đặt tên `profile.column.<tên cột>.<tín hiệu>`, tức **mỗi
khoá đã thuộc về một cột cụ thể**. Liệt kê tất cả thành một bức tường chip ở cuối
trang buộc người đọc tự ghép ngược lại với các dòng phía trên.

**Sửa:** mỗi dòng cột tự lọc `profile.column.<tên>.` của riêng nó và hiện phần
đuôi (`null_rate`, `quantile.p95`…) thành chip nhỏ ngay dưới dòng đó. Khối
"Signals used by Agent" giữ lại **chỉ** những tín hiệu mức toàn bộ dữ liệu
(`profile.row_count`, `profile.completeness_score`…).

### O2. "Sinh Rule" báo lỗi — ba tầng chặn chồng nhau

Bấm Sinh Rule trả *"This workflow step is not ready to run."* Truy DB thấy:

```
current_step: UNDERSTAND_DATA
  UNDERSTAND_DATA : COMPLETED
  PROPOSE_RULES   : WAITING_APPROVAL
```

Ba tầng chặn, phải gỡ lần lượt mới lộ ra tầng sau:

1. **Route** chỉ cho chạy khi status ∈ `{READY, FAILED, COMPLETED}`. Đang
   `WAITING_APPROVAL` → 409. Nghĩa là **bất kỳ dataset nào đã từng sinh luật thì
   không bao giờ sinh lại được**.
2. **`execute_step`** đòi `run.current_step == step_key`. Xác nhận hợp đồng đẩy
   con trỏ tới `PROPOSE_RULES`, nhưng các đường khác để nó ở lại
   `UNDERSTAND_DATA` → chặn tiếp.
3. Gỡ xong hai tầng trên mới hiện lỗi **thật sự có nghĩa**: *"Confirm the current
   semantic contract before proposing rules."*

**Sửa:**

- Route và `execute_step` cho phép chạy lại một chặng **đã từng chạy**
  (`COMPLETED` / `WAITING_APPROVAL` / `FAILED` / `RUNNING`). Chặng `LOCKED` vẫn bị
  chặn nên thứ tự quy trình không bị phá. `RUNNING` phải nằm trong danh sách vì
  route lật status sang `RUNNING` **ngay trước khi** dispatch, nên tới lúc
  `execute_step` chạy thì trạng thái đã cho phép nó đã bị ghi đè.
- Điều kiện thứ ba là quy tắc nghiệp vụ đúng, không nên gỡ. Thay vào đó **nói ra
  trước khi bấm**: phần ② của Bước 3 hiện dòng "Hợp đồng ngữ nghĩa chưa được xác
  nhận", nút Sinh Rule bị vô hiệu, và có sẵn nút **"Xác nhận hợp đồng"** ngay
  cạnh — không phải quay lại Bước 2 tìm.

### O3. Panel Profile tự đóng khi cuộn

`Step1DataPreparation` có `useEffect` reset `showProfile`/`showObservatory` khi
đổi dataset, nhưng mảng phụ thuộc là `[dataset?.id, loadDictionary, dataset]` —
có cả **object `dataset`**. Mỗi lần `refreshWorkspace` dựng lại danh sách, object
đó đổi identity dù dữ liệu y nguyên, effect chạy lại và **đóng panel vừa mở**.

**Sửa:** chỉ phụ thuộc `datasetId`. Việc reset vẫn đúng khi thật sự đổi dataset.

### O4. "Tiến độ duyệt" vỡ format

Markup đã được viết lại thành `.rs-head` / `.rs-track` / `.rs-legend`, nhưng
**CSS cho những lớp đó chưa từng được viết** — `grep` ra 0 kết quả. Trong khi đó
`.review-summary` vẫn giữ `display: flex; align-items: center` của markup cũ, nên
ba khối bị ép thành một hàng ngang và thanh tiến độ không có chiều cao nên vô
hình.

**Sửa:** viết CSS đúng cho markup hiện tại — `display: grid` xếp dọc, thanh
tiến độ cao 7px bo tròn với hai đoạn duyệt/từ chối, chú giải có chấm màu, phần
trăm đẩy sang phải. Xoá luôn `.review-summary strong`, `.review-summary > span`,
`.review-progress` và hai override theme của chúng — tất cả trỏ tới markup không
còn tồn tại.

### O5. Upload lại có chạy thật không?

Kiểm tra ba tầng:

| Tầng | Kết quả |
|---|---|
| `agent_mode` | `graph` — LLM chạy thật, không phải nhánh deterministic fallback |
| `VITE_USE_MOCK_API` | `false` — không dùng dữ liệu giả |
| Khoá idempotency phía client | `ui-${sha256(file)}` — **chỉ phụ thuộc nội dung tệp** |

Tầng thứ ba là vấn đề thật: khoá idempotency lẽ ra định danh **một lần gửi**, chứ
không phải nội dung. Đã đổi thành `ui-${checksum}-${uuid}` — tính một lần mỗi lần
gọi nên retry mạng vẫn dùng lại đúng khoá đó.

Nhưng kiểm chứng cho thấy **vẫn trả về version cũ**. Lý do là tầng thứ tư, và nó
**có chủ đích**: `POST /workspaces/{id}/datasets/import` đánh địa chỉ version
**theo checksum trong phạm vi workspace**, kèm comment giải thích rõ đây là thứ
làm hai lần retry hội tụ về một version bất biến. Đây là thuộc tính kiến trúc
(version bất biến, định danh theo nội dung) và có test bảo vệ — **tôi không phá
nó**.

Điều thực sự sai là giao diện **im lặng**: người dùng tải lên, không thấy gì xảy
ra, và không có cách nào biết vì sao. Response đã mang sẵn cờ `idempotent_replay`
nhưng client bỏ qua hoàn toàn.

**Sửa:** đọc cờ đó và nói thẳng: *"Tệp này trùng khớp hoàn toàn với X đã có —
dùng lại bản profile cũ, không chạy lại."* Tệp khác dù chỉ một byte vẫn tạo
version mới và chạy profiling thật.

### Kiểm chứng

```
run PROPOSE_RULES (truoc khi sua):  409 This workflow step is not ready to run.
run PROPOSE_RULES (sau tang 1):     200, job FAILED "Complete the current workflow step..."
run PROPOSE_RULES (sau tang 2):     200, job FAILED "Confirm the current semantic contract..."  <- dieu kien nghiep vu that
upload cung tep, khoa moi:          tra ve cung version + idempotent_replay = true
```

Backend **341 passed**, vẫn đúng 11 fail có sẵn — nới cổng workflow không phá test
nào.

---

## Phần P — Bước 4 (Graph 2 · Thực thi)

### P1. "Run approved rules" chết — hai lỗi độc lập chồng nhau

**Lỗi A — schema DB lệch model.** Thông báo là
`sqlite3.IntegrityError: datatype mismatch` khi chèn vào `dq_results`. Đọc DDL
thật trong `data/gate2_mvp.db`:

```sql
CREATE TABLE dq_results (
    id INTEGER NOT NULL,      -- model hien tai: String(36), sinh UUID
    ...
    rule_id VARCHAR(64)       -- model hien tai: String(512)
)
```

Bảng được tạo từ một phiên bản model cũ. `Base.metadata.create_all()` chỉ tạo
bảng **thiếu**, không bao giờ sửa bảng đã tồn tại, và trong `rule_store.py` có
sẵn ba helper `_migrate_local_*` cho các bảng khác nhưng **không có** cái nào cho
`dq_results`. Nên mọi lần chạy Graph 2 đều chết khi nhét chuỗi UUID vào cột
INTEGER.

**Sửa:** thêm `_migrate_local_dq_result_ids` — phát hiện `id` còn kiểu INTEGER
thì dựng lại bảng theo model, **giữ nguyên dữ liệu cũ** (id số được ép sang chuỗi
cùng giá trị nên vẫn duy nhất), rồi bỏ bảng tạm. Chỉ chạy trên SQLite; production
vẫn là thao tác phát hành có kiểm soát như các migration khác.

Kết quả: `id INTEGER` → `id VARCHAR(36)`, **176 dòng lịch sử giữ nguyên**.

**Lỗi B — tên kiểu luật không khớp compiler.** Sửa xong lỗi A thì lộ ra
`ValueError: Unsupported rule template: RANGE`. Thống kê `rule_type` trong DB:

```
NOT_NULL 20 | not_null 11 | RANGE 10 | numeric_range 8 | UNIQUE 4
NULL_RATE 4 | CROSS_FIELD_COMPARISON 1 | ROW_COUNT 1 | ...
```

Graph 1B sinh tên chữ HOA (`RANGE`, `NOT_NULL`, `UNIQUE`), còn
`compile_rule_to_sql` chỉ khớp chữ thường và dùng tên khác (`numeric_range`).
Không có gì dịch giữa hai bên, nên **phần lớn luật mà agent hiện tại đề xuất đều
không chạy được**.

**Sửa:** thêm `RULE_TYPE_ALIASES` + `normalize_rule_type()` chuẩn hoá chữ và ánh
xạ bí danh (`RANGE`→`numeric_range`…), áp dụng cả trong compiler lẫn chỗ dựng
tham số. Thêm template cho `UNIQUE` (kiểm tra trùng trên một cột — vừa với dạng
chọn theo dòng).

**Lỗi C — một luật hỏng giết cả run.** Vòng lặp thực thi không có `try/except`
theo từng luật, nên một luật compiler không diễn đạt được sẽ làm hỏng toàn bộ.

**Sửa:** cô lập từng luật. Luật không chạy được ghi thành `SKIPPED` kèm
`error_message`, và run tiếp tục. Kết quả một phần hữu ích hơn nhiều so với một
run thất bại trắng.

`NULL_RATE` và `ROW_COUNT` là luật mức toàn bộ dữ liệu, không hợp với dạng trả về
danh sách dòng lỗi — chúng nay được đánh `SKIPPED` có lý do rõ ràng thay vì làm
sập run. **Đây vẫn là khoảng trống chưa lấp**, xem mục "chưa làm".

**Kiểm chứng** (40 luật đã duyệt của bạn):

```
truoc: run FAILED, 0 ket qua
sau:   run SUCCEEDED, 40 ket qua — 19 PASS / 16 FAIL / 5 SKIPPED
```

### P2. Nhật ký kiểm toán vào biểu tượng trên thanh trên cùng

Audit history trước đó được dán vào **đáy Bước 4**, đẩy chính phần kết quả mà nó
chú giải ra khỏi màn hình.

**Sửa:** thêm nút biểu tượng cùng cỡ với chuông thông báo trên topbar; bấm mở
`AuditPage` trong overlay đã có (`DetailOverlay`) — có `✕`, đóng bằng `Esc`, khoá
cuộn nền. Xoá khối audit toàn trang ở Bước 4.

### P3. Panel Graph 2 hiện 0/5 "Chưa chạy" cạnh kết quả đã có

Truy DB: `graph_node_runs` chỉ có `G1A` (45) và `G1B` (3) — **không một node G2
nào**. Graph 2 *có* được instrument đầy đủ ở `src/agents/graph.py:328-338`, nên
đây không phải thiếu telemetry.

Nguyên nhân thật: **có hai đường thực thi khác nhau.**

| Đường | Gọi từ | Telemetry |
|---|---|---|
| `POST /dq-runs` → `run_dq_checks` (SQL có giới hạn) | Nút "Run approved rules" ở Bước 4 | không |
| `build_execution_graph()` (đồ thị dbt) | `analysis_workflow` (Graph 2 + Graph 3) | có |

Nút ở Bước 4 đi đường thứ nhất, nên các node của đường thứ hai đúng là **chưa
chạy** — nhãn không sai, chỉ là không ai giải thích.

**Sửa (có giới hạn):** `GraphStagePanel` nhận thêm `emptyNote`, và panel G2 nói
thẳng: *"Chạy luật đã duyệt thực thi trực tiếp bằng SQL có giới hạn, không đi qua
đồ thị dbt bên dưới…"*

**Tôi cố ý không** đổi nút Bước 4 sang chạy đồ thị Graph 2. Đường đó cần một
`Graph1RunModel` thuộc lineage khác hẳn, và đổi sẽ thay đổi ý nghĩa của chính
hành động "chạy luật đã duyệt". Đó là quyết định kiến trúc, cần bạn xác nhận —
xem mục "chưa làm".

---

## Phần Q — Làm nốt hai việc còn treo ở phần P

### Q1. Node Graph 2 sáng thật

**Cách tôi không chọn:** ép nút Bước 4 chạy đồ thị dbt. Đường đó cần lineage
`Graph1Run` và sẽ đổi ý nghĩa của chính hành động "chạy luật đã duyệt".

**Cách tôi không chọn (2):** gắn telemetry của bộ chạy SQL vào các node `G2`.
Node `validate_dbt_project` mang mô tả "quét dự án dbt sinh ra" — dán nhãn đó lên
một bước kiểm tra SQL là **nói sai**.

**Cách đã làm:** thêm graph **`G2_DIRECT`** vào catalog, mô tả đúng bốn chặng mà
bộ chạy SQL thật sự thực hiện:

| Node | Việc thật |
|---|---|
| `compile_rules` | Chuẩn hoá kiểu luật, biên dịch thành SELECT có giới hạn trên cột được cho phép |
| `validate_sql` | Từ chối mọi thứ không phải một câu SELECT duy nhất |
| `execute_checks` | Chạy từng kiểm tra, timeout 5 giây |
| `persist_results` | Lưu kết quả kèm id dòng lỗi có giới hạn, đóng run |

Hạ tầng telemetry chỉ có `instrument()` cho node langgraph, nên tôi thêm
`record_stage()` trong `node_telemetry.py` — context manager ghi một chặng của
đường chạy Python thuần, dùng lại đúng `_record_start`/`_record_end` sẵn có.

Bước 4 nay hiển thị `["G2_DIRECT", "G2"]`: đồ thị thật sự chạy đứng trước, đồ thị
dbt của luồng phân tích đứng sau. Không còn khối chú thích "vì sao 0/5" nữa vì
không còn gì để biện minh.

**Hai test tôi làm hỏng và đã sửa.** Thêm `G2_DIRECT` phá bất biến *"mọi graph
trong catalog đều có builder langgraph"* — `test_catalog_matches_the_compiled_builders`
và `test_returns_every_graph_and_its_nodes` fail. Đúng là lỗi của tôi, không phải
test sai. Cách sửa: đánh dấu `"langgraph": False` trên `G2_DIRECT` và **thu hẹp
bất biến về đúng phạm vi** — chỉ graph nào khai báo builder langgraph mới bị đối
chiếu. Bảo vệ chống trôi lệch vẫn còn nguyên ở nơi nó có ý nghĩa.

### Q2. Luật `NULL_RATE` và `ROW_COUNT` chạy được

Hai loại này lấy **toàn bộ dữ liệu** làm chủ ngữ, không thể diễn đạt thành câu
SELECT trả về id dòng lỗi — đó là lý do compiler từ chối chúng.

**Sửa:** thêm `AGGREGATE_RULE_TYPES` + `evaluate_aggregate_rule()`, chạy ở nhánh
riêng trước nhánh biên dịch SQL.

Một điều tôi **không** làm: bịa ngưỡng. Spec mà agent đang sinh ra không có
ngưỡng nào cả (`{"type":"NULL_RATE","column":"passenger_count"}`). Nếu tôi tự đặt
"quá 5% là FAIL" thì đó là chính sách không ai quyết. Nên:

- Không có ngưỡng → **đo và báo cáo**, trạng thái PASS, tỷ lệ đo được ghi vào
  `violation_rate`.
- Có ngưỡng (`max_null_rate`, `min_row_count`, `max_row_count` — vừa thêm vào
  `RuleSpecSchema`) → thực thi đúng ngưỡng đó.
- `ROW_COUNT` không ngưỡng → FAIL khi bộ dữ liệu rỗng, vì đó là cách đọc duy nhất
  đúng với mọi diễn giải.

Kèm theo: `violation_rate` và `error_message` **đã được lưu từ trước nhưng không
có trong schema trả về**, nên giao diện không có gì để hiện. Đã bổ sung vào
`DqResultSchema`, vào type `DqResult` phía FE (kèm trạng thái `SKIPPED` vốn cũng
thiếu), và hiện tỷ lệ đo được ngay cạnh số dòng lỗi; luật bị bỏ qua hiện lý do
thay vì để trống — trống sẽ bị đọc nhầm thành đã đạt.

### Kiểm chứng

```
run:                 SUCCEEDED
ket qua:             40 — 24 PASS / 16 FAIL / 0 SKIPPED      (truoc: 5 SKIPPED)
luat tong hop:       ROW_COUNT  PASS  rate 0.0
                     NULL_RATE  PASS  rate 15.344   x4
node G2_DIRECT:      1 compile_rules   SUCCEEDED
                     2 validate_sql    SUCCEEDED
                     3 execute_checks  SUCCEEDED
                     4 persist_results SUCCEEDED
```

Backend **341 passed**, đúng 11 fail có sẵn — hai test tôi làm hỏng đã về xanh.

---

## Phần R — Bước 5 (Graph 3 · Bất thường)

### R1. Biểu đồ cũ dán sai nhãn

`Graph3Analytics` hiện đúng hai con số và một biểu đồ tròn tiêu đề **"Phân bố mức
độ bất thường"** — nhưng dữ liệu nó vẽ là `anomaly_type`, tức **loại** tín hiệu,
không phải mức độ. Dữ liệu thật chỉ có **một** loại (`HIGH_VIOLATION_RATE`), nên
biểu đồ tròn thành một hình tròn đỏ đặc không mang thông tin gì — đúng như ảnh
bạn gửi.

### R2. Dữ liệu vốn đã giàu, chỉ là không ai hiện ra

Payload `DqAnomaly` mà giao diện **đang nhận sẵn** có: `current_rate`,
`historical_mean`, `z_score`, `history_size`, `detection_mode`, `checked_count`,
`failed_count`, `reason`, cùng `rule_id` mã hoá cả tên cột và loại luật. Giao
diện dùng đúng hai trường: độ dài mảng và `anomaly_type`.

**Sửa:** thay bằng `components/wizard/AnomalyStatisticsPanel.tsx`. Mọi con số đều
lấy từ payload, không có ngưỡng nào do màn hình tự nghĩ ra:

| Khối | Nội dung |
|---|---|
| 6 ô chỉ số | Số bất thường (kèm số luật bình thường) · tỷ lệ vi phạm cao nhất · z-score cao nhất · tổng dòng bị ảnh hưởng · số cột liên quan · chế độ phát hiện |
| Xếp hạng | Thanh ngang từng luật theo `current_rate`, giảm dần — hình ảnh chính của trang |
| Phân bố | Theo **loại tín hiệu** (nhãn đúng) và theo **loại luật**, suy từ `rule_id` |
| Bảng chi tiết | Luật · loại · hiện tại vs baseline · z-score · lịch sử · dòng lỗi · lý do |

Một điểm về sự trung thực của số liệu: khi mọi tín hiệu đều ở chế độ
`COLD_START`, panel nói rõ rằng chúng đang so với **ngưỡng tĩnh 5%**, không phải
baseline của chính bộ dữ liệu — vì cột "Baseline" khi đó trống, và người đọc cần
biết vì sao thay vì tưởng hệ thống thiếu dữ liệu. Ô "lịch sử" hiện chip
`cold start` thay vì số 0 trơ trọi.

Chỉ ô "Số bất thường" mang màu cảnh báo; các ô còn lại là **phép đo**, nên để tông
trung tính. Màu đỏ dành cho thứ thật sự cần chú ý.

### R3. Node Graph 3 hiện 0/4

Khác với Graph 2 ở phần Q: `build_anomaly_graph` **đã** được instrument đầy đủ
(`src/agents/graph.py:396-399`). Các node im lặng vì luồng của bạn chưa gọi tới
chúng — số liệu bất thường bạn đang xem do bộ phát hiện chạy kèm nút "Chạy luật
đã duyệt" sinh ra, không phải Graph 3.

**Sửa:** dùng lại `emptyNote` đã thêm ở phần P để nói đúng điều đó, thay vì để
bốn thẻ "chưa chạy" đứng cạnh số liệu rõ ràng đang có mà không giải thích.

Tôi **không** dựng thêm một graph `G3_DIRECT`. Ở phần Q việc đó xứng đáng vì bộ
chạy SQL có bốn chặng riêng biệt đáng theo dõi; phát hiện bất thường ở đây là một
bước đơn trong `run_dq_checks`, chia nhỏ thành đồ thị sẽ là bịa cấu trúc không tồn
tại.

### Kiểm chứng — số liệu panel dựng từ run thật

```
bat thuong: 6 | luat da kiem: 40 | binh thuong: 34
ty le cao nhat: 100.00%   z-score cao nhat: 2.4
dong bi anh huong: 162,350
cot lien quan: dropoff_location_id, fare_amount, pickup_location_id, total_amount, vendor_id
theo loai luat: UNIQUE 3, RANGE 3
che do: COLD_START 6/6
xep hang: 100.00% / 99.98% / 99.97% / 9.12% / 9.12% / 6.51%
```

Thay cho "6 · 1 · một hình tròn đỏ" trước đó.

Đã xoá `Graph3Analytics` (56 dòng) khỏi `WizardAnalytics.tsx` — nó không còn được
import ở đâu.

---

## Phần S — Kiểm chứng hệ thống có chạy thật không

Nghi ngờ hợp lý: thời gian chạy quá nhanh. Tôi kiểm bằng chứng thay vì suy đoán.

### S1. Dữ liệu là thật

```
source_rows: 50.000 dong cho dataset-nyc-yellow-taxi-50k
gate2_mvp.db: 34,5 MB
mau: row-00001 | Curb Mobility, LLC | 0.97 | 8.6 | 13.32
```

### S2. Kết quả kiểm tra là tính thật, không phải số dựng sẵn

Phép thử quyết định: **tự viết truy vấn đếm độc lập** rồi đối chiếu với con số hệ
thống ghi. Tám luật đầu tiên:

```
KHOP  he thong=  136  toi dem=  136   RANGE     airport_fee
KHOP  he thong=    0  toi dem=    0   NOT_NULL  cbd_congestion_fee
KHOP  he thong=  104  toi dem=  104   RANGE     cbd_congestion_fee
KHOP  he thong=  745  toi dem=  745   RANGE     congestion_surcharge
KHOP  he thong=    0  toi dem=    0   NOT_NULL  dropoff_at
...
```

Khớp tuyệt đối. Nếu kết quả là số dựng sẵn thì không thể trùng với phép đếm mà
tôi tự viết ra.

### S3. Vì sao Graph 2 nhanh — và vì sao đó là hợp lý

Tôi đo trực tiếp SQLite, không qua ứng dụng:

```
40 truy van quet 50.000 dong: 0,60 s   (15 ms / truy van)
1 lan dem toan bang:             3 ms
```

Hệ thống ghi `execute_checks = 1.355 ms`, tức **chậm hơn hơn gấp đôi** so với đo
trần — phần chênh là ORM và commit từng luật. Nói cách khác, con số của hệ thống
không những không bị rút ngắn, nó còn cõng thêm chi phí thật.

Bốn mươi câu `WHERE` đơn giản trên 50 nghìn dòng vốn dĩ chỉ tốn từng ấy. Nhanh ở
đây là SQLite nhanh, không phải hệ thống bỏ qua việc.

### S4. LLM có thật sự được gọi

```
provider: openai      model: gpt-4o-mini      API key: co (164 ky tu)
cache LLM: khong co   agent_mode: graph
```

Không có lớp cache nào, nên mỗi lần chạy là một lượt gọi mạng mới. Đo bằng đồng
hồ thật một lượt `UNDERSTAND_DATA`:

```
tong: 26,2 s
  1 build_profile_digest       DETERMINISTIC      11 ms
  2 data_dictionary_generator  LLM            13.345 ms
  3 dataset_understanding      LLM            11.904 ms
```

Mười ba giây cho một node là độ trễ mạng thật; không có cách nào giả ra con số
đó. Thêm bằng chứng ở phần G: bộ dữ liệu IMDb sinh ra hợp đồng ngữ nghĩa với đúng
**27 cột riêng của nó** (`id`, `url`, `primaryTitle`…), không phải cột taxi.

### S5. Lỗi thật tìm được: không có bằng chứng nào hiển thị cho người dùng

Đây mới là điều đáng sửa. `model_name` **rỗng ở mọi node LLM**, vì
`_model_name_from()` chỉ đọc trường `model_name` trong payload mà node trả về —
và không node LLM nào đặt trường đó. Kết quả: giao diện không có gì chứng minh có
mô hình tham gia, nên người dùng nhìn vào chỉ thấy một con số thời lượng trơ trọi
và hoàn toàn có lý khi nghi ngờ.

**Sửa:** thêm `configured_model_name()` và ghi nó vào node LLM khi node không tự
báo. Đây là **model đã cấu hình**, không phải khẳng định về một lượt trả lời cụ
thể — comment trong code nói rõ điều đó. Thẻ node và trang chi tiết node vốn đã
đọc `run.model_name`, nên nó hiện ra ngay: `model=gpt-4o-mini`.

### Kết luận

Hệ thống chạy thật. Nhanh ở Graph 2 là do bản chất công việc, đã chứng minh bằng
đo độc lập; chậm ở Graph 1A (10–16 giây mỗi node LLM) là độ trễ mạng thật. Vấn đề
duy nhất là **thiếu bằng chứng hiển thị**, nay đã sửa.

---

## Phần T — Sửa dứt điểm 11 test fail

Suốt các vòng trước tôi chỉ xác nhận 11 test này **có sẵn từ HEAD** rồi để đó.
Đọc kỹ thì hai trong số đó che giấu **lỗi sản phẩm thật**, không phải test cũ.

### T1. Lỗi sản phẩm: một merge hỏng đã xoá mất thân hàm

`generate_dashboard_proposals` ở HEAD:

```python
def generate_dashboard_proposals(db, dataset_id):   # <- mat tham so semantic_contract
    ...
    try:
        ...
        if semantic_contract is not None:            # <- NameError: khong phai tham so
            raw_rules = _invoke_dashboard_proposal_graph(...)
    except Exception as exc:
        return _mock_proposals(evidence)             # <- moi lan goi deu roi vao day

    raise AgentWorkflowError(...)                    # <- raw_rules tinh xong roi vut di
```

So với commit `69cb42d` thì thấy rõ: chữ ký từng có `semantic_contract`, và sau
khối `try` từng có `_normalise_graph_rules` → `_complete_with_policy_candidates`
→ `return proposals`. Toàn bộ phần đó biến mất trong lần merge tạo ra `a2ee821`.

Hậu quả trong sản phẩm, không chỉ trong test:

1. **Mọi lượt gọi đều ném `NameError`**, bị `except Exception` nuốt, rồi trả
   `_mock_proposals` — luật do agent sinh **không bao giờ tới được người dùng**.
   Đây chính là thứ khiến bạn nghi ngờ hệ thống chạy giả.
2. `rule_proposer_workflow.py:686` gọi hàm này với **ba** tham số, trong khi chữ
   ký chỉ nhận hai — nhánh fallback đó chắc chắn ném `TypeError`, tức fallback
   của Graph 1B thực chất không tồn tại.

**Sửa:** khôi phục chữ ký và phần thân đã mất.

### T2. Lỗi sản phẩm: guardrail độ tin cậy bị đổi thành âm thầm ghi đè

`RuleConfidence._validate_overall` ở HEAD **tự sửa** `overall` thành trung bình
các thành phần khi lệch quá 0.25. Bản `69cb42d` **ném lỗi**.

Ghi đè âm thầm nghĩa là: mô hình khai "95% tin cậy" trong khi các thành phần của
chính nó trung bình 0.2, hệ thống lặng lẽ đổi số rồi hiển thị một con số **mô hình
chưa từng đưa ra**. Đúng loại bịa đặt mà dự án này canh chừng ở mọi chỗ khác.

**Sửa:** khôi phục việc ném lỗi.

### T3. Bốn test dùng stub đã lỗi thời

`test_rule_proposer_workflow.py` giả lập `generate_dashboard_proposals` trả một
đề xuất, rồi khẳng định `.one()`. Nhưng Graph 1B đã được đưa lên làm **đường
chính**, hàm kia chỉ còn là fallback — nên đường chính chạy thật và sinh 8 luật.
Test chưa được cập nhật khi Graph 1B ra đời.

**Sửa:** giả lập cả đường chính, giữ nguyên stub fallback.

### T4. Một fixture không giống candidate thật

`test_propose_for_table_deepagent_fallback_to_structured_llm` dựng tay một
candidate không có `evidence_items`, nên bị guard *"Candidate has no evidence
reference"* chặn. Candidate thật luôn đi qua `_attach_evidence_items` nên luôn có
trường đó — guard đúng, fixture sai.

**Sửa:** cho fixture mang `evidence_items` đúng dạng thật. Guard giữ nguyên: nó
là thứ bảo đảm không luật nào được đề xuất mà không truy vết được về bằng chứng.

### Kết quả

```
truoc:  11 failed, 341 passed
sau:     0 failed, 352 passed, 10 skipped
```

`tsc` sạch, `vite build` thành công, và DQ run thật vẫn chạy đúng
(`SUCCEEDED · 40 ket qua · 24 PASS / 16 FAIL`) sau khi khôi phục code.

**Điều đáng rút kinh nghiệm:** tôi đã coi 11 fail này là "có sẵn nên không phải
việc của mình" qua nhiều vòng. Hai trong số đó là lỗi sản phẩm nghiêm trọng —
trong đó có đúng cái làm luật của agent không bao giờ tới được người dùng. Test
đỏ là tín hiệu, không phải nhiễu nền.

---

## Phần U — Kiểm tra toàn bộ nút trên giao diện

Phiên này không có công cụ điều khiển trình duyệt, nhưng một nút hỏng là vì lời
gọi API sau nó hỏng. Tôi liệt kê **49 phương thức** trong `api/client.ts` rồi
viết một bài kiểm tra gọi từng cái, đặt tên theo đúng nút người dùng bấm.

**Kết quả: 44 nút · 38 chạy được · 6 hỏng.** Trong 6 nút hỏng, **2 là lỗi sản
phẩm thật**, 4 còn lại do chính kịch bản kiểm tra gây ra (tôi tạm dừng một luật
rồi lại yêu cầu chạy nó, và xoá một đề xuất vừa được duyệt — cả hai đều bị chặn
đúng).

### U1. "Profile dataset" chết ở dataset thứ hai trở đi

```
PendingRollbackError ... Original exception was:
(sqlite3.IntegrityError) UNIQUE constraint failed: source_rows.source_row_id
```

`SourceRowModel` khai `source_row_id` là **khoá chính một mình**. Nhưng mọi bộ dữ
liệu đều đánh số dòng lại từ `row-00001`, nên **bộ dữ liệu thứ hai bất kỳ đi qua
đường nạp này đều đụng khoá** và cả phiên DB rơi vào trạng thái không dùng được.
Khoá tự nhiên phải là cặp `(source_row_id, dataset_id)`.

**Sửa:** đổi sang khoá kép, kèm `_migrate_local_source_row_key` dựng lại bảng cho
DB đã tồn tại (giống cách đã làm với `dq_results`).

**Tôi làm hỏng migration ở lần chạy đầu và phải cứu dữ liệu.** SQLite giữ nguyên
tên index khi `ALTER TABLE ... RENAME`, nên lệnh tạo index của bảng mới đụng tên
index cũ, transaction hỏng dở dang: bảng mới rỗng, **50.000 dòng nằm lại ở bảng
tạm**. Tôi phát hiện ngay khi kiểm chứng (`so dong giu lai: 0`), copy dữ liệu
sang, dựng lại index và xoá bảng tạm — **không mất dòng nào**. Sau đó vá cả hai
migration để xoá index cũ trước khi tạo bảng mới, và ghi rõ cạm bẫy này trong
comment.

### U2. Một luật tạm dừng làm hỏng cả lượt chạy

`POST /dq-runs` trả `422 ACTIVE_RULES_REQUIRED` nếu **bất kỳ** luật nào trong
danh sách đang `PAUSED`. Giao diện thì gửi **toàn bộ** luật đã duyệt. Nên chỉ cần
bạn tạm dừng một luật ở Execution settings là nút "Run approved rules" hỏng hoàn
toàn — mà thông báo lỗi không nói luật nào.

Guard phía backend là đúng: gọi tên một luật đang tạm dừng rồi đòi chạy thì nên
bị từ chối. Cái sai là **giao diện đi hỏi chạy thứ nó đã biết là đang tạm dừng**.

**Sửa:** `runApprovedRules` lọc bỏ luật `PAUSED` trước khi gửi, báo *"Bỏ qua N
luật đang tạm dừng"*, và nếu tất cả đều tạm dừng thì nói thẳng phải bật lại ít
nhất một luật thay vì để backend trả 422 khó hiểu.

Kiểm chứng với 41 luật đã duyệt, tạm dừng 1:

```
gui TAT CA (hanh vi cu):  422
loc bo luat tam dung:     202
```

### Bốn nút còn lại — kịch bản của tôi, không phải sản phẩm

| Nút | Vì sao báo hỏng |
|---|---|
| B3 · Sinh Rule | 409 vì kịch bản xếp hàng `UNDERSTAND_DATA` rồi gọi `PROPOSE_RULES` ngay khi job trước còn chạy. Giao diện vô hiệu hoá nút khi có job nên không gặp |
| B3 · Xoá đề xuất | 422 `APPROVED_PROPOSAL_DELETE_FORBIDDEN` — đúng: kịch bản vừa duyệt hàng loạt rồi đòi xoá |
| B4 · Kết quả kiểm tra | 404 dây chuyền từ U2 |
| B5 · Bất thường | 404 dây chuyền từ U2 |

### Dọn dẹp

Bài kiểm tra có tạo tài khoản, luật thủ công và hai dataset nháp. Đã xoá hết; dữ
liệu về đúng trạng thái ban đầu: **4 dataset · 5 tài khoản · 41 đề xuất · 50.000
dòng**. Một cấu hình `PAUSED` có sẵn từ trước của bạn được **giữ nguyên**, không
đụng vào.

Backend **352 passed**, `tsc` sạch, `vite build` thành công.

---

## Phần V — Sửa lỗi của chính tôi, và điều tra nốt thứ tôi đã bỏ qua

### V1. Migration tôi vá nhưng chưa bao giờ chứng minh

Ở phần U tôi vá hai migration sau khi làm hỏng một lần. Nhưng lần chạy sau khi vá
chỉ là **no-op**, vì tôi đã sửa tay DB trước đó — nghĩa là bản vá **chưa từng
được chạy ở đúng trường hợp nó sinh ra để xử lý**. Đó là lỗi của tôi: vá xong rồi
tuyên bố đã sửa mà không kiểm chứng.

**Đã kiểm chứng:** dựng một DB đúng schema cũ (khoá đơn + index trùng tên, khoá
`INTEGER`) rồi chạy migration thật:

```
TRUOC: source_rows pk = ['source_row_id']        dq_results pk = INTEGER
SAU  : source_rows pk = ['source_row_id','dataset_id']  dq_results pk = VARCHAR(36)
so dong giu lai: 500 / 20      gia tri con nguyen: 10.5
bang tam con lai: []           index: ix_source_rows_dataset_id con nguyen
dataset thu 2 dung row-00001:  CHEN DUOC
chay lai lan 2:                OK (idempotent)
```

Thêm `tests/unit/test_local_schema_migrations.py` (6 test) để không phải tin vào
lời hứa nữa: khoá đổi đúng, không mất dòng, không sót bảng tạm, index sống sót,
hai dataset dùng chung `row-00001` được, và chạy lại là no-op.

### V2. Thứ tôi nhìn thấy rồi lướt qua: hệ thống bịa dữ liệu taxi

Trong traceback ở phần U có một chi tiết tôi ghi nhận rồi bỏ đi: file tải lên chỉ
có `id,name`, nhưng câu INSERT lại chứa `Curb Mobility, LLC`, `pickup_at`,
`fare_amount`. Lẽ ra tôi phải dừng lại ngay lúc đó.

Truy ra: `run_ingest_profile` thử ba nguồn theo thứ tự — file trong `upload_dir`,
Supabase, rồi **file parquet taxi mẫu**. Import versioned **không ghi file vào
`upload_dir`** (chỉ đường legacy ghi), nên mọi dataset versioned rơi thẳng xuống
nhánh cuối và bị nạp **50.000 dòng taxi dưới tên của chính nó**.

Hậu quả: bấm "Profile dataset" trên một bộ dữ liệu bạn vừa tải lên sẽ ghi đè nó
bằng dữ liệu taxi, rồi hồ sơ, luật và kết quả kiểm tra đều đo trên dữ liệu **bạn
chưa từng tải lên**. Đây là lỗi nặng nhất tìm được hôm nay.

**Sửa:** nhánh fixture chỉ còn áp dụng cho đúng dataset demo. Với dataset khác:
nếu đã có profile snapshot versioned thì dùng lại và đánh dấu `PROFILE_READY`;
nếu không có gì thì **báo lỗi rõ ràng**, tuyệt đối không thay bằng fixture.

**Kiểm chứng** — tải một CSV chỉ có `id,name` rồi bấm Profile:

```
job: SUCCEEDED  "Versioned profile is already current"
so dong taxi bi chen: 0
ho so: 3 dong | 2 cot | ['id', 'name']
```

Trước bản vá, chỗ này sẽ là 50.000 dòng taxi và một hồ sơ 21 cột taxi.

Đã soát dữ liệu hiện tại của bạn: không bộ nào bị nhiễm — chỉ `NYC Yellow Taxi
50k Sample` có 50.000 dòng, ba bộ còn lại đều 0 dòng trong `source_rows` đúng như
bản chất versioned của chúng.

Backend **358 passed**.

---

## Phần W — Quét lại toàn bộ và một crash 500 còn sót

### W1. Chạy lại bài kiểm tra nút: 44/44

Sau các bản vá ở U và V, chạy lại bài kiểm tra 44 nút:

```
TONG: 44 nut  |  OK: 44  |  FAIL: 0
```

Bốn ca hỏng trước đây là do kịch bản tự đánh nhau với guard đúng, đã sửa kịch bản
cho giống hành vi thật của giao diện: tạm dừng luật thì không gửi luật đó đi chạy,
và từ chối đề xuất trước khi xoá.

### W2. "0 bất thường" — kiểm tra rồi kết luận **không phải lỗi**

Bài kiểm tra báo `B5 · Bất thường: 0`, trong khi trước đó là 6. Tôi nghi mình vừa
làm hỏng và truy đến cùng:

```
tong signal: 41        diem >= 0.70: 0
detector: ROBUST_MAD 39 | COLD_START_STATIC 1 | VOLUME_DRIFT 1
du lich su: True 39, False 2          diem cao nhat: 0.40
quyet dinh: NORMAL | severity: LOW
```

39/41 tín hiệu nay có `sufficient_history=True`, tức detector đã **chuyển từ
ngưỡng tĩnh cold-start sang so sánh với lịch sử của chính bộ dữ liệu**. Vì tỷ lệ
vi phạm lặp lại y hệt qua nhiều lượt chạy nên không có độ lệch nào — kết luận
`NORMAL` là đúng. Con số 6 trước kia là hành vi cold-start khi chưa có lịch sử.

Kết quả kiểm tra giữa các lượt cũng giống hệt (16 FAIL / 24 PASS), xác nhận phần
thực thi không đổi. **Không sửa gì** — sửa ở đây sẽ là phá một thứ đang đúng.

### W3. Crash 500 khi sắp xếp Data Explorer theo cột không phải taxi

Cùng họ với lỗi V2. `GET /datasets/{id}/rows` nhận `sort_by` là chuỗi tự do
(`min_length=0`), nhưng nhánh legacy tra cứu **không có bảo vệ**:

```python
sort_columns = {"pickup_at": ..., "trip_distance": ..., "fare_amount": ..., "total_amount": ...}
sort_column = sort_columns[sort_by]     # KeyError -> 500
```

Hai nhánh kia (versioned, đọc file) đều có fallback khi cột không hợp lệ; riêng
nhánh này không. Thêm nữa, mặc định của tham số là `"pickup_at"` — một cột taxi
đặt làm mặc định cho endpoint dùng chung.

Tái hiện:

```
sort_by='pickup_at'        -> 200
sort_by='passenger_count'  -> CRASH KeyError
sort_by='vendor_id'        -> CRASH KeyError
sort_by=''                 -> CRASH KeyError      (ma min_length=0 lai cho phep!)
```

Data Explorer hiển thị đúng những cột bộ dữ liệu có, nên người dùng bấm sắp xếp
theo bất kỳ cột nào ngoài bốn cột trên là gặp 500.

**Sửa:** dùng `.get(sort_by, SourceRowModel.source_row_id)` — `source_row_id` có ở
mọi bộ dữ liệu nên luôn sắp xếp được; và bỏ mặc định `"pickup_at"`, để rỗng cho
mỗi nhánh tự quyết theo bộ dữ liệu của nó.

Sau khi sửa, thử 5 giá trị `sort_by` trên cả 4 bộ dữ liệu: **20/20 trả 200**.

Thêm `tests/unit/test_dataset_rows_sorting.py` (10 test). Hai test đầu tôi viết
kém — một cái introspect mong manh, một cái có `or True` nên không khẳng định gì
— đã viết lại bằng `inspect.signature` cho thẳng thắn.

Backend **368 passed**.

---

## Phần X — CẢNH BÁO: khoá API OpenAI đã bị vô hiệu

Phát hiện trong lúc đo Graph 1B:

```
401 - Your API key has been invalidated   (code: token_invalidated)
GET https://api.openai.com/v1/models -> 401 "Incorrect API key provided: sk-proj-***"
```

**Mọi bước dùng LLM đều sẽ hỏng cho tới khi thay khoá mới**: Graph 1A (hiểu ngữ
nghĩa), Graph 1B (sinh luật), Graph 3 (giả thuyết nguyên nhân). Các phần tất định
— import, profile, chạy luật, thống kê — không bị ảnh hưởng.

Tôi không biết chắc vì sao khoá bị thu hồi. Cần nói thẳng: các phép đo của tôi ở
phần này tạo ra **rất nhiều lời gọi và 38 lượt bị 429**, và điều đó có thể đã góp
phần. Cũng có thể khoá được xoay vòng hoặc hết hạn vì lý do khác. Sau khi phát
hiện tôi **dừng toàn bộ phép đo có gọi LLM**.

## Phần Y — Ba việc ở Graph 1B / 2 / 3

### Y1. Vì sao "Generate rule proposals" đứng ở 67% rất lâu

67% là đúng: 2/3 node xong, `rule_proposer` còn chạy. Trong DB, **cả bốn** lần
chạy `rule_proposer` đều kẹt ở `RUNNING` với `0 ms` — không lần nào kết thúc.

Đo một lượt thật (khi khoá còn sống):

```
75 loi goi OpenAI | 32 lan bi 429 (43%) | 2 lo | van chua xong sau 10 phut
```

Nguyên nhân gốc tìm được trong log chế độ `legacy`:

```
LLM must return one narrative per server candidate: expected 20, received 5
LLM must return one narrative per server candidate: expected 10, received 9
-> 0 rules | 2 errors
```

Node đòi **đúng một narrative cho mỗi candidate, được ăn cả ngã về không**.
`gpt-4o-mini` trả thiếu, nên **cả lô bị vứt kể cả phần nó làm đúng** — rồi vòng
DeepAgent thử lại toàn bộ lô, và đó là chỗ sinh ra hàng chục lời gọi cùng cơn bão
429.

**Sửa:** giữ lại phần đã phủ. Phép kiểm tra số lượng ở đầu chuyển thành cảnh báo;
candidate nào không có narrative thì **bỏ qua** thay vì ném lỗi, và chỉ báo lỗi
khi **không** candidate nào được phủ. Tính an toàn giữ nguyên: mỗi luật trả về
vẫn gắn với một candidate thật kèm bằng chứng của nó — chỉ là đề xuất ít hơn thay
vì không có gì.

**Hai hướng tôi đã thử và loại bỏ**, ghi lại để không ai thử lại:

| Thử | Kết quả |
|---|---|
| `exit_behavior="end"` cho `ToolCallLimitMiddleware` | Nhanh hơn hẳn (33s / 5 lời gọi / 0 lần 429) nhưng **hỏng**: agent dừng ngay khi hết ngân sách tool, trước khi kịp trả kết quả có cấu trúc. Thử cả ngân sách 6 và 15, đều "could not produce a valid structured response". Đã hoàn nguyên — đổi tính đúng đắn lấy tốc độ là sai |
| `rule_proposer_mode=legacy` | Cũng hỏng, và chính nó để lộ nguyên nhân gốc ở trên |

Chưa xác minh được bản vá cuối trên lượt chạy thật vì khoá API chết giữa chừng.
38 test của `rule_proposer` vẫn xanh.

### Y2. Bước 4 hiện kết quả khi chưa bấm nút

`refreshWorkspace` nạp sẵn lượt chạy gần nhất và kết quả của nó, nên vào trang là
thấy một lượt chạy đã xong — đọc ra như thể nút đã được bấm rồi.

**Sửa:** kết quả chỉ hiện sau khi bấm "Chạy luật đã duyệt". Dữ liệu vẫn được nạp
sẵn, nên có thêm nút **"Xem lượt chạy trước"** để mở lại có chủ đích — giấu hẳn
lịch sử thì mất thông tin. Khi có job chạy trong lúc đang mở trang thì tự hiện,
vì đó là việc do chính người dùng bắt đầu.

### Y3. Báo cáo Steward hiện Markdown thô

`react-markdown` và `remark-gfm` **đã nằm sẵn trong dependencies** nhưng không ai
dùng: panel đổ thẳng nội dung vào `<pre>`, nên Steward đọc dấu `|` và `#` thay vì
bảng.

**Sửa:** render bằng `ReactMarkdown` + `remark-gfm` (cần cho bảng), kèm bộ chữ cho
tài liệu: tiêu đề có phân cấp, bảng có viền và header, `code` inline có nền nhạt,
và khối trích dẫn đầu báo cáo — vốn là lời cảnh báo "tạo tự động theo template" —
được vẽ thành hộp cảnh báo màu hổ phách đúng vai trò của nó.

Backend **368 passed**, `tsc` sạch, `vite build` thành công.

---

## Phần Z — Graph 1B chạy được: 26,8 giây, 14 luật

Khoá API mới đã hoạt động (`GET /v1/models -> 200`), nên xác minh được bản vá ở
phần Y và tìm nốt rào chặn thứ hai.

### Z1. Bản vá "giữ phần đã phủ" — đã xác minh trên lượt chạy thật

```
[source_rows] Model covered 5 of 20 candidates; proposing the covered ones.
[source_rows] 15 candidate(s) had no narrative and were left unproposed: ...
[source_rows] Model covered 9 of 10 candidates; proposing the covered ones.
rule_proposer_node hoan thanh: 14 rules | 0 errors        <- truoc: 0 rules | 2 errors
```

Đúng như thiết kế: phần model làm được được giữ lại, phần thiếu bị bỏ qua có ghi
log, và node hoàn thành sạch thay vì ném lỗi.

### Z2. Rào chặn thứ hai: trần hiển thị 5 luật chặn cả bộ 14 luật

Node đã trả 14 luật nhưng khâu sau vẫn hỏng:

```
AgentWorkflowError: Graph 1B could not form a valid dashboard rule set.
```

Nguồn: `if not 2 <= len(proposals) <= 5: raise`.

Trần **5** là ngân sách bố cục của **dashboard** — năm ô hiển thị. Nhưng đường này
nuôi hàng đợi duyệt ở Bước 3, nơi thường xuyên có hàng chục luật (queue của bạn
đang có 41). Áp trần đó ở đây khiến một bộ luật đầy đủ, có bằng chứng bị vứt sạch
**vì quá nhiều**.

**Sửa:** chỉ giữ cận dưới (`< 2` thì báo lỗi kèm số lượng thực tế), bỏ cận trên
cho đường workflow. Hàm dashboard giữ nguyên cửa sổ 2..5 của nó.

### Kết quả

```
truoc: 0 de xuat, 2 loi        (legacy)  /  khong bao gio xong (deepagent)
sau:   14 de xuat, 0 loi trong 26,8 giay (legacy)
   - numeric_range          | trip_distance must be non-negative
   - numeric_range          | fare_amount must be non-negative
   - cross_field_comparison | pickup_at must not follow dropoff_at
   - not_null               | vendor_id must be populated
   ...
```

### Z3. Chế độ DeepAgent vẫn rất đắt — và đó là lựa chọn của bạn

Đo lại với khoá mới: **199 lời gọi OpenAI, 80 lần 429 (40%), chưa xong sau 12
phút**. Tôi dừng phép đo để không đốt thêm khoá vừa thay.

Chi phí này thuộc về kiến trúc DeepAgent (vòng ReAct nhiều lượt gọi), không phải
một lỗi tôi có thể vá. Hai bản vá trên **vẫn có ích cho cả hai chế độ**, nhưng
khác biệt về thời gian là rất lớn:

| Chế độ | Thời gian | Lời goi | Kết quả |
|---|---|---|---|
| `deepagent` (mặc định) | > 12 phút, chưa xong | 199 (80 lần 429) | — |
| `legacy` | **26,8 giây** | ~5 | 14 luật |

Đặt `RULE_PROPOSER_MODE=legacy` trong `.env` là chuyển được. **Tôi không tự đổi
mặc định** — DeepAgent dùng tool để tra cứu thêm bằng chứng nên luật của nó có thể
sâu hơn; đánh đổi giữa chất lượng và thời gian là quyết định của bạn, không phải
của tôi.

Backend **368 passed**.

---

## Phần AA — Kiểm chứng thật mục 2 và 3 (trước đó mới chỉ biên dịch được)

Mục 1 tôi đã đo bằng số, nhưng mục 2 và 3 mới chỉ qua `tsc` + `vite build` — tức
là **dịch được**, không phải **chạy đúng**. Dự án không có bộ chạy test frontend
(`vitest`/`@testing-library` đều chưa cài), nên tôi xác minh bằng cách khác.

### AA1. Lỗi trong chính bản vá mục 2 của tôi

Đọc lại logic mình viết:

```tsx
useEffect(() => { setRevealed(false); }, [activeRun?.id]);
```

Reset mỗi khi **id lượt chạy** đổi. Nhưng id đổi ngay khoảnh khắc lượt chạy mới
được tạo — tức đúng lượt người dùng vừa bấm — nên kết quả có thể bị ẩn ngay sau
khi họ bấm. Và khi họ đã mở "Xem lượt chạy trước", chỉ cần một lần
`refreshWorkspace` trả về lượt gần nhất khác là nó lại đóng.

Ý định ban đầu là reset khi **đổi bộ dữ liệu**, không phải đổi lượt chạy.

**Sửa:** truyền `datasetId` xuống `RunsPage` và khoá effect theo nó. Lượt chạy do
người dùng bắt đầu (hoặc đang chạy) vẫn tự hiện qua effect `[busy]`.

### AA2. Mục 3 — render báo cáo qua chính thư viện thật

Chạy `react-markdown` + `remark-gfm` trên hai báo cáo Steward có thật trong
`output/steward_reports/`, render bằng `renderToStaticMarkup`:

| Báo cáo | Tiêu đề | Bảng | th / td | blockquote | code | còn markdown thô? |
|---|---|---|---|---|---|---|
| `..._dq-6a3f9303...md` | 16 | 5 | 19 / 72 | 0 | 0 | không |
| `..._run_7363dc91.md` (bản mới nhất) | — | — | 16 / 160 | 2 | 29 | không |

Trích đoạn HTML thật:

```html
<h1>Báo Cáo Data Steward — Kết Quả Kiểm Tra Chất Lượng Dữ Liệu</h1>
<blockquote><p><strong>Lưu ý:</strong> Báo cáo này được tạo tự động theo template…</p></blockquote>
<hr/>
<h2>1. Thông Tin Phiên Chạy</h2>
<table><thead><tr><th>Trường</th><th>Giá trị</th></tr></thead>…
```

Khối `blockquote` đó chính là dòng cảnh báo được vẽ thành hộp hổ phách. Không còn
dấu `|---|` nào lọt ra HTML.

`tsc` sạch, `vite build` thành công. Lượt này không đụng file backend nào; lần
chạy đầy đủ gần nhất là **368 passed**.

---

## Kiểm chứng

| Hạng mục | Kết quả |
|---|---|
| Test backend | **333 passed**, 10 skipped |
| Test mới thêm | `tests/unit/test_data_dictionary_store.py` — **10 passed** |
| `tsc --noEmit` | sạch |
| `vite build` | thành công |
| Smoke test import | `202` → version `READY` → job `SUCCEEDED` |
| Smoke test dictionary | CSV `201`, JSON `201`, file hỏng `422`, `DELETE` `204`, `GET` sau đó trả `null` |

**11 test fail còn lại là lỗi có sẵn, không phải do thay đổi này.** Đã kiểm
chứng bằng cách dựng `git worktree` sạch tại `HEAD` và chạy đúng 4 file test đó
— ra **cùng một danh sách 11 fail**. Chúng nằm ở `test_dashboard_agent_workflow`,
`test_rule_proposer_workflow`, `test_rule_proposer_node`,
`test_rule_proposal_core_evidence` — không file nào liên quan tới phần đã sửa.

---

## Việc chưa làm / cần lưu ý

1. **`.env` và `.env.local` không nằm trong git.** Phần cấu hình
   `VITE_WORKSPACE_ID` sẽ không tự sang máy người khác — đã thêm vào
   `.env.example` để đồng đội biết đường điền.
2. **Phần lớn thay đổi chưa commit, chưa push.** A1/A2 đã nằm trong `a2ee821`.

Các điểm từng nằm trong mục này đã được sửa dứt điểm: hack
`WORKER_DISPATCH_MODE=inline` và description hardcode (A4, A5), node Graph 2 và
luật tổng hợp (Q1, Q2).

## Danh sách file thay đổi

**Backend**
- `src/models/database.py` — bảng `DatasetDataDictionaryModel`
- `src/services/data_dictionary_store.py` — *mới*
- `src/api/routes.py` — 3 endpoint dictionary
- `src/services/session_service.py` — `ensure_default_workspace`
- `src/services/rule_store.py` — gọi seed trong `init_db`
- `src/services/graph1_workflow.py` — seed dictionary vào state Graph 1A
- `src/services/job_dispatch.py` — fallback chạy in-process khi worker không gọi được (chỉ ngoài production)
- `tests/unit/test_data_dictionary_store.py` — *mới*
- `tests/unit/test_versioned_profile_workflow_gate.py` — *mới*
- `tests/unit/test_bulk_proposal_review.py` — *mới*
- `tests/unit/test_local_schema_migrations.py` — *mới*
- `tests/unit/test_dataset_rows_sorting.py` — *mới*
- `src/services/rule_proposer_workflow.py` — hợp nhất định nghĩa "đã profile" cho cả hai đường import

**Frontend**
- `frontend/src/components/wizard/Step1DataPreparation.tsx` — *mới*, phần 1+2 chia 2 cột
- `frontend/src/components/wizard/DetailOverlay.tsx` — *mới*
- `frontend/src/components/NotificationBell.tsx` — *mới*
- `frontend/src/components/wizard/AnomalyStatisticsPanel.tsx` — *mới*, thay `Graph3Analytics`
- `frontend/src/components/wizard/DatasetCatalogView.tsx` — *mới*
- `frontend/src/components/wizard/DataExplorerDialog.tsx` — viết lại
- `frontend/src/App.tsx` — nối component, toast, xoá `DatasetsPage`, chuông thông báo, overlay, `ProgressPanel` chạy theo telemetry node
- `frontend/src/api/client.ts`, `mockApi.ts`, `types.ts` — API dictionary, `confirmSemanticContract`, bổ sung `"CONFIRMED"` vào union trạng thái artifact
- `frontend/src/styles.css` — style Bước 1, tab Explorer, overlay, bảng catalog, chuông; bỏ ghim stepper; `page-container` co giãn; dàn đều node Graph 1A; drawer node → trang
- `frontend/src/components/graph/NodeDetailDrawer.tsx` — drawer cạnh → hộp thoại nổi; `SummaryTree` đọc descriptor; bảng đặc tả Vào/Ra
- `frontend/src/components/graph/NodeCard.tsx`, `GraphFlow.tsx` — số thứ tự bước

**Cấu hình**
- `.env` — gỡ `WORKER_DISPATCH_MODE=inline`, thay bằng ghi chú
- `frontend/.env`, `.env.local`, `.env.example` — `VITE_WORKSPACE_ID`
