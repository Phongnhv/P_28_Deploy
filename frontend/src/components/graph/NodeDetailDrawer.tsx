import { useEffect, useState } from "react";
import type { GraphNodeSpec, NodeRun, NodeRunDetail } from "../../types";
import { formatDuration } from "./NodeCard";

/** Render a redacted summary tree without pretending it is real data. */
function SummaryTree({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value === null || value === undefined) return <em className="graph-summary-null">null</em>;
  if (typeof value !== "object") return <span className="graph-summary-scalar">{String(value)}</span>;

  if (Array.isArray(value)) {
    return (
      <ul className="graph-summary-list">
        {value.map((item, index) => (
          <li key={index}>
            <SummaryTree value={item} depth={depth + 1} />
          </li>
        ))}
      </ul>
    );
  }

  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length === 0) return <em className="graph-summary-null">{"{}"}</em>;

  return (
    <dl className={`graph-summary-object depth-${Math.min(depth, 3)}`}>
      {entries.map(([key, item]) => (
        <div className="graph-summary-entry" key={key}>
          <dt>{key}</dt>
          <dd>
            <SummaryTree value={item} depth={depth + 1} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function NodeDetailDrawer({
  node,
  run,
  language,
  onClose,
  loadDetail,
}: {
  node: GraphNodeSpec | null;
  run?: NodeRun;
  language: "en" | "vi";
  onClose: () => void;
  loadDetail: (nodeRunId: string) => Promise<NodeRunDetail>;
}) {
  const [detail, setDetail] = useState<NodeRunDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDetail(null);
    setError(null);
    if (!run) return;
    let cancelled = false;
    setLoading(true);
    loadDetail(run.id)
      .then((value) => {
        if (!cancelled) setDetail(value);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Unable to load node detail.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [run, loadDetail]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!node) return null;
  const vi = language === "vi";

  return (
    <>
      <div className="graph-drawer-scrim" onClick={onClose} aria-hidden="true" />
      <aside className="graph-drawer" role="dialog" aria-label={vi ? node.label_vi : node.label_en}>
        <header className="graph-drawer-head">
          <div>
            <span className={`graph-node-kind kind-${node.kind.toLowerCase()}`}>{node.kind}</span>
            <h3>{vi ? node.label_vi : node.label_en}</h3>
            <code>{node.name}</code>
          </div>
          <button type="button" className="graph-drawer-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>

        <div className="graph-drawer-body">
          <section>
            <span className="eyebrow">{vi ? "MỤC ĐÍCH" : "PURPOSE"}</span>
            <p>{vi ? node.purpose_vi : node.purpose_en}</p>
          </section>

          {run ? (
            <section className="graph-drawer-metrics">
              <div>
                <span>{vi ? "Trạng thái" : "Status"}</span>
                <strong className={`state-${run.status.toLowerCase()}`}>{run.status}</strong>
              </div>
              <div>
                <span>{vi ? "Thời lượng" : "Duration"}</span>
                <strong>{formatDuration(run.duration_ms)}</strong>
              </div>
              <div>
                <span>{vi ? "Thứ tự" : "Sequence"}</span>
                <strong>#{run.sequence}</strong>
              </div>
              {run.model_name && (
                <div>
                  <span>{vi ? "Mô hình" : "Model"}</span>
                  <strong>{run.model_name}</strong>
                </div>
              )}
              {run.started_at && (
                <div>
                  <span>{vi ? "Bắt đầu" : "Started"}</span>
                  <strong>{new Date(run.started_at).toLocaleString()}</strong>
                </div>
              )}
            </section>
          ) : (
            <section className="graph-drawer-empty">
              {vi
                ? "Node này chưa chạy trong ngữ cảnh đang xem."
                : "This node has not run in the context you are viewing."}
            </section>
          )}

          {run?.error_message && (
            <section className="graph-drawer-error">
              <span className="eyebrow">{vi ? "LỖI" : "ERROR"}</span>
              <pre>{run.error_message}</pre>
            </section>
          )}

          {(node.inputs.length > 0 || node.outputs.length > 0 || node.db_tables.length > 0) && (
            <section className="graph-drawer-contract">
              {node.inputs.length > 0 && (
                <div>
                  <span className="eyebrow">{vi ? "ĐẦU VÀO" : "INPUTS"}</span>
                  <div className="graph-chip-row">
                    {node.inputs.map((item) => (
                      <code key={item}>{item}</code>
                    ))}
                  </div>
                </div>
              )}
              {node.outputs.length > 0 && (
                <div>
                  <span className="eyebrow">{vi ? "ĐẦU RA" : "OUTPUTS"}</span>
                  <div className="graph-chip-row">
                    {node.outputs.map((item) => (
                      <code key={item}>{item}</code>
                    ))}
                  </div>
                </div>
              )}
              {node.db_tables.length > 0 && (
                <div>
                  <span className="eyebrow">{vi ? "BẢNG DỮ LIỆU" : "DB TABLES"}</span>
                  <div className="graph-chip-row">
                    {node.db_tables.map((item) => (
                      <code key={item}>{item}</code>
                    ))}
                  </div>
                </div>
              )}
            </section>
          )}

          {run && (
            <section>
              <span className="eyebrow">{vi ? "TÓM TẮT VÀO / RA" : "INPUT / OUTPUT SUMMARY"}</span>
              <p className="graph-drawer-privacy">
                {vi
                  ? "Chỉ tên khoá, số lượng và giá trị vô hướng ngắn. Không có giá trị dòng dữ liệu thật."
                  : "Key names, counts and short scalars only. No source row values."}
              </p>
              {loading && <div className="graph-drawer-loading">{vi ? "Đang tải…" : "Loading…"}</div>}
              {error && <div className="graph-drawer-error-text">{error}</div>}
              {detail && (
                <div className="graph-summary-panes">
                  <div>
                    <h4>{vi ? "Vào" : "Input"}</h4>
                    <SummaryTree value={detail.input_summary} />
                  </div>
                  <div>
                    <h4>{vi ? "Ra" : "Output"}</h4>
                    <SummaryTree value={detail.output_summary} />
                  </div>
                </div>
              )}
            </section>
          )}

          {node.source && (
            <section className="graph-drawer-source">
              <span className="eyebrow">{vi ? "MÃ NGUỒN" : "SOURCE"}</span>
              <code>{node.source}</code>
            </section>
          )}
        </div>
      </aside>
    </>
  );
}
