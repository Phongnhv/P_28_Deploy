import { useEffect, useState } from "react";
import type { Dataset, DatasetRow, DatasetRowsResponse } from "../../types";

/** How many rows the preview asks for. A peek at the data, not a browser. */
const PREVIEW_LIMIT = 20;

const COLUMNS: { key: keyof DatasetRow; label: string; numeric?: boolean }[] = [
  { key: "source_row_id", label: "Row ID" },
  { key: "vendor_id", label: "Vendor" },
  { key: "pickup_at", label: "Pickup" },
  { key: "dropoff_at", label: "Dropoff" },
  { key: "passenger_count", label: "Passengers", numeric: true },
  { key: "trip_distance", label: "Distance", numeric: true },
  { key: "payment_type", label: "Payment" },
  { key: "fare_amount", label: "Fare", numeric: true },
  { key: "total_amount", label: "Total", numeric: true },
];

function formatCell(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2);
  return String(value);
}

/**
 * A quick look at the first rows of a dataset, as a modal.
 *
 * This replaced a full-page swap on step 1. Checking what the data looks like is
 * a glance, not a destination: swapping the page away meant losing the upload
 * and contract context you were in the middle of reading.
 */
export function DataExplorerDialog({
  dataset,
  language,
  loadRows,
  onClose,
}: {
  dataset: Dataset;
  language: "en" | "vi";
  loadRows: (datasetId: string, limit: number) => Promise<DatasetRowsResponse>;
  onClose: () => void;
}) {
  const vi = language === "vi";
  const [response, setResponse] = useState<DatasetRowsResponse | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

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
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const rows = response?.rows ?? [];

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
                ? `${PREVIEW_LIMIT} dòng đầu tiên trong tổng số ${dataset.row_count.toLocaleString()} dòng.`
                : `First ${PREVIEW_LIMIT} rows of ${dataset.row_count.toLocaleString()}.`}
            </p>
          </div>
          <button type="button" className="explorer-dialog-close" onClick={onClose} aria-label={vi ? "Đóng" : "Close"}>
            ✕
          </button>
        </header>

        <div className="explorer-dialog-body">
          {loading && <div className="explorer-dialog-state">{vi ? "Đang tải…" : "Loading…"}</div>}
          {error && <div className="explorer-dialog-state error">{error}</div>}
          {!loading && !error && rows.length === 0 && (
            <div className="explorer-dialog-state">
              {vi ? "Tập dữ liệu này chưa có dòng nào." : "This dataset has no rows yet."}
            </div>
          )}
          {!loading && !error && rows.length > 0 && (
            <div className="explorer-dialog-scroll">
              <table className="explorer-dialog-table">
                <thead>
                  <tr>
                    {COLUMNS.map((column) => (
                      <th key={String(column.key)} className={column.numeric ? "numeric" : ""}>
                        {column.label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.source_row_id}>
                      {COLUMNS.map((column) => (
                        <td key={String(column.key)} className={column.numeric ? "numeric" : ""}>
                          {formatCell(row[column.key])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
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
