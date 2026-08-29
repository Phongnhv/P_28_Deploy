import { useEffect, useMemo, useState } from "react";
import type { DataDictionary, Dataset, DatasetRow, DatasetRowsResponse } from "../../types";

/** How many rows the preview asks for. A peek at the data, not a browser. */
const PREVIEW_LIMIT = 20;

function formatCell(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2);
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** Columns the API adds for bookkeeping; they are not part of the user's data. */
const INTERNAL_COLUMNS = new Set(["source_row_id", "_row_id", "__index__"]);

function isNumericType(logicalType?: string): boolean {
  const type = (logicalType ?? "").toLowerCase();
  return ["int", "float", "double", "decimal", "number", "numeric", "long", "bigint"].some((hint) =>
    type.includes(hint),
  );
}

/**
 * A quick look at the first rows of a dataset, as a modal.
 *
 * The column list is derived from the response, never hard-coded. An earlier
 * version listed the taxi fixture's columns literally, so every generic upload
 * rendered a table of empty cells: the rows were there, but none of their keys
 * matched the names the table asked for.
 */
export function DataExplorerDialog({
  dataset,
  language,
  loadRows,
  loadDictionary,
  onClose,
}: {
  dataset: Dataset;
  language: "en" | "vi";
  loadRows: (datasetId: string, limit: number) => Promise<DatasetRowsResponse>;
  loadDictionary?: (datasetId: string) => Promise<DataDictionary | null>;
  onClose: () => void;
}) {
  const vi = language === "vi";
  const [response, setResponse] = useState<DatasetRowsResponse | null>(null);
  const [dictionary, setDictionary] = useState<DataDictionary | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<"rows" | "columns" | "dictionary">("rows");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    loadRows(dataset.id, PREVIEW_LIMIT)
      .then((value) => {
        if (!cancelled) setResponse(value);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : vi ? "Không đọc được dữ liệu." : "Unable to read rows.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [dataset.id, loadRows, vi]);

  useEffect(() => {
    if (!loadDictionary) return;
    let cancelled = false;
    // A missing dictionary is the ordinary case, so a failure here must not
    // block the preview the operator actually opened the dialog for.
    loadDictionary(dataset.id)
      .then((value) => {
        if (!cancelled) setDictionary(value);
      })
      .catch(() => {
        if (!cancelled) setDictionary(null);
      });
    return () => {
      cancelled = true;
    };
  }, [dataset.id, loadDictionary]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const rows: DatasetRow[] = response?.rows ?? [];

  // Prefer the declared schema; fall back to the union of row keys so a dataset
  // whose version predates schema capture still renders something useful.
  const columns = useMemo(() => {
    const declared = (response?.schema ?? []).filter((column) => column?.name);
    if (declared.length) {
      return declared
        .filter((column) => !INTERNAL_COLUMNS.has(column.name))
        .map((column) => ({
          key: column.name,
          label: column.name,
          type: column.logical_type ?? column.physical_type,
          numeric: isNumericType(column.logical_type ?? column.physical_type),
        }));
    }
    const keys: string[] = [];
    rows.forEach((row) => {
      Object.keys(row).forEach((key) => {
        if (!INTERNAL_COLUMNS.has(key) && !keys.includes(key)) keys.push(key);
      });
    });
    return keys.map((key) => ({
      key,
      label: key,
      type: undefined as string | undefined,
      numeric: rows.some((row) => typeof row[key] === "number"),
    }));
  }, [response?.schema, rows]);

  const dictionaryColumns = dictionary?.tables?.[0]?.columns ?? [];
  const dictionaryByName = useMemo(
    () => new Map(dictionaryColumns.map((column) => [column.name, column])),
    [dictionaryColumns],
  );

  const rowKey = (row: DatasetRow, index: number) =>
    typeof row.source_row_id === "string" && row.source_row_id ? row.source_row_id : `row-${index}`;

  return (
    <>
      <div className="explorer-dialog-scrim" onClick={onClose} aria-hidden="true" />
      <div className="explorer-dialog" role="dialog" aria-modal="true" aria-label={dataset.name}>
        <header className="explorer-dialog-head">
          <div>
            <span className="eyebrow">{vi ? "XEM NHANH DỮ LIỆU" : "DATA PREVIEW"}</span>
            <h3>{dataset.name}</h3>
            <p>
              {vi
                ? `${columns.length} cột · ${PREVIEW_LIMIT} dòng đầu trong tổng số ${dataset.row_count.toLocaleString()} dòng.`
                : `${columns.length} columns · first ${PREVIEW_LIMIT} of ${dataset.row_count.toLocaleString()} rows.`}
            </p>
          </div>
          <button type="button" className="explorer-dialog-close" onClick={onClose} aria-label={vi ? "Đóng" : "Close"}>
            ✕
          </button>
        </header>

        <nav className="explorer-dialog-tabs">
          <button type="button" className={tab === "rows" ? "active" : ""} onClick={() => setTab("rows")}>
            {vi ? "Dữ liệu mẫu" : "Sample rows"}
          </button>
          <button type="button" className={tab === "columns" ? "active" : ""} onClick={() => setTab("columns")}>
            {vi ? "Chi tiết cột" : "Columns"} <span className="explorer-tab-count">{columns.length}</span>
          </button>
          <button type="button" className={tab === "dictionary" ? "active" : ""} onClick={() => setTab("dictionary")}>
            {vi ? "Data dictionary" : "Data dictionary"}
            {dictionaryColumns.length > 0 && <span className="explorer-tab-count">{dictionaryColumns.length}</span>}
          </button>
        </nav>

        <div className="explorer-dialog-body">
          {loading && <div className="explorer-dialog-state">{vi ? "Đang tải…" : "Loading…"}</div>}
          {error && <div className="explorer-dialog-state error">{error}</div>}

          {!loading && !error && tab === "rows" && (
            rows.length === 0 ? (
              <div className="explorer-dialog-state">
                {vi ? "Tập dữ liệu này chưa có dòng nào." : "This dataset has no rows yet."}
              </div>
            ) : (
              <div className="explorer-dialog-scroll">
                <table className="explorer-dialog-table">
                  <thead>
                    <tr>
                      {columns.map((column) => (
                        <th key={column.key} className={column.numeric ? "numeric" : ""}>
                          {column.label}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row, index) => (
                      <tr key={rowKey(row, index)}>
                        {columns.map((column) => (
                          <td key={column.key} className={column.numeric ? "numeric" : ""}>
                            {formatCell(row[column.key])}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          )}

          {!loading && !error && tab === "columns" && (
            <div className="explorer-dialog-scroll">
              <table className="explorer-dialog-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>{vi ? "Tên cột" : "Column"}</th>
                    <th>{vi ? "Kiểu" : "Type"}</th>
                    <th>{vi ? "Giá trị mẫu" : "Sample value"}</th>
                    <th>{vi ? "Mô tả" : "Description"}</th>
                  </tr>
                </thead>
                <tbody>
                  {columns.map((column, index) => (
                    <tr key={column.key}>
                      <td className="numeric">{index + 1}</td>
                      <td><code>{column.label}</code></td>
                      <td>{column.type ?? "—"}</td>
                      <td>{formatCell(rows.find((row) => row[column.key] !== null && row[column.key] !== undefined)?.[column.key])}</td>
                      <td>{dictionaryByName.get(column.key)?.description || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {!loading && !error && tab === "dictionary" && (
            dictionaryColumns.length === 0 ? (
              <div className="explorer-dialog-state">
                {vi
                  ? "Chưa có data dictionary được tải lên. Agent sẽ tự sinh ở Graph 1A."
                  : "No dictionary uploaded. The agent will infer one in Graph 1A."}
              </div>
            ) : (
              <div className="explorer-dialog-scroll">
                <p className="explorer-dictionary-source">
                  {vi ? "Nguồn: " : "Source: "}
                  <strong>{dictionary?.source_filename || (vi ? "đã tải lên" : "uploaded")}</strong>
                </p>
                <table className="explorer-dialog-table">
                  <thead>
                    <tr>
                      <th>{vi ? "Cột" : "Column"}</th>
                      <th>{vi ? "Mô tả" : "Description"}</th>
                      <th>{vi ? "Kiểu ngữ nghĩa" : "Semantic type"}</th>
                      <th>{vi ? "Vai trò" : "Business role"}</th>
                      <th>{vi ? "Cho phép null" : "Nullable"}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dictionaryColumns.map((column) => (
                      <tr key={column.name}>
                        <td><code>{column.name}</code></td>
                        <td>{column.description || "—"}</td>
                        <td>{column.semantic_type}</td>
                        <td>{column.business_role}</td>
                        <td>{column.nullable_expected ? (vi ? "Có" : "Yes") : (vi ? "Không" : "No")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          )}
        </div>

        <footer className="explorer-dialog-foot">
          <span>
            {vi ? "Truy cập đọc có giới hạn — chỉ hiển thị mẫu." : "Bounded read access — sample only."}
          </span>
          <button type="button" className="button secondary" onClick={onClose}>
            {vi ? "Đóng" : "Close"}
          </button>
        </footer>
      </div>
    </>
  );
}
