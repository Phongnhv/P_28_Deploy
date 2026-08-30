import { useEffect, useState } from "react";
import type { GraphNodeSpec, NodeRun, NodeRunDetail } from "../../types";
import { formatDuration, getKindLabel } from "./NodeCard";

/**
 * `summarize()` in src/services/node_telemetry.py replaces every container it
 * redacts with a small descriptor: `{type:"dict", keys:n}`,
 * `{type:"records", count:n, fields:[...]}`, `{type:"list", count:n, sample:[...]}`.
 * Rendered as ordinary key/value rows those turn one fact into two or three
 * lines of noise ("type / dict", "keys / 4"), which is what made this panel
 * unreadable. Recognise them and state the fact once.
 */
type Descriptor = {
  kind: "dict" | "records" | "list" | "opaque";
  count?: number;
  fields?: string[];
  sample?: unknown[];
  raw: string;
};

function readDescriptor(value: Record<string, unknown>): Descriptor | null {
  const type = value.type;
  if (typeof type !== "string") return null;
  const keys = Object.keys(value);
  // A real payload key called "type" is common, so only treat this as a
  // descriptor when every other key is one the summariser emits.
  const allowed = new Set(["type", "keys", "count", "fields", "sample"]);
  if (!keys.every((key) => allowed.has(key))) return null;

  if (type === "dict") return { kind: "dict", count: Number(value.keys ?? 0), raw: type };
  if (type === "records") {
    return {
      kind: "records",
      count: Number(value.count ?? 0),
      fields: Array.isArray(value.fields) ? value.fields.map(String) : [],
      raw: type,
    };
  }
  if (type === "list") {
    return {
      kind: "list",
      count: Number(value.count ?? 0),
      sample: Array.isArray(value.sample) ? value.sample : undefined,
      raw: type,
    };
  }
  return keys.length === 1 ? { kind: "opaque", raw: type } : null;
}

function DescriptorChip({ descriptor, vi }: { descriptor: Descriptor; vi: boolean }) {
  if (descriptor.kind === "dict") {
    return (
      <span className="graph-shape shape-dict">
        <b>{descriptor.count}</b> {vi ? "khoá" : descriptor.count === 1 ? "key" : "keys"}
      </span>
    );
  }
  if (descriptor.kind === "records") {
    return (
      <span className="graph-shape shape-records">
        <b>{descriptor.count}</b> {vi ? "bản ghi" : descriptor.count === 1 ? "record" : "records"}
      </span>
    );
  }
  if (descriptor.kind === "list") {
    return (
      <span className="graph-shape shape-list">
        <b>{descriptor.count}</b> {vi ? "phần tử" : descriptor.count === 1 ? "item" : "items"}
      </span>
    );
  }
  return <span className="graph-shape shape-opaque">{descriptor.raw}</span>;
}

/** Render a redacted summary tree without pretending it is real data. */
function SummaryTree({ value, depth = 0, vi }: { value: unknown; depth?: number; vi: boolean }) {
  if (value === null || value === undefined) return <em className="graph-summary-null">null</em>;
  if (typeof value === "boolean") {
    return <span className={`graph-summary-bool ${value ? "yes" : "no"}`}>{String(value)}</span>;
  }
  if (typeof value !== "object") return <span className="graph-summary-scalar">{String(value)}</span>;

  if (Array.isArray(value)) {
    return (
      <ul className="graph-summary-list">
        {value.map((item, index) => (
          <li key={index}>
            <SummaryTree value={item} depth={depth + 1} vi={vi} />
          </li>
        ))}
      </ul>
    );
  }

  const record = value as Record<string, unknown>;
  const descriptor = readDescriptor(record);
  if (descriptor) {
    return (
      <div className="graph-summary-shape">
        <DescriptorChip descriptor={descriptor} vi={vi} />
        {descriptor.fields && descriptor.fields.length > 0 && (
          <span className="graph-shape-fields">
            {descriptor.fields.map((field) => (
              <code key={field}>{field}</code>
            ))}
          </span>
        )}
        {descriptor.sample && descriptor.sample.length > 0 && (
          <div className="graph-shape-sample">
            <span className="graph-shape-sample-label">{vi ? "mẫu" : "sample"}</span>
            <ul className="graph-summary-list">
              {descriptor.sample.map((item, index) => (
                <li key={index}>
                  <SummaryTree value={item} depth={depth + 1} vi={vi} />
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    );
  }

  const entries = Object.entries(record);
  if (entries.length === 0) return <em className="graph-summary-null">{vi ? "rỗng" : "empty"}</em>;

  return (
    <dl className={`graph-summary-object depth-${Math.min(depth, 3)}`}>
      {entries.map(([key, item]) => (
        <div className="graph-summary-entry" key={key}>
          {/* The summariser marks truncation with a "…" key; it is a note about
              the listing, not a field of the payload. */}
          <dt className={key === "…" ? "truncation" : undefined}>{key}</dt>
          <dd>
            <SummaryTree value={item} depth={depth + 1} vi={vi} />
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

  // Only lock the page behind once this is actually on screen; the component
  // renders null while no node is selected.
  const open = Boolean(node);
  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [open]);

  if (!node) return null;
  const vi = language === "vi";

  return (
    <>
      <div className="graph-drawer-scrim" onClick={onClose} aria-hidden="true" />
      <aside className="graph-drawer" role="dialog" aria-modal="true" aria-label={vi ? node.label_vi : node.label_en}>
        <header className="graph-drawer-head">
          <div>
            <span className={`graph-node-kind kind-${node.kind.toLowerCase()}`}>{getKindLabel(node.kind, vi)}</span>
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
                  <div className="pane-in">
                    <h4>
                      <span className="pane-arrow" aria-hidden="true">↓</span>
                      {vi ? "Dữ liệu vào" : "Input"}
                    </h4>
                    <SummaryTree value={detail.input_summary} vi={vi} />
                  </div>
                  <div className="pane-out">
                    <h4>
                      <span className="pane-arrow" aria-hidden="true">↑</span>
                      {vi ? "Dữ liệu ra" : "Output"}
                    </h4>
                    <SummaryTree value={detail.output_summary} vi={vi} />
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
