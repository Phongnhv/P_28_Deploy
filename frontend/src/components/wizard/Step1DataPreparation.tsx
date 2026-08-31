import { useEffect, useState, type ReactNode } from "react";
import type { DataDictionary, Dataset } from "../../types";

/**
 * Step 1, as two things to supply and three things to reveal.
 *
 * The screen used to run everything at once: picking a dataset immediately
 * refreshed the workspace and the quality catalogue and the observatory were
 * always on screen, for every dataset at once. That made it impossible to tell
 * which numbers belonged to the dataset you had just chosen. Here each stage is
 * behind its own action, and everything below the picker is scoped to the one
 * selected dataset.
 */

function SectionCard({
  index,
  title,
  hint,
  badge,
  children,
}: {
  index: string;
  title: string;
  hint: string;
  badge?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="prep-section">
      <header className="prep-section-head">
        <span className="prep-section-index">{index}</span>
        <div className="prep-section-title">
          <h2>{title}</h2>
          <p>{hint}</p>
        </div>
        {badge}
      </header>
      <div className="prep-section-body">{children}</div>
    </section>
  );
}

export function Step1DataPreparation({
  datasets,
  dataset,
  language,
  canOperate,
  importing,
  profiling,
  profileReady,
  onImportDataset,
  onSelectDataset,
  onDeleteDataset,
  onOpenExplorer,
  onProfileDataset,
  loadDictionary,
  uploadDictionary,
  deleteDictionary,
  profilePanel,
  observatoryPanel,
}: {
  datasets: Dataset[];
  dataset?: Dataset;
  language: "en" | "vi";
  canOperate: boolean;
  importing: boolean;
  profiling: boolean;
  profileReady: boolean;
  onImportDataset: (file: File) => void;
  onSelectDataset: (datasetId: string) => void;
  onDeleteDataset?: (datasetId: string) => void;
  onOpenExplorer: (datasetId: string) => void;
  onProfileDataset: () => void;
  loadDictionary: (datasetId: string) => Promise<DataDictionary | null>;
  uploadDictionary: (datasetId: string, file: File) => Promise<DataDictionary>;
  deleteDictionary: (datasetId: string) => Promise<void>;
  profilePanel: ReactNode;
  observatoryPanel: ReactNode;
}) {
  const vi = language === "vi";
  const [dictionary, setDictionary] = useState<DataDictionary | null>(null);
  const [dictionaryBusy, setDictionaryBusy] = useState(false);
  const [dictionaryError, setDictionaryError] = useState("");
  const [showProfile, setShowProfile] = useState(false);
  const [showObservatory, setShowObservatory] = useState(false);

  // Everything below the picker describes one dataset. Switching datasets must
  // collapse it, or the panels keep showing the previous dataset's numbers
  // under the new dataset's name.
  //
  // Keyed on the id alone. Depending on the `dataset` object meant any refresh
  // that rebuilt the list — polling a job, finishing an upload — produced a new
  // object identity and re-ran this, silently collapsing the profile panel the
  // user had just opened.
  const datasetId = dataset?.id;
  useEffect(() => {
    setShowProfile(false);
    setShowObservatory(false);
    setDictionaryError("");
    setDictionary(null);
    if (!datasetId) return;
    let cancelled = false;
    loadDictionary(datasetId)
      .then((value) => {
        if (!cancelled) setDictionary(value);
      })
      .catch(() => {
        if (!cancelled) setDictionary(null);
      });
    return () => {
      cancelled = true;
    };
  }, [datasetId, loadDictionary]);

  async function handleDictionaryUpload(file: File) {
    if (!dataset) return;
    setDictionaryBusy(true);
    setDictionaryError("");
    try {
      setDictionary(await uploadDictionary(dataset.id, file));
    } catch (err) {
      setDictionaryError(
        err instanceof Error ? err.message : vi ? "Không đọc được tệp từ điển." : "Unable to read the dictionary file.",
      );
    } finally {
      setDictionaryBusy(false);
    }
  }

  async function handleDictionaryRemove() {
    if (!dataset) return;
    setDictionaryBusy(true);
    setDictionaryError("");
    try {
      await deleteDictionary(dataset.id);
      setDictionary(null);
    } catch (err) {
      setDictionaryError(err instanceof Error ? err.message : vi ? "Không xoá được." : "Unable to remove it.");
    } finally {
      setDictionaryBusy(false);
    }
  }

  const dictionaryColumns = dictionary?.tables?.[0]?.columns ?? [];

  return (
    <div className="datasets-page prep-page">
      <div className="page-heading datasets-heading">
        <div>
          <span className="eyebrow">{vi ? "BƯỚC 1 · CHUẨN BỊ DỮ LIỆU" : "STEP 1 · DATA PREPARATION"}</span>
          <h1>{vi ? "Chuẩn bị dữ liệu" : "Prepare your data"}</h1>
          <p>
            {vi
              ? "Nạp dữ liệu và từ điển dữ liệu, chọn một bộ để làm việc, rồi profile trước khi sang Graph 1A."
              : "Supply the data and its dictionary, choose one to work on, then profile it before Graph 1A."}
          </p>
        </div>
      </div>

      {/* Parts 1 and 2 sit side by side: they are the two things you supply,
          and stacking them pushed the dataset list below the fold on every
          screen. They fall back to one column when there is no room. */}
      <div className="prep-supply-row">
      {/* ---- Part 1: the dataset itself ---- */}
      <SectionCard
        index="1"
        title={vi ? "Nạp bộ dữ liệu" : "Import a dataset"}
        hint={vi ? "CSV hoặc Parquet, tối đa 100 MB." : "CSV or Parquet, up to 100 MB."}
      >
        <label className={`prep-dropzone ${importing ? "busy" : ""}`}>
          <input
            type="file"
            accept=".csv,.parquet,text/csv,application/vnd.apache.parquet"
            disabled={!canOperate || importing}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) onImportDataset(file);
              event.currentTarget.value = "";
            }}
          />
          <span className="prep-dropzone-icon">{importing ? "…" : "+"}</span>
          <strong>{importing ? (vi ? "Đang nạp dữ liệu…" : "Importing…") : vi ? "Chọn tệp để tải lên" : "Choose a file to upload"}</strong>
          <small>{vi ? "Tệp được kiểm tra và tạo version tự động." : "The file is validated and versioned automatically."}</small>
        </label>
      </SectionCard>

      {/* ---- Part 2: the dictionary, optional by design ---- */}
      <SectionCard
        index="2"
        title={vi ? "Từ điển dữ liệu (tuỳ chọn)" : "Data dictionary (optional)"}
        hint={
          vi
            ? "Bỏ trống thì agent sẽ tự sinh từ điển ở Graph 1A. Tải lên thì bản của bạn được ưu tiên."
            : "Leave it empty and the agent infers one in Graph 1A. Upload one and yours is used instead."
        }
        badge={
          <span className={`prep-badge ${dictionaryColumns.length ? "supplied" : "inferred"}`}>
            {dictionaryColumns.length
              ? vi ? "DÙNG BẢN TẢI LÊN" : "USING UPLOAD"
              : vi ? "AGENT TỰ SINH" : "AGENT INFERS"}
          </span>
        }
      >
        {!dataset ? (
          <p className="prep-muted">
            {vi ? "Chọn một bộ dữ liệu bên dưới trước khi tải từ điển." : "Select a dataset below before uploading a dictionary."}
          </p>
        ) : dictionaryColumns.length ? (
          <div className="prep-dictionary-summary">
            <div>
              <strong>{dictionary?.source_filename || (vi ? "Từ điển đã tải lên" : "Uploaded dictionary")}</strong>
              <span className="prep-muted">
                {vi
                  ? `${dictionaryColumns.length} cột được mô tả · áp dụng cho ${dataset.name}`
                  : `${dictionaryColumns.length} columns described · applies to ${dataset.name}`}
              </span>
            </div>
            <button className="button ghost danger" disabled={dictionaryBusy || !canOperate} onClick={() => void handleDictionaryRemove()}>
              {vi ? "Gỡ bỏ" : "Remove"}
            </button>
          </div>
        ) : (
          <label className={`prep-dropzone compact ${dictionaryBusy ? "busy" : ""}`}>
            <input
              type="file"
              accept=".csv,.json,.tsv,text/csv,application/json"
              disabled={!canOperate || dictionaryBusy}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void handleDictionaryUpload(file);
                event.currentTarget.value = "";
              }}
            />
            <span className="prep-dropzone-icon">{dictionaryBusy ? "…" : "+"}</span>
            <strong>{dictionaryBusy ? (vi ? "Đang xử lý…" : "Processing…") : vi ? "Tải từ điển dữ liệu" : "Upload a data dictionary"}</strong>
            <small>
              {vi
                ? "CSV hoặc JSON, cần cột tên (column_name) và mô tả (description)."
                : "CSV or JSON with a column-name field and a description field."}
            </small>
          </label>
        )}
        {dictionaryError && <p className="prep-error">{dictionaryError}</p>}
      </SectionCard>
      </div>

      {/* ---- The catalogue of what has been uploaded ---- */}
      <section className="prep-section">
        <header className="prep-section-head">
          <span className="prep-section-index">3</span>
          <div className="prep-section-title">
            <h2>{vi ? "Dataset đã Upload" : "Uploaded datasets"}</h2>
            <p>
              {vi
                ? "Bấm vào một thẻ để chọn. Chọn xong chưa chạy gì — bạn quyết định bước tiếp theo."
                : "Click a card to select it. Selecting runs nothing — the next step is yours to trigger."}
            </p>
          </div>
          <span className="prep-count">{datasets.length}</span>
        </header>

        {datasets.length ? (
          <div className="prep-dataset-grid">
            {datasets.map((item) => {
              const isSelected = item.id === dataset?.id;
              return (
                <article
                  className={`prep-dataset-card ${isSelected ? "active" : ""}`}
                  key={item.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => onSelectDataset(item.id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      onSelectDataset(item.id);
                    }
                  }}
                >
                  <div className="prep-dataset-top">
                    <span className={`prep-status ${isSelected ? "selected" : ""}`}>
                      <span className="prep-status-dot" />
                      {isSelected
                        ? vi ? "ĐÃ CHỌN" : "SELECTED"
                        : vi
                          ? item.status === "REGISTERED"
                            ? "ĐÃ ĐĂNG KÝ"
                            : item.status === "PROFILE_READY"
                              ? "ĐÃ PROFILE"
                              : item.status.replaceAll("_", " ")
                          : item.status.replaceAll("_", " ")}
                    </span>
                    <code>{item.manifest_version}</code>
                  </div>
                  <h3 title={item.name}>{item.name}</h3>
                  <p title={item.description}>{item.description}</p>
                  {/* Row count and source moved out of the card. They are
                      per-dataset detail, and repeating them across every card
                      made the grid tall and sparse; the Data Explorer and the
                      dataset catalog both show them in context. */}
                  <div className="prep-dataset-actions">
                    <button
                      className="button secondary"
                      onClick={(event) => {
                        event.stopPropagation();
                        onOpenExplorer(item.id);
                      }}
                    >
                      {vi ? "Xem dữ liệu" : "Data Explorer"}
                    </button>
                    <button
                      className="button ghost danger"
                      title={vi ? "Xoá bộ dữ liệu" : "Delete dataset"}
                      onClick={(event) => {
                        event.stopPropagation();
                        onDeleteDataset?.(item.id);
                      }}
                    >
                      🗑
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        ) : (
          <div className="empty-state">
            <h2>{vi ? "Chưa có bộ dữ liệu nào." : "No datasets registered."}</h2>
            <p className="muted">{vi ? "Tải một tệp lên ở phần 1 để bắt đầu." : "Upload a file in part 1 to begin."}</p>
          </div>
        )}
      </section>

      {/* ---- Profiling, on demand, for the selected dataset only ---- */}
      <section className="prep-section">
        <header className="prep-section-head">
          <span className="prep-section-index">4</span>
          <div className="prep-section-title">
            <h2>{vi ? "Profile bộ dữ liệu" : "Profile the dataset"}</h2>
            <p>
              {dataset
                ? vi
                  ? `Thống kê chất lượng của "${dataset.name}".`
                  : `Quality statistics for "${dataset.name}".`
                : vi
                  ? "Chọn một bộ dữ liệu để bật phần này."
                  : "Select a dataset to enable this."}
            </p>
          </div>
          <button
            className="button secondary"
            disabled={!dataset || profiling}
            aria-expanded={showProfile}
            onClick={() => {
              setShowProfile((prev) => {
                const next = !prev;
                if (next && !profileReady) onProfileDataset();
                return next;
              });
            }}
          >
            {profiling
              ? (vi ? "Đang profile…" : "Profiling…")
              : showProfile
                ? (vi ? "Ẩn Profile dữ liệu" : "Hide profile")
                : (vi ? "Profile dữ liệu" : "Profile dataset")}
          </button>
        </header>
        {showProfile && <div className="prep-reveal">{profilePanel}</div>}
      </section>

      {/* ---- The observatory, collapsed until asked for (Temporarily hidden per user request) ---- */}
      {false && (
        <section className="prep-section">
          <header className="prep-section-head">
            <span className="prep-section-index">5</span>
            <div className="prep-section-title">
              <h2>{vi ? "Giám sát chất lượng dữ liệu" : "Data Quality Observability"}</h2>
              <p>
                {vi
                  ? "Theo dõi sức khoẻ run, độ trôi của rule và các tín hiệu cần chú ý."
                  : "Monitor run health, rule drift, and the signals that need attention."}
              </p>
            </div>
            <button
              className="button secondary"
              disabled={!dataset}
              aria-expanded={showObservatory}
              onClick={() => setShowObservatory((prev) => !prev)}
            >
              {showObservatory ? (vi ? "Ẩn bảng" : "Hide panel") : vi ? "Mở bảng" : "Open panel"}
            </button>
          </header>
          {showObservatory && <div className="prep-reveal">{observatoryPanel}</div>}
        </section>
      )}
    </div>
  );
}
