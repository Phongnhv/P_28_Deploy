import React from "react";
import { useI18n } from "../../i18n/context";
import type { AnalysisResult, AnalysisRunStatus, DqResult, DqAnomaly, QualityTrendPoint } from "../../types";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Cell,
} from "recharts";

interface Step5AnalyticsProps {
  results: DqResult[];
  anomalies: DqAnomaly[];
  trends: QualityTrendPoint[];
  analysis?: AnalysisResult | null;
  analysisStatus?: AnalysisRunStatus | null;
  analysisRunId?: string;
  onBack: () => void;
  onStartNewRun: () => void;
  onRerunAnalysis?: () => void;
  rerunBusy?: boolean;
}

type DashboardRuleResult = {
  rule_id: string;
  rule_title: string;
  status: string;
  checked_count: number;
  failed_count: number;
  anomaly?: { flagged: boolean };
};

export const Step5Analytics: React.FC<Step5AnalyticsProps> = ({
  results,
  anomalies,
  trends,
  analysis,
  analysisStatus,
  analysisRunId,
  onBack,
  onStartNewRun,
  onRerunAnalysis,
  rerunBusy = false,
}) => {
  const { t, language } = useI18n();

  const hasAnalysisEvidence = Boolean(analysis?.graph2?.available);
  const dashboardResults: DashboardRuleResult[] = hasAnalysisEvidence
    ? analysis!.graph2.results
    : results;
  const totalRules = hasAnalysisEvidence ? analysis!.graph2.summary.total : dashboardResults.length;
  const passedRules = hasAnalysisEvidence
    ? analysis!.graph2.summary.passed
    : dashboardResults.filter((r) => r.status === "PASS").length;
  const failedRules = hasAnalysisEvidence
    ? analysis!.graph2.summary.failed
    : dashboardResults.filter((r) => r.status === "FAIL").length;
  const errorRules = hasAnalysisEvidence ? analysis!.graph2.summary.errors : 0;
  const totalRowsChecked = hasAnalysisEvidence
    ? analysis!.graph2.summary.total_checked
    : dashboardResults.reduce((acc, r) => acc + (r.checked_count || 0), 0);
  const totalRowsFailed = hasAnalysisEvidence
    ? analysis!.graph2.summary.total_failed
    : dashboardResults.reduce((acc, r) => acc + (r.failed_count || 0), 0);

  const rulePassScore = totalRules > 0 ? (passedRules / totalRules) * 100 : 100;
  const score = hasAnalysisEvidence
    ? totalRowsChecked > 0
      ? Number(((1 - totalRowsFailed / totalRowsChecked) * 100).toFixed(2))
      : Number(rulePassScore.toFixed(2))
    : trends.length > 0
      ? trends[trends.length - 1].quality_score
      : Number(rulePassScore.toFixed(2));
  const scoreLabel = Number.isInteger(score) ? score.toFixed(0) : score.toFixed(2);

  // Grade determination
  const gradeLabel =
    score >= 90
      ? language === "vi" ? "Xuất sắc (Hạng A)" : "Excellent (Grade A)"
      : score >= 75
        ? language === "vi" ? "Đạt yêu cầu (Hạng B)" : "Passed (Grade B)"
        : score >= 50
          ? language === "vi" ? "Cần chú ý (Hạng C)" : "Attention Needed (Grade C)"
          : language === "vi" ? "Rủi ro cao (Hạng D)" : "High Risk (Grade D)";

  const scoreColor =
    score >= 80 ? "#10b981" : score >= 60 ? "#f59e0b" : "#ef4444";

  // Data for Rule Performance Breakdown (Top Failed or Checked Rules)
  const ruleBreakdownData = dashboardResults
    .map((r) => {
    const passRate =
      r.checked_count > 0
        ? Number((((r.checked_count - r.failed_count) / r.checked_count) * 100).toFixed(2))
        : r.status === "PASS" ? 100 : 0;
    return {
      name: r.rule_title.length > 22 ? r.rule_title.substring(0, 22) + "…" : r.rule_title,
      fullTitle: r.rule_title,
      failed: r.failed_count,
      checked: r.checked_count,
      passRate: passRate,
      status: r.status,
    };
    })
    .sort((a, b) => a.passRate - b.passRate)
    .slice(0, 12);

  // Show only persisted history. A first run must not be padded with
  // fabricated scores that look like production evidence.
  const trendData = trends.length > 0 ? trends.slice(-8).map((point, index, points) => ({
    run:
      index === points.length - 1
        ? (language === "vi" ? "Hiện tại" : "Current")
        : new Date(point.created_at).toLocaleDateString(language === "vi" ? "vi-VN" : "en-US", {
            month: "short",
            day: "numeric",
          }),
    score: point.quality_score,
  })) : hasAnalysisEvidence ? [{
    run: language === "vi" ? "Hiện tại" : "Current",
    score,
  }] : [];

  const visibleAnomalies = hasAnalysisEvidence
    ? (analysis?.graph3.signals ?? [])
        // Keep the alert banner aligned with the backend anomaly adapter:
        // cold-start observations without enough history are evidence, not
        // anomalies. Only signals at the persisted attention threshold belong
        // in the warning surface.
        .filter((signal) => signal.score >= 0.7)
        .slice(0, 8)
        .map((signal) => ({
          rule_title: signal.target_id,
          reason: signal.explanation,
          anomaly_type: signal.family,
        }))
    : anomalies.map((item) => ({
        rule_title: item.rule_title,
        reason: item.reason,
        anomaly_type: item.anomaly_type,
      }));

  return (
    <div>
      {/* Step Header */}
      <div
        className="page-heading"
        style={{
          marginBottom: "24px",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "16px",
        }}
      >
        <div>
          <span className="eyebrow">STEP 4 · {t("wizard.step4Title").toUpperCase()}</span>
          <h1>{t("analytics.title") || "Đánh giá kết quả & Bảng phân tích"}</h1>
          <p>{t("analytics.subtitle") || "Tổng quan chất lượng dữ liệu sau khi thực thi các quy tắc kiểm thử."}</p>
          {hasAnalysisEvidence && <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginTop: "10px" }}><span className="status-pill info">RULE PROPOSAL + ANOMALY DETECTION</span><span className="status-pill success">VERSIONED SOURCE ADAPTER</span>{analysisRunId && <code style={{ color: "var(--muted)", fontSize: "11px" }}>{analysisRunId}</code>}</div>}
        </div>
        <div style={{ display: "flex", gap: "12px" }}>
          <button
            className="button secondary"
            onClick={onBack}
            style={{ fontSize: "14px", padding: "8px 16px" }}
          >
            {t("wizard.back")}
          </button>
          {onRerunAnalysis && analysisRunId && <button
            className="button secondary"
            onClick={onRerunAnalysis}
            disabled={rerunBusy || analysisStatus === "RUNNING" || analysisStatus === "PENDING"}
            style={{ fontSize: "14px", padding: "8px 16px" }}
          >
            {rerunBusy ? "Đang rerun…" : "Rerun Rule Proposal & Anomaly Detection"}
          </button>}
          <button
            className="button primary"
            onClick={onStartNewRun}
            style={{ fontSize: "14px", padding: "8px 20px" }}
          >
            {t("wizard.startNewRun")}
          </button>
        </div>
      </div>

      {/* Hero Score Section */}
      <div
        style={{
          background: "var(--surface)",
          border: "1px solid var(--border)",
          borderRadius: "20px",
          padding: "28px 32px",
          marginBottom: "24px",
          display: "grid",
          gridTemplateColumns: "1fr 2fr",
          gap: "32px",
          alignItems: "center",
          boxShadow: "0 4px 20px -2px rgba(0, 0, 0, 0.05)",
        }}
      >
        {/* Left: Giant Score Gauge */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            borderRight: "1px solid var(--border)",
            paddingRight: "24px",
          }}
        >
          <div style={{ fontSize: "13px", textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--muted)", fontWeight: 600, marginBottom: "8px" }}>
            {t("analytics.dqScore") || "CHỈ SỐ CHẤT LƯỢNG TỔNG THỂ"}
          </div>
          <div
            style={{
              fontSize: "64px",
              fontWeight: 900,
              color: scoreColor,
              lineHeight: 1,
              letterSpacing: "-0.03em",
              marginBottom: "8px",
            }}
          >
            {scoreLabel}%
          </div>
          <span
            className={`status-pill ${score >= 80 ? "success" : score >= 60 ? "warning" : "danger"}`}
            style={{ fontSize: "13px", fontWeight: 700, padding: "6px 14px" }}
          >
            {gradeLabel}
          </span>
        </div>

        {/* Right: Quick Summary Cards */}
        <div>
          <h3 style={{ fontSize: "18px", fontWeight: 700, margin: "0 0 12px 0", color: "var(--ink)" }}>
            {language === "vi" ? "Tóm tắt kết quả kiểm soát dữ liệu" : "Data Control Summary"}
          </h3>
          <p style={{ fontSize: "14px", color: "var(--muted)", margin: "0 0 20px 0", lineHeight: 1.5 }}>
            {failedRules === 0
              ? (language === "vi"
                  ? `Toàn bộ ${totalRules} quy tắc kiểm tra đã đạt tiêu chuẩn 100%. Dữ liệu sẵn sàng đưa vào các đường ống xử lý sản xuất.`
                  : `All ${totalRules} rule checks passed 100%. Data is ready for downstream production pipelines.`)
              : (language === "vi"
                  ? `Phát hiện ${failedRules} / ${totalRules} quy tắc vi phạm với tổng cộng ${totalRowsFailed.toLocaleString()} dòng dữ liệu không đạt yêu cầu.`
                  : `Detected ${failedRules} / ${totalRules} failing rules with ${totalRowsFailed.toLocaleString()} total non-compliant rows.`)}
            {errorRules > 0 && ` ${errorRules} rule execution error${errorRules === 1 ? "" : "s"} also needs attention.`}
          </p>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "16px" }}>
            <div style={{ background: "rgba(0, 0, 0, 0.02)", padding: "14px", borderRadius: "12px", border: "1px solid var(--border)" }}>
              <span style={{ fontSize: "12px", color: "var(--muted)", fontWeight: 500 }}>
                {language === "vi" ? "Quy tắc đã duyệt" : "Rules Executed"}
              </span>
              <div style={{ fontSize: "22px", fontWeight: 800, color: "var(--ink)", marginTop: "4px" }}>
                {totalRules}
              </div>
            </div>
            <div style={{ background: "rgba(0, 0, 0, 0.02)", padding: "14px", borderRadius: "12px", border: "1px solid var(--border)" }}>
              <span style={{ fontSize: "12px", color: "var(--muted)", fontWeight: 500 }}>
                {language === "vi" ? "Tổng số dòng kiểm tra" : "Rows Evaluated"}
              </span>
              <div style={{ fontSize: "22px", fontWeight: 800, color: "var(--ink)", marginTop: "4px" }}>
                {totalRowsChecked > 0 ? totalRowsChecked.toLocaleString() : "—"}
              </div>
            </div>
            <div style={{ background: failedRules > 0 ? "rgba(239, 68, 68, 0.05)" : "rgba(16, 185, 129, 0.05)", padding: "14px", borderRadius: "12px", border: `1px solid ${failedRules > 0 ? "rgba(239, 68, 68, 0.2)" : "rgba(16, 185, 129, 0.2)"}` }}>
              <span style={{ fontSize: "12px", color: failedRules > 0 ? "#dc2626" : "#059669", fontWeight: 600 }}>
                {language === "vi" ? "Dòng vi phạm" : "Failing Rows"}
              </span>
              <div style={{ fontSize: "22px", fontWeight: 800, color: failedRules > 0 ? "#dc2626" : "#059669", marginTop: "4px" }}>
                {totalRowsFailed.toLocaleString()}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* AI Anomaly Alert Banner */}
      {visibleAnomalies.length > 0 ? (
        <div
          style={{
            background: "linear-gradient(135deg, rgba(245, 158, 11, 0.08) 0%, rgba(239, 68, 68, 0.08) 100%)",
            border: "1px solid rgba(245, 158, 11, 0.3)",
            borderRadius: "16px",
            padding: "20px 24px",
            marginBottom: "24px",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "12px" }}>
            <span style={{ fontSize: "18px" }}>⚠️</span>
            <h3 style={{ fontSize: "16px", fontWeight: 700, color: "#d97706", margin: 0 }}>
              {language === "vi"
                ? `Cảnh báo: Phát hiện ${visibleAnomalies.length} tín hiệu cần xem xét`
                : `Alert: ${visibleAnomalies.length} quality signals need review`}
            </h3>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
            {visibleAnomalies.map((item, idx) => (
              <div
                key={idx}
                style={{
                  background: "rgba(255, 255, 255, 0.7)",
                  padding: "10px 14px",
                  borderRadius: "8px",
                  fontSize: "13px",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <div>
                  <strong style={{ color: "var(--ink)", marginRight: "8px" }}>{item.rule_title}</strong>
                  <span style={{ color: "var(--muted)" }}>{item.reason}</span>
                </div>
                <span
                  className="status-pill warning"
                  style={{ fontSize: "11px", fontWeight: 700 }}
                >
                  {item.anomaly_type}
                </span>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div
          style={{
            background: "rgba(16, 185, 129, 0.05)",
            border: "1px solid rgba(16, 185, 129, 0.2)",
            borderRadius: "16px",
            padding: "16px 24px",
            marginBottom: "24px",
            display: "flex",
            alignItems: "center",
            gap: "12px",
          }}
        >
          <span style={{ fontSize: "18px", color: "#10b981" }}>✓</span>
          <span style={{ fontSize: "14px", fontWeight: 600, color: "#047857" }}>
            {language === "vi"
              ? "Không phát hiện bất kỳ biến động bất thường nào so với dữ liệu lịch sử."
              : "No historical data anomalies or sudden violation spikes detected."}
          </span>
        </div>
      )}

      {hasAnalysisEvidence && analysis && (
        <section className="panel" style={{ marginBottom: "24px", padding: "22px 24px", border: "1px solid var(--border)", background: "var(--surface)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "16px", flexWrap: "wrap" }}>
            <div>
              <span className="eyebrow">ANOMALY DETECTION · STEWARD DECISION</span>
              <h3 style={{ margin: "4px 0 6px" }}>{language === "vi" ? "Tín hiệu bất thường & khuyến nghị" : "Anomaly signals & recommendation"}</h3>
              <p className="muted" style={{ margin: 0, maxWidth: "760px", lineHeight: 1.5 }}>
                {analysis.graph3.decision?.override_reason || (analysis.graph3.decision
                  ? `${analysis.graph3.decision.decision} · confidence ${(analysis.graph3.decision.confidence * 100).toFixed(0)}%`
                  : language === "vi" ? "Anomaly Detection chưa phát sinh quyết định." : "Anomaly Detection has not produced a decision yet.")}
              </p>
            </div>
            <span className={`status-pill ${analysis.graph3.decision?.severity === "HIGH" ? "danger" : "info"}`}>
              {analysis.graph3.decision?.severity || (language === "vi" ? "CHƯA CÓ" : "NO DECISION")}
            </span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: "12px", marginTop: "18px" }}>
            <div style={{ padding: "12px 14px", borderRadius: "10px", background: "var(--surface-muted)", border: "1px solid var(--border)" }}><span className="muted">Rule Proposal results</span><strong style={{ display: "block", marginTop: "4px", fontSize: "20px" }}>{analysis.graph2.summary.total}</strong></div>
            <div style={{ padding: "12px 14px", borderRadius: "10px", background: "var(--surface-muted)", border: "1px solid var(--border)" }}><span className="muted">Anomaly Detection signals</span><strong style={{ display: "block", marginTop: "4px", fontSize: "20px" }}>{analysis.graph3.signals.length}</strong></div>
            <div style={{ padding: "12px 14px", borderRadius: "10px", background: "var(--surface-muted)", border: "1px solid var(--border)" }}><span className="muted">Report</span><strong style={{ display: "block", marginTop: "4px", fontSize: "14px" }}>{analysis.report.available ? (analysis.report.source || "AVAILABLE") : "PENDING"}</strong></div>
          </div>
          {analysis.report.available && <details style={{ marginTop: "16px" }}><summary style={{ cursor: "pointer", fontWeight: 700 }}>{language === "vi" ? "Mở báo cáo Data Steward" : "Open Data Steward report"}</summary><div className="steward-report-content" style={{ marginTop: "12px", padding: "14px", borderRadius: "10px", background: "var(--surface-muted)", color: "var(--ink-soft)", fontSize: "13px", lineHeight: 1.55 }}><ReactMarkdown remarkPlugins={[remarkGfm]}>{analysis.report.markdown}</ReactMarkdown></div></details>}
        </section>
      )}

      {/* Visual Charts Grid */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: "24px",
          marginBottom: "24px",
        }}
      >
        {/* Quality Score Trend (Area Chart) */}
        <div
          className="panel"
          style={{
            background: "var(--surface)",
            padding: "20px 24px",
            borderRadius: "16px",
            border: "1px solid var(--border)",
          }}
        >
          <h3 style={{ fontSize: "16px", fontWeight: 700, marginBottom: "4px", color: "var(--ink)" }}>
            {t("analytics.qualityTrend") || "Xu hướng điểm số chất lượng"}
          </h3>
          <p style={{ fontSize: "13px", color: "var(--muted)", margin: "0 0 16px 0" }}>
            {language === "vi" ? "Diễn biến chỉ số qua các đợt thực thi" : "Score progression across recent runs"}
          </p>

          <div style={{ height: "220px", width: "100%" }}>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={trendData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                <defs>
                  <linearGradient id="scoreGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#2563eb" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#2563eb" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border)" />
                <XAxis dataKey="run" tick={{ fontSize: 12, fill: "var(--muted)" }} />
                <YAxis domain={[0, 100]} tick={{ fontSize: 12, fill: "var(--muted)" }} />
                <Tooltip
                  contentStyle={{
                    background: "#1e293b",
                    color: "#fff",
                    borderRadius: "8px",
                    border: "none",
                    fontSize: "13px",
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="score"
                  stroke="#2563eb"
                  strokeWidth={3}
                  fillOpacity={1}
                  fill="url(#scoreGradient)"
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Rule Performance Distribution (Bar Chart) */}
        <div
          className="panel"
          style={{
            background: "var(--surface)",
            padding: "20px 24px",
            borderRadius: "16px",
            border: "1px solid var(--border)",
          }}
        >
          <h3 style={{ fontSize: "16px", fontWeight: 700, marginBottom: "4px", color: "var(--ink)" }}>
            {t("analytics.rulePerformance") || "Tỷ lệ Đạt theo Quy tắc (%)"}
          </h3>
          <p style={{ fontSize: "13px", color: "var(--muted)", margin: "0 0 16px 0" }}>
            {language === "vi" ? "Mức độ tuân thủ của từng quy tắc đã chạy" : "Compliance rate per executed rule"}
          </p>

          <div style={{ height: "220px", width: "100%" }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={ruleBreakdownData}
                layout="vertical"
                margin={{ top: 0, right: 20, left: 10, bottom: 0 }}
              >
                <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="var(--border)" />
                <XAxis type="number" domain={[0, 100]} tick={{ fontSize: 11, fill: "var(--muted)" }} />
                <YAxis dataKey="name" type="category" tick={{ fontSize: 11, fill: "var(--ink)" }} width={120} />
                <Tooltip
                  formatter={(val: any) => [`${Number(val ?? 0)}%`, language === "vi" ? "Tỷ lệ Đạt" : "Pass Rate"]}
                  contentStyle={{
                    background: "#1e293b",
                    color: "#fff",
                    borderRadius: "8px",
                    border: "none",
                    fontSize: "13px",
                  }}
                />
                <Bar dataKey="passRate" radius={[0, 4, 4, 0]}>
                  {ruleBreakdownData.map((entry, idx) => (
                    <Cell
                      key={`cell-${idx}`}
                      fill={entry.passRate >= 90 ? "#10b981" : entry.passRate >= 70 ? "#f59e0b" : "#ef4444"}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Detailed Results Table */}
      <div
        className="panel"
        style={{
          background: "var(--surface)",
          borderRadius: "16px",
          border: "1px solid var(--border)",
          padding: "24px",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
          <div>
            <h3 style={{ fontSize: "16px", fontWeight: 700, color: "var(--ink)", margin: 0 }}>
              {t("analytics.resultsTable") || "Chi tiết kết quả kiểm tra quy tắc"}
            </h3>
            <p style={{ fontSize: "13px", color: "var(--muted)", margin: "4px 0 0 0" }}>
              {language === "vi" ? "Danh sách chi tiết số dòng kiểm thử và vi phạm" : "Detailed list of checked and failing records per rule"}
            </p>
          </div>
          <span className="status-pill gray" style={{ fontSize: "12px" }}>
            {dashboardResults.length} {language === "vi" ? "quy tắc" : "rules"}
          </span>
        </div>

        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: "14px" }}>
            <thead>
              <tr style={{ borderBottom: "2px solid var(--border)", color: "var(--muted)", fontSize: "12px", textTransform: "uppercase" }}>
                <th style={{ padding: "12px 10px", whiteSpace: "nowrap" }}>{t("analytics.ruleTitle") || "TÊN QUY TẮC"}</th>
                <th style={{ padding: "12px 10px", whiteSpace: "nowrap" }}>{t("analytics.checked") || "ĐÃ KIỂM TRA"}</th>
                <th style={{ padding: "12px 10px", whiteSpace: "nowrap" }}>{t("analytics.failed") || "SỐ DÒNG LỖI"}</th>
                <th style={{ padding: "12px 10px", whiteSpace: "nowrap" }}>{language === "vi" ? "TỶ LỆ ĐẠT" : "PASS RATE"}</th>
                <th style={{ padding: "12px 10px", whiteSpace: "nowrap" }}>{t("analytics.status") || "TRẠNG THÁI"}</th>
              </tr>
            </thead>
            <tbody>
              {dashboardResults.length === 0 ? (
                <tr>
                  <td colSpan={5} style={{ padding: "24px", textAlign: "center", color: "var(--muted)" }}>
                    {t("analytics.noResults") || "Chưa có kết quả kiểm tra nào."}
                  </td>
                </tr>
              ) : (
                dashboardResults.map((res, i) => {
                  const passRate =
                    res.checked_count > 0
                      ? Number((((res.checked_count - res.failed_count) / res.checked_count) * 100).toFixed(2))
                      : res.status === "PASS" ? 100 : 0;
                  const passRateLabel = Number.isInteger(passRate)
                    ? `${passRate.toFixed(0)}%`
                    : `${passRate.toFixed(2)}%`;

                  return (
                    <tr key={i} style={{ borderBottom: "1px solid var(--border)", height: "48px" }}>
                      <td style={{ padding: "10px", fontWeight: 600, color: "var(--ink)" }}>
                        {res.rule_title}
                      </td>
                      <td style={{ padding: "10px", color: "var(--muted)" }}>
                        {res.checked_count ? res.checked_count.toLocaleString() : "—"}
                      </td>
                      <td style={{ padding: "10px", fontWeight: 600, color: res.failed_count > 0 ? "#dc2626" : "var(--ink)" }}>
                        {res.failed_count ? res.failed_count.toLocaleString() : 0}
                      </td>
                      <td style={{ padding: "10px", minWidth: "160px" }}>
                        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                          <div style={{ flex: 1, height: "6px", background: "rgba(0,0,0,0.06)", borderRadius: "3px", overflow: "hidden" }}>
                            <div
                              style={{
                                width: `${passRate}%`,
                                height: "100%",
                                background: passRate >= 90 ? "#10b981" : passRate >= 70 ? "#f59e0b" : "#ef4444",
                                borderRadius: "3px",
                              }}
                            />
                          </div>
                          <span style={{ fontSize: "12px", fontWeight: 700, width: "48px", textAlign: "right" }}>
                            {passRateLabel}
                          </span>
                        </div>
                      </td>
                      <td style={{ padding: "10px" }}>
                        <span
                          className={`status-pill ${res.status === "PASS" ? "success" : res.status === "FAIL" ? "danger" : "warning"}`}
                          style={{ fontSize: "11px", fontWeight: 700 }}
                        >
                          {res.status}
                        </span>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
