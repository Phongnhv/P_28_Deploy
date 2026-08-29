import { useState } from "react";
import type { StewardReport } from "../../types";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Reader for the Markdown report produced by Graph 3's `report_writer` node.
 *
 * That node has always written a report to `output/steward_reports/`, but no
 * endpoint served it, so the last artifact of the whole pipeline never reached
 * anybody. Loading is on demand: most runs never produce one, and a 404 here is
 * an ordinary outcome rather than an error worth shouting about.
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
    <section className="steward-report-panel">
      <div className="steward-report-head">
        <div>
          <span className="eyebrow">{vi ? "BÁO CÁO ĐIỀU TRA" : "INVESTIGATION REPORT"}</span>
          <strong>{vi ? "Báo cáo Steward từ Graph 3" : "Steward report from Graph 3"}</strong>
          <p>
            {vi
              ? "Do node report_writer sinh ra sau khi điều tra nguyên nhân gốc."
              : "Written by the report_writer node after root-cause investigation."}
          </p>
        </div>
        <button className="button ghost" onClick={open} disabled={state === "loading"}>
          {state === "loading"
            ? vi
              ? "Đang tải…"
              : "Loading…"
            : report
              ? vi
                ? "Ẩn"
                : "Hide"
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
