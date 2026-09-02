import { useState } from "react";
import type { StewardReport } from "../../types";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Reader for the Markdown report produced by Graph 3's `report_writer` node.
 *
 * Graph 3 stores the generated Markdown in the workflow's ANOMALY_REPORT
 * artifact. Loading is on demand so the report body is fetched only when the
 * steward opens it.
 */
export function StewardReportPanel({
  runId,
  language,
  loadReport,
}: {
  runId: string;
  language: "en" | "vi";
  loadReport: (runId: string) => Promise<StewardReport>;
}) {
  const vi = language === "vi";
  const [report, setReport] = useState<StewardReport | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "missing" | "error">("idle");

  async function open() {
    if (report) {
      setReport(null);
      setState("idle");
      return;
    }
    setState("loading");
    try {
      setReport(await loadReport(runId));
      setState("idle");
    } catch {
      setState("missing");
    }
  }

  return (
    <section className="panel steward-report-panel">
      <div className="panel-heading" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <span className="eyebrow">{vi ? "GRAPH 3 · BÁO CÁO ĐIỀU TRA" : "GRAPH 3 · INVESTIGATION REPORT"}</span>
          <h2>{vi ? "Báo cáo Steward từ Graph 3" : "Steward Report from Graph 3"}</h2>
          <p className="muted">
            {vi
              ? "Do node report_writer sinh ra sau khi hoàn tất điều tra nguyên nhân gốc."
              : "Written by the report_writer node after completing root-cause investigation."}
          </p>
        </div>
        <button className="button secondary" onClick={open} disabled={state === "loading"}>
          {state === "loading"
            ? vi
              ? "Đang tải…"
              : "Loading…"
            : report
              ? vi
                ? "Ẩn báo cáo"
                : "Hide report"
              : vi
                ? "Xem báo cáo"
                : "View report"}
        </button>
      </div>

      {state === "missing" && (
        <div className="steward-report-empty">
          {vi
            ? "Lần chạy này chưa sinh báo cáo Steward nào."
            : "No steward report has been written for this run yet."}
        </div>
      )}

      {report && (
        <div className="steward-report-body">
          <div className="steward-report-meta">
            <code>{report.filename}</code>
            <span>{new Date(report.generated_at).toLocaleString()}</span>
          </div>
          {/* react-markdown and remark-gfm were already dependencies but nothing
              used them: the report was dumped into a <pre>, so a Steward read
              raw pipes and hashes instead of the tables the writer produced. */}
          <div className="steward-report-content">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{report.content}</ReactMarkdown>
          </div>
        </div>
      )}
    </section>
  );
}
