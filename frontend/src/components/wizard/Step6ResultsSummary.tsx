import React, { useMemo, useState } from "react";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Cell,
  ReferenceLine,
} from "recharts";
import type {
  AgentArtifact,
  Dataset,
  DatasetProfile,
  DqAnomaly,
  DqResult,
  DqRun,
  GraphCatalog,
  NodeRun,
  QualityTrendPoint,
  RuleConfiguration,
  RuleProposal,
  WorkflowRun,
} from "../../types";
import { formatDuration } from "../graph/NodeCard";
import { latestRunsByGraph } from "../graph/graphRunUtils";
import { graph3Decision } from "./graph3Outcome";

export interface Step6ResultsSummaryProps {
  dataset?: Dataset;
  profile?: DatasetProfile | null;
  workflow?: WorkflowRun | null;
  workflowArtifacts?: AgentArtifact[];
  understandingArtifact?: AgentArtifact;
  contractConfirmed?: boolean;
  proposals?: RuleProposal[];
  approvedRules?: RuleProposal[];
  ruleConfigurations?: RuleConfiguration[];
  activeRun?: DqRun | null;
  dqResults?: DqResult[];
  dqAnomalies?: DqAnomaly[];
  qualityTrends?: QualityTrendPoint[];
  graphCatalog?: GraphCatalog | null;
  workflowNodeRuns?: NodeRun[];
  language: "en" | "vi";
  onStartNewRun: () => void;
  onNavigateToStep: (step: number) => void;
  onOpenObservatory?: () => void;
}

export const Step6ResultsSummary: React.FC<Step6ResultsSummaryProps> = ({
  dataset,
  profile,
  workflow,
  workflowArtifacts = [],
  understandingArtifact,
  contractConfirmed = false,
  proposals = [],
  approvedRules = [],
  ruleConfigurations = [],
  activeRun,
  dqResults = [],
  dqAnomalies = [],
  qualityTrends = [],
  graphCatalog,
  workflowNodeRuns = [],
  language,
  onStartNewRun,
  onNavigateToStep,
  onOpenObservatory,
}) => {
  const isVi = language === "vi";
  const decision = graph3Decision(workflowArtifacts, activeRun?.id);
  const graph3NeedsReview = decision !== null && decision !== "NORMAL";
  const [tableFilter, setTableFilter] = useState<"ALL" | "FAIL" | "PASS">("ALL");

  // -------------------------------------------------------------------------
  // Quality Score Calculation
  // -------------------------------------------------------------------------
  const totalRules = dqResults.length;
  const passedRules = dqResults.filter((r) => r.status === "PASS").length;
  const failedRules = dqResults.filter((r) => r.status === "FAIL").length;
  const totalRowsChecked = dqResults.reduce((acc, r) => acc + (r.checked_count || 0), 0);
  const totalRowsFailed = dqResults.reduce((acc, r) => acc + (r.failed_count || 0), 0);

  const rulePassRate = totalRules > 0 ? (passedRules / totalRules) * 100 : null;
  const rowComplianceRate =
    totalRowsChecked > 0 ? Math.max(0, 1 - totalRowsFailed / totalRowsChecked) * 100 : 100;

  // Composite Quality Score: 70% row compliance + 30% rule pass rate
  const qualityScore = useMemo(() => {
    if (totalRules === 0) {
      if (qualityTrends.length > 0) {
        return Math.round(qualityTrends[qualityTrends.length - 1].quality_score);
      }
      return null;
    }
    if (totalRowsChecked > 0) {
      const combined = rowComplianceRate * 0.7 + (rulePassRate ?? 100) * 0.3;
      return Number(combined.toFixed(1));
    }
    return rulePassRate !== null ? Number(rulePassRate.toFixed(1)) : 100;
  }, [totalRules, totalRowsChecked, rowComplianceRate, rulePassRate, qualityTrends]);

  // Grade & Color tone
  const gradeInfo = useMemo(() => {
    if (qualityScore === null) {
      return {
        grade: "—",
        label: isVi ? "Chưa có lượt chạy" : "No Execution Yet",
        tone: "neutral",
        color: "var(--muted)",
        description: isVi
          ? "Hãy thực thi kiểm định ở Bước 4 để tính điểm chất lượng dữ liệu."
          : "Execute verification in Step 4 to compute the data quality score.",
      };
    }
    if (qualityScore >= 90) {
      return {
        grade: "A",
        label: isVi ? "Xuất sắc (Hạng A)" : "Excellent (Grade A)",
        tone: "good",
        color: "#10b981",
        description: isVi
          ? "Điểm cao trên bộ luật đã chạy. Xem các vi phạm và kết luận Graph 3 trước khi sử dụng dữ liệu."
          : "High score on the executed rules. Review violations and the Graph 3 decision before using the data.",
      };
    }
    if (qualityScore >= 75) {
      return {
        grade: "B",
        label: isVi ? "Đạt yêu cầu (Hạng B)" : "Good (Grade B)",
        tone: "good",
        color: "#2563eb",
        description: isVi
          ? "Phần lớn lượt kiểm tra đạt. Điểm tổng hợp không xác định mức độ nghiêm trọng của từng vi phạm."
          : "Most checks passed. The aggregate score does not determine the severity of individual violations.",
      };
    }
    if (qualityScore >= 50) {
      return {
        grade: "C",
        label: isVi ? "Cần chú ý (Hạng C)" : "Attention Needed (Grade C)",
        tone: "warning",
        color: "#f59e0b",
        description: isVi
          ? "Phát hiện nhiều vi phạm quy tắc hoặc bất thường thống kê cần xem xét và làm sạch."
          : "Several rule violations or anomalies detected. Data requires review and cleaning.",
      };
    }
    return {
      grade: "D",
      label: isVi ? "Rủi ro cao (Hạng D)" : "High Risk (Grade D)",
      tone: "bad",
      color: "#ef4444",
      description: isVi
        ? "Tỷ lệ lỗi cao hoặc vi phạm rào chắn nghiêm trọng. Không nên đưa vào pipeline dữ liệu sản xuất."
        : "High failure rate or critical guardrail breaches. Not recommended for production pipelines.",
    };
  }, [qualityScore, isVi]);

  // Sum only the newest invocation of each graph; retries/history must not
  // inflate the pipeline duration shown to the steward.
  const totalPipelineTimeMs = useMemo(() => {
    return latestRunsByGraph(workflowNodeRuns).reduce((sum, run) => sum + (run.duration_ms || 0), 0);
  }, [workflowNodeRuns]);
  const latestWorkflowNodeCount = latestRunsByGraph(workflowNodeRuns).length;

  // -------------------------------------------------------------------------
  // Visual Chart Data
  // -------------------------------------------------------------------------
  const historicalTrendsData = useMemo(() => {
    if (qualityTrends.length > 0) {
      return qualityTrends.slice(-10).map((pt, idx, arr) => ({
        runLabel:
          idx === arr.length - 1
            ? isVi ? "Hiện tại" : "Current"
            : new Date(pt.created_at).toLocaleDateString(isVi ? "vi-VN" : "en-US", {
                month: "short",
                day: "numeric",
              }),
        score: Math.round(pt.quality_score),
      }));
    }
    if (qualityScore !== null) {
      return [{ runLabel: isVi ? "Hiện tại" : "Current", score: qualityScore }];
    }
    return [];
  }, [qualityTrends, qualityScore, isVi]);

  // Filtered rules for audit table
  const filteredRules = useMemo(() => {
    let list = [...dqResults].sort((a, b) => {
      if (a.status === "FAIL" && b.status !== "FAIL") return -1;
      if (b.status === "FAIL" && a.status !== "FAIL") return 1;
      return (b.failed_count || 0) - (a.failed_count || 0);
    });
    if (tableFilter === "FAIL") return list.filter((r) => r.status === "FAIL");
    if (tableFilter === "PASS") return list.filter((r) => r.status === "PASS");
    return list;
  }, [dqResults, tableFilter]);

  // Column and row counts
  const columnCount = profile?.columns?.length || 0;
  const rowCount = profile?.row_count || dataset?.row_count || 0;

  // SVG Gauge calculations (radius = 50, circumference = 2 * PI * 50 = 314.16)
  const gaugeCircumference = 314.16;
  const validScore = qualityScore !== null ? Math.min(100, Math.max(0, qualityScore)) : 0;
  const gaugeOffset = gaugeCircumference - (validScore / 100) * gaugeCircumference;

  return (
    <div className="results-summary-page">
      {/* Page Heading & Action Bar */}
      <div
        className="page-heading"
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          flexWrap: "wrap",
          gap: "16px",
          paddingBottom: "18px",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <div>
          <span className="eyebrow">
            {isVi ? "BƯỚC 6 · TỔNG KẾT TOÀN DIỆN WORKFLOW" : "STEP 6 · COMPREHENSIVE WORKFLOW SUMMARY"}
          </span>
          <h1 style={{ fontSize: "24px", fontWeight: 800, letterSpacing: "-0.02em", marginTop: "4px" }}>
            {isVi ? "Bảng Điểm & Đánh giá Chất lượng Dữ liệu" : "Data Quality Scorecard & Executive Findings"}
          </h1>
          <p style={{ marginTop: "4px", color: "var(--muted)", fontSize: "14px", maxWidth: "75ch" }}>
            {isVi
              ? `Báo cáo tổng hợp chất lượng toàn chu trình AI Agent kiểm định cho tập '${dataset?.name ?? "Tập dữ liệu"}'.`
              : `Comprehensive quality synthesis report across all AI agent verification stages for '${dataset?.name ?? "Dataset"}'.`}
          </p>
        </div>
        <div style={{ display: "flex", gap: "10px", flexWrap: "wrap", alignItems: "center" }}>
          <button
            type="button"
            className="button secondary"
            onClick={() => window.print()}
            title={isVi ? "In hoặc lưu báo cáo thành PDF" : "Print or save report as PDF"}
            style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="6 9 6 2 18 2 18 9" />
              <path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2" />
              <rect x="6" y="14" width="12" height="8" />
            </svg>
            {isVi ? "In / Xuất PDF" : "Print / PDF"}
          </button>
          <button
            type="button"
            className="button primary"
            onClick={onStartNewRun}
            style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M5 12h14" />
              <path d="m12 5 7 7-7 7" />
            </svg>
            {isVi ? "Bắt đầu Lượt chạy mới" : "Start New Run"}
          </button>
        </div>
      </div>

      <div className="datasets-page" style={{ marginTop: "24px", display: "flex", flexDirection: "column", gap: "24px" }}>
        {/* ================================================================= */}
        {/* SECTION 1: EXECUTIVE QUALITY SCORECARD (HERO KPI) */}
        {/* ================================================================= */}
        <section
          className="panel results-hero-panel"
          style={{
            padding: "28px",
            background: "linear-gradient(135deg, var(--surface) 0%, color-mix(in srgb, var(--accent) 3%, var(--surface)) 100%)",
            border: "1px solid var(--border)",
            borderRadius: "14px",
            boxShadow: "0 4px 20px rgba(0, 0, 0, 0.03)",
          }}
        >
          <div className="results-score-grid" style={{ display: "grid", gridTemplateColumns: "minmax(320px, 1.2fr) 2fr", gap: "32px", alignItems: "center" }}>
            {/* Score Wheel / Hero Display */}
            <div className="results-score-card">
              <span className="eyebrow" style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                <span style={{ display: "inline-block", width: "8px", height: "8px", borderRadius: "50%", background: gradeInfo.color }} />
                {isVi ? "ĐIỂM CHẤT LƯỢNG TỔNG HỢP (DATA QUALITY SCORE)" : "COMPOSITE DATA QUALITY SCORE (DQ SCORE)"}
              </span>
              <div className="results-score-display" style={{ display: "flex", alignItems: "center", gap: "24px", marginTop: "8px" }}>
                {/* SVG Circular Gauge */}
                <div style={{ position: "relative", width: "130px", height: "130px", flexShrink: 0 }}>
                  <svg width="130" height="130" viewBox="0 0 120 120" style={{ transform: "rotate(-90deg)" }}>
                    <circle
                      cx="60"
                      cy="60"
                      r="50"
                      fill="none"
                      stroke="var(--border)"
                      strokeWidth="10"
                      opacity="0.4"
                    />
                    <circle
                      cx="60"
                      cy="60"
                      r="50"
                      fill="none"
                      stroke={gradeInfo.color}
                      strokeWidth="10"
                      strokeDasharray={gaugeCircumference}
                      strokeDashoffset={gaugeOffset}
                      strokeLinecap="round"
                      style={{ transition: "stroke-dashoffset 0.8s ease-in-out" }}
                    />
                  </svg>
                  <div
                    style={{
                      position: "absolute",
                      top: 0,
                      left: 0,
                      width: "100%",
                      height: "100%",
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "center",
                      justifyContent: "center",
                      pointerEvents: "none",
                    }}
                  >
                    <span style={{ fontSize: "28px", fontWeight: 800, color: gradeInfo.color, lineHeight: 1 }}>
                      {qualityScore === null ? "—" : `${qualityScore}%`}
                    </span>
                    <span
                      style={{
                        fontSize: "11px",
                        fontWeight: 700,
                        background: gradeInfo.color,
                        color: "#ffffff",
                        padding: "2px 8px",
                        borderRadius: "999px",
                        marginTop: "6px",
                        textTransform: "uppercase",
                        letterSpacing: "0.04em",
                      }}
                    >
                      {isVi ? `Hạng ${gradeInfo.grade}` : `Grade ${gradeInfo.grade}`}
                    </span>
                  </div>
                </div>

                {/* Verdict Text & Mini Sub-bars */}
                <div className="results-score-verdict" style={{ flex: 1 }}>
                  <h3 style={{ margin: "0 0 6px 0", fontSize: "19px", fontWeight: 800, color: gradeInfo.color }}>
                    {gradeInfo.label}
                  </h3>
                  <p className="muted" style={{ margin: 0, fontSize: "13.5px", lineHeight: "1.5" }}>
                    {gradeInfo.description}
                  </p>

                  {/* Dual Compliance Progress Bars */}
                  {qualityScore !== null && (
                    <div style={{ display: "flex", flexDirection: "column", gap: "8px", marginTop: "14px" }}>
                      <div>
                        <div style={{ display: "flex", justifyContent: "space-between", fontSize: "11.5px", marginBottom: "3px" }}>
                          <span style={{ color: "var(--muted)" }}>{isVi ? "Tuân thủ cấp dòng (70% trọng số):" : "Row compliance (70% weight):"}</span>
                          <strong style={{ color: "var(--ink)", fontVariantNumeric: "tabular-nums" }}>{rowComplianceRate.toFixed(1)}%</strong>
                        </div>
                        <div style={{ height: "5px", borderRadius: "999px", background: "var(--border)", overflow: "hidden" }}>
                          <div
                            style={{
                              width: `${rowComplianceRate}%`,
                              height: "100%",
                              background: rowComplianceRate >= 90 ? "#10b981" : rowComplianceRate >= 75 ? "#2563eb" : "#f59e0b",
                              borderRadius: "999px",
                            }}
                          />
                        </div>
                      </div>
                      <div>
                        <div style={{ display: "flex", justifyContent: "space-between", fontSize: "11.5px", marginBottom: "3px" }}>
                          <span style={{ color: "var(--muted)" }}>{isVi ? "Quy tắc vượt qua (30% trọng số):" : "Rule pass rate (30% weight):"}</span>
                          <strong style={{ color: "var(--ink)", fontVariantNumeric: "tabular-nums" }}>
                            {rulePassRate !== null ? `${rulePassRate.toFixed(1)}%` : "—"}
                          </strong>
                        </div>
                        <div style={{ height: "5px", borderRadius: "999px", background: "var(--border)", overflow: "hidden" }}>
                          <div
                            style={{
                              width: `${rulePassRate ?? 0}%`,
                              height: "100%",
                              background: (rulePassRate ?? 0) >= 90 ? "#10b981" : (rulePassRate ?? 0) >= 75 ? "#2563eb" : "#f59e0b",
                              borderRadius: "999px",
                            }}
                          />
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>

            {/* 4 Supporting Metric KPI Cards */}
            <div className="results-kpi-quad" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: "14px" }}>
              {/* Card 1: Rules */}
              <div className="results-kpi-box" style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "12px", padding: "14px 16px", display: "flex", flexDirection: "column", gap: "6px", boxShadow: "0 1px 4px rgba(0,0,0,0.03)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span className="eyebrow" style={{ fontSize: "11px" }}>{isVi ? "QUY TẮC KIỂM ĐỊNH" : "RULES EVALUATED"}</span>
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke={failedRules > 0 ? "#ef4444" : "#10b981"} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
                    <path d="m9 12 2 2 4-4" />
                  </svg>
                </div>
                <div className="results-kpi-val" style={{ display: "flex", alignItems: "baseline", gap: "6px" }}>
                  <strong style={{ fontSize: "22px", fontWeight: 800, color: failedRules > 0 ? "var(--ink)" : "#10b981" }}>
                    {passedRules}/{totalRules}
                  </strong>
                  <span className="results-kpi-sub" style={{ fontSize: "12px", color: "var(--muted)", fontWeight: 600 }}>
                    {totalRules > 0 ? `${((passedRules / totalRules) * 100).toFixed(0)}% ${isVi ? "đạt" : "pass"}` : "—"}
                  </span>
                </div>
                <small className="muted" style={{ fontSize: "11.5px", lineHeight: "1.4" }}>
                  {failedRules > 0
                    ? isVi ? `${failedRules} quy tắc có lỗi vi phạm` : `${failedRules} rules failed`
                    : isVi ? "Tất cả quy tắc đều vượt qua" : "All rules passed"}
                </small>
              </div>

              {/* Card 2: Rows Checked */}
              <div className="results-kpi-box" style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "12px", padding: "14px 16px", display: "flex", flexDirection: "column", gap: "6px", boxShadow: "0 1px 4px rgba(0,0,0,0.03)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span className="eyebrow" style={{ fontSize: "11px" }}>{isVi ? "LƯỢT KIỂM TRA DÒNG" : "ROW CHECKS"}</span>
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                    <ellipse cx="12" cy="5" rx="9" ry="3" />
                    <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
                    <path d="M3 12c0 1.66 4 3 9 3s9-1.34 9-3" />
                  </svg>
                </div>
                <div className="results-kpi-val" style={{ display: "flex", alignItems: "baseline", gap: "6px" }}>
                  <strong style={{ fontSize: "22px", fontWeight: 800 }}>
                    {totalRowsChecked > 0 ? totalRowsChecked.toLocaleString() : rowCount.toLocaleString()}
                  </strong>
                  <span className="results-kpi-sub" style={{ fontSize: "12px", color: "var(--muted)", fontWeight: 600 }}>
                    {isVi ? "lượt" : "checks"}
                  </span>
                </div>
                <small className="muted" style={{ fontSize: "11.5px", lineHeight: "1.4" }}>
                  {totalRowsFailed > 0
                    ? isVi ? `${totalRowsFailed.toLocaleString()} lượt vi phạm quy tắc` : `${totalRowsFailed.toLocaleString()} failed checks`
                    : isVi ? "0 lượt vi phạm" : "0 failed checks"}
                </small>
              </div>

              {/* Card 3: Anomalies */}
              <div className="results-kpi-box" style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "12px", padding: "14px 16px", display: "flex", flexDirection: "column", gap: "6px", boxShadow: "0 1px 4px rgba(0,0,0,0.03)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span className="eyebrow" style={{ fontSize: "11px" }}>{isVi ? "KẾT LUẬN GRAPH 3" : "GRAPH 3 DECISION"}</span>
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke={graph3NeedsReview ? "#f59e0b" : "var(--muted)"} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
                  </svg>
                </div>
                <div className="results-kpi-val" style={{ display: "flex", alignItems: "baseline", gap: "6px" }}>
                  <strong style={{ fontSize: "20px", fontWeight: 800, minWidth: 0, overflowWrap: "anywhere", color: graph3NeedsReview ? "#f59e0b" : "var(--text)" }}>
                    {decision?.replaceAll("_", " ") ?? "—"}
                  </strong>
                  <span className="results-kpi-sub" style={{ fontSize: "12px", color: "var(--muted)", fontWeight: 600 }}>
                  </span>
                </div>
                <small className="muted" style={{ fontSize: "11.5px", lineHeight: "1.4" }}>
                  {decision
                    ? isVi ? "Theo báo cáo của lượt thực thi này" : "From this execution's report"
                    : isVi ? "Chưa có báo cáo cho lượt thực thi này" : "No report for this execution yet"}
                </small>
              </div>

              {/* Card 4: Pipeline Duration */}
              <div className="results-kpi-box" style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "12px", padding: "14px 16px", display: "flex", flexDirection: "column", gap: "6px", boxShadow: "0 1px 4px rgba(0,0,0,0.03)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span className="eyebrow" style={{ fontSize: "11px" }}>{isVi ? "THỜI GIAN THỰC THI" : "PIPELINE DURATION"}</span>
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--muted)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="12" cy="12" r="10" />
                    <polyline points="12 6 12 12 16 14" />
                  </svg>
                </div>
                <div className="results-kpi-val" style={{ display: "flex", alignItems: "baseline", gap: "6px" }}>
                  <strong style={{ fontSize: "22px", fontWeight: 800 }}>
                    {formatDuration(totalPipelineTimeMs)}
                  </strong>
                </div>
                <small className="muted" style={{ fontSize: "11.5px", lineHeight: "1.4" }}>
                  {latestWorkflowNodeCount > 0
                    ? isVi ? `${latestWorkflowNodeCount} node agent qua 4 graph` : `${latestWorkflowNodeCount} agent nodes executed`
                    : isVi ? "Tổng thời gian pipeline" : "Total pipeline time"}
                </small>
              </div>
            </div>
          </div>
        </section>

        {/* ================================================================= */}
        {/* SECTION 2: WORKFLOW STAGE JOURNEY (CHRONOLOGICAL PIPELINE) */}
        {/* ================================================================= */}
        <section className="panel" style={{ padding: "24px" }}>
          <div className="panel-heading" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "12px" }}>
            <div>
              <span className="eyebrow">
                {isVi ? "HÀNH TRÌNH WORKFLOW · QUY TRÌNH 4 GRAPH" : "WORKFLOW JOURNEY · 4 GRAPH PHASES"}
              </span>
              <h2 style={{ fontSize: "18px", fontWeight: 700, marginTop: "4px" }}>
                {isVi ? "Tình trạng Thực thi qua từng Giai đoạn" : "Stage-by-Stage Execution Status"}
              </h2>
              <p className="muted" style={{ fontSize: "13px", marginTop: "4px" }}>
                {isVi
                  ? "Tổng hợp kết quả xử lý từ khâu hiểu ngữ nghĩa dữ liệu đến sinh quy tắc, thực thi và điều tra bất thường."
                  : "Summary of outcomes from semantic data understanding to rule engineering, execution, and root-cause analysis."}
              </p>
            </div>
          </div>

          <div className="results-stages-grid" style={{ marginTop: "20px", display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(250px, 1fr))", gap: "16px" }}>
            {/* Stage 1: Graph 1A */}
            <div
              className="results-stage-card"
              style={{
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: "12px",
                padding: "18px",
                display: "flex",
                flexDirection: "column",
                justifyContent: "space-between",
                gap: "12px",
                borderTop: "3px solid #2563eb",
              }}
            >
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                  <span style={{ fontSize: "11px", fontWeight: 800, color: "#2563eb", background: "color-mix(in srgb, #2563eb 10%, transparent)", padding: "2px 6px", borderRadius: "4px" }}>
                    PHASE 01 · G1A
                  </span>
                  <span className={`status-pill ${contractConfirmed ? "success" : understandingArtifact ? "info" : "neutral"}`}>
                    <span className="status-dot" />
                    {contractConfirmed
                      ? isVi ? "Đã xác nhận" : "Confirmed"
                      : understandingArtifact
                        ? isVi ? "Đã phân tích" : "Analyzed"
                        : isVi ? "Chưa chạy" : "Pending"}
                  </span>
                </div>
                <h4 style={{ margin: "0 0 6px 0", fontSize: "15px", fontWeight: 700 }}>
                  {isVi ? "Hiểu Ngữ nghĩa Dữ liệu" : "Semantic Understanding"}
                </h4>
                <p className="muted" style={{ fontSize: "12.5px", lineHeight: "1.5", margin: 0 }}>
                  {isVi
                    ? `AI Agent đã quét ${columnCount} cột, nhận diện kiểu ngữ nghĩa và vai trò kinh doanh của bảng dữ liệu.`
                    : `AI Agent scanned ${columnCount} columns and mapped semantic types and business roles.`}
                </p>
              </div>
              <div style={{ borderTop: "1px solid var(--border)", paddingTop: "10px", marginTop: "auto" }}>
                <button
                  type="button"
                  className="button ghost small"
                  onClick={() => onNavigateToStep(2)}
                  style={{ width: "100%", justifyContent: "space-between", fontSize: "12px", color: "var(--accent)" }}
                >
                  {isVi ? "Xem Hợp đồng Ngữ nghĩa" : "View Semantic Contract"}
                  <span>→</span>
                </button>
              </div>
            </div>

            {/* Stage 2: Graph 1B */}
            <div
              className="results-stage-card"
              style={{
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: "12px",
                padding: "18px",
                display: "flex",
                flexDirection: "column",
                justifyContent: "space-between",
                gap: "12px",
                borderTop: "3px solid #7c3aed",
              }}
            >
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                  <span style={{ fontSize: "11px", fontWeight: 800, color: "#7c3aed", background: "color-mix(in srgb, #7c3aed 10%, transparent)", padding: "2px 6px", borderRadius: "4px" }}>
                    PHASE 02 · G1B
                  </span>
                  <span className={`status-pill ${approvedRules.length > 0 ? "success" : proposals.length > 0 ? "info" : "neutral"}`}>
                    <span className="status-dot" />
                    {approvedRules.length > 0
                      ? isVi ? `${approvedRules.length} đã duyệt` : `${approvedRules.length} approved`
                      : proposals.length > 0
                        ? isVi ? `${proposals.length} đề xuất` : `${proposals.length} proposed`
                        : isVi ? "Chưa có quy tắc" : "No rules"}
                  </span>
                </div>
                <h4 style={{ margin: "0 0 6px 0", fontSize: "15px", fontWeight: 700 }}>
                  {isVi ? "Kỹ nghệ Quy tắc (HITL)" : "Rule Engineering (HITL)"}
                </h4>
                <p className="muted" style={{ fontSize: "12.5px", lineHeight: "1.5", margin: 0 }}>
                  {isVi
                    ? `${proposals.length} quy tắc được chuẩn bị; ${approvedRules.length} quy tắc đã duyệt.`
                    : `${proposals.length} rules prepared; ${approvedRules.length} rules approved.`}
                </p>
              </div>
              <div style={{ borderTop: "1px solid var(--border)", paddingTop: "10px", marginTop: "auto" }}>
                <button
                  type="button"
                  className="button ghost small"
                  onClick={() => onNavigateToStep(3)}
                  style={{ width: "100%", justifyContent: "space-between", fontSize: "12px", color: "var(--accent)" }}
                >
                  {isVi ? "Xem Danh sách Quy tắc" : "View Ruleset"}
                  <span>→</span>
                </button>
              </div>
            </div>

            {/* Stage 3: Graph 2 */}
            <div
              className="results-stage-card"
              style={{
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: "12px",
                padding: "18px",
                display: "flex",
                flexDirection: "column",
                justifyContent: "space-between",
                gap: "12px",
                borderTop: "3px solid #059669",
              }}
            >
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                  <span style={{ fontSize: "11px", fontWeight: 800, color: "#059669", background: "color-mix(in srgb, #059669 10%, transparent)", padding: "2px 6px", borderRadius: "4px" }}>
                    PHASE 03 · G2
                  </span>
                  <span className={`status-pill ${activeRun ? (failedRules === 0 ? "success" : "warning") : "neutral"}`}>
                    <span className="status-dot" />
                    {activeRun
                      ? isVi ? `Đã chạy (${passedRules}/${totalRules})` : `Executed (${passedRules}/${totalRules})`
                      : isVi ? "Chưa thực thi" : "Not executed"}
                  </span>
                </div>
                <h4 style={{ margin: "0 0 6px 0", fontSize: "15px", fontWeight: 700 }}>
                  {isVi ? "Thực thi & Kiểm định" : "Deterministic Execution"}
                </h4>
                <p className="muted" style={{ fontSize: "12.5px", lineHeight: "1.5", margin: 0 }}>
                  {isVi
                    ? `Biên dịch sang dbt / SQL runner, thực hiện ${totalRowsChecked.toLocaleString()} lượt kiểm tra qua ${totalRules} quy tắc.`
                    : `Compiled to dbt / SQL runner, performed ${totalRowsChecked.toLocaleString()} row checks across ${totalRules} rules.`}
                </p>
              </div>
              <div style={{ borderTop: "1px solid var(--border)", paddingTop: "10px", marginTop: "auto" }}>
                <button
                  type="button"
                  className="button ghost small"
                  onClick={() => onNavigateToStep(4)}
                  style={{ width: "100%", justifyContent: "space-between", fontSize: "12px", color: "var(--accent)" }}
                >
                  {isVi ? "Xem Chi tiết Thực thi" : "View Execution Results"}
                  <span>→</span>
                </button>
              </div>
            </div>

            {/* Stage 4: Graph 3 */}
            <div
              className="results-stage-card"
              style={{
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: "12px",
                padding: "18px",
                display: "flex",
                flexDirection: "column",
                justifyContent: "space-between",
                gap: "12px",
                borderTop: "3px solid #d97706",
              }}
            >
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                  <span style={{ fontSize: "11px", fontWeight: 800, color: "#d97706", background: "color-mix(in srgb, #d97706 10%, transparent)", padding: "2px 6px", borderRadius: "4px" }}>
                    PHASE 04 · G3
                  </span>
                  <span className={`status-pill ${decision === "NORMAL" ? "success" : "warning"}`}>
                    <span className="status-dot" />
                    {decision ?? (isVi ? "Chưa phân tích" : "Not analysed")}
                  </span>
                </div>
                <h4 style={{ margin: "0 0 6px 0", fontSize: "15px", fontWeight: 700 }}>
                  {isVi ? "Bất thường & Nguyên nhân gốc" : "Anomalies & Root Cause"}
                </h4>
                <p className="muted" style={{ fontSize: "12.5px", lineHeight: "1.5", margin: 0 }}>
                  {isVi
                    ? `Kết luận: ${decision ?? "chưa có"}. Bảng thống kê có ${dqAnomalies.length} tín hiệu sau lọc. Xem báo cáo để đọc bằng chứng và giới hạn phân tích.`
                    : `Decision: ${decision ?? "pending"}. The statistics table contains ${dqAnomalies.length} signals after filtering. See the report for evidence and analysis limitations.`}
                </p>
              </div>
              <div style={{ borderTop: "1px solid var(--border)", paddingTop: "10px", marginTop: "auto" }}>
                <button
                  type="button"
                  className="button ghost small"
                  onClick={() => onNavigateToStep(5)}
                  style={{ width: "100%", justifyContent: "space-between", fontSize: "12px", color: "var(--accent)" }}
                >
                  {isVi ? "Xem Báo cáo Điều tra" : "View Investigation"}
                  <span>→</span>
                </button>
              </div>
            </div>
          </div>
        </section>

        {/* ================================================================= */}
        {/* SECTION 3: VISUAL CHARTS & TREND ANALYSIS */}
        {/* ================================================================= */}
        <div className="results-visuals-grid" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "24px" }}>
          {/* Chart 1: Rule Outcome Distribution */}
          <section className="panel" style={{ padding: "24px" }}>
            <div className="panel-heading" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
              <div>
                <span className="eyebrow">{isVi ? "PHÂN BỔ QUY TẮC" : "RULE DISTRIBUTION"}</span>
                <h3 style={{ fontSize: "17px", fontWeight: 700, marginTop: "4px" }}>
                  {isVi ? "Tỷ lệ Đạt / Vi phạm Quy tắc" : "Rule Pass vs Fail Breakdown"}
                </h3>
              </div>
              <div style={{ textAlign: "right" }}>
                <strong style={{ fontSize: "18px", color: failedRules > 0 ? "#f59e0b" : "#10b981" }}>
                  {rulePassRate !== null ? `${rulePassRate.toFixed(0)}%` : "—"}
                </strong>
                <div style={{ fontSize: "11.5px", color: "var(--muted)" }}>{isVi ? "Tỷ lệ đạt chuẩn" : "Pass Rate"}</div>
              </div>
            </div>

            <div style={{ height: "240px", marginTop: "16px" }}>
              {totalRules === 0 ? (
                <div className="workflow-artifact-empty" style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center" }}>
                  {isVi ? "Chưa có kết quả kiểm định để vẽ biểu đồ." : "No rule results available to graph."}
                </div>
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={[
                      {
                        name: isVi ? "Đạt chuẩn" : "Passed",
                        count: passedRules,
                      },
                      {
                        name: isVi ? "Vi phạm" : "Failed",
                        count: failedRules,
                      },
                    ]}
                    margin={{ top: 10, right: 10, left: -20, bottom: 0 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" opacity={0.2} vertical={false} />
                    <XAxis dataKey="name" stroke="currentColor" fontSize={12} tickLine={false} />
                    <YAxis allowDecimals={false} stroke="currentColor" fontSize={12} tickLine={false} />
                    <Tooltip
                      cursor={{ fill: "color-mix(in srgb, var(--border) 25%, transparent)" }}
                      contentStyle={{
                        background: "var(--surface)",
                        borderColor: "var(--border)",
                        borderRadius: "8px",
                        boxShadow: "0 4px 12px rgba(0,0,0,0.08)",
                        fontSize: "13px",
                      }}
                    />
                    <Bar dataKey="count" radius={[6, 6, 0, 0]} maxBarSize={60}>
                      <Cell fill="#10b981" />
                      <Cell fill="#ef4444" />
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              )}
            </div>
          </section>

          {/* Chart 2: Quality Trend Over Time (Area Chart with Gradient) */}
          <section className="panel" style={{ padding: "24px" }}>
            <div className="panel-heading" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
              <div>
                <span className="eyebrow">{isVi ? "XU HƯỚNG CHẤT LƯỢNG" : "QUALITY TREND"}</span>
                <h3 style={{ fontSize: "17px", fontWeight: 700, marginTop: "4px" }}>
                  {isVi ? "Biến động Điểm Chất lượng Dữ liệu" : "Data Quality Score Trajectory"}
                </h3>
              </div>
              <div style={{ textAlign: "right" }}>
                <strong style={{ fontSize: "18px", color: "var(--accent)" }}>
                  {qualityScore !== null ? `${qualityScore}%` : "—"}
                </strong>
                <div style={{ fontSize: "11.5px", color: "var(--muted)" }}>{isVi ? "Điểm hiện tại" : "Latest Score"}</div>
              </div>
            </div>

            <div style={{ height: "240px", marginTop: "16px" }}>
              {historicalTrendsData.length === 0 ? (
                <div className="workflow-artifact-empty" style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center" }}>
                  {isVi ? "Chưa có dữ liệu lịch sử các lượt chạy." : "No trend history recorded yet."}
                </div>
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={historicalTrendsData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                    <defs>
                      <linearGradient id="scoreTrendGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#2563eb" stopOpacity={0.35} />
                        <stop offset="95%" stopColor="#2563eb" stopOpacity={0.0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" opacity={0.2} vertical={false} />
                    <XAxis dataKey="runLabel" stroke="currentColor" fontSize={12} tickLine={false} />
                    <YAxis domain={[0, 100]} stroke="currentColor" fontSize={12} tickLine={false} />
                    <ReferenceLine y={80} stroke="#10b981" strokeDasharray="3 3" label={{ value: isVi ? "Mục tiêu (80%)" : "Benchmark", fill: "#10b981", fontSize: 11 }} />
                    <Tooltip
                      formatter={(val: any) => [`${val}%`, isVi ? "Điểm DQ" : "DQ Score"]}
                      contentStyle={{
                        background: "var(--surface)",
                        borderColor: "var(--border)",
                        borderRadius: "8px",
                        boxShadow: "0 4px 12px rgba(0,0,0,0.08)",
                        fontSize: "13px",
                      }}
                    />
                    <Area
                      type="monotone"
                      dataKey="score"
                      stroke="#2563eb"
                      strokeWidth={2.5}
                      fill="url(#scoreTrendGrad)"
                      dot={{ r: 4, fill: "#2563eb" }}
                      activeDot={{ r: 6 }}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              )}
            </div>
          </section>
        </div>

        {/* ================================================================= */}
        {/* SECTION 4: DETAILED RULE OBSERVATIONS TABLE WITH FILTERS */}
        {/* ================================================================= */}
        <section className="panel" style={{ padding: "24px" }}>
          <div className="panel-heading" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "12px" }}>
            <div>
              <span className="eyebrow">{isVi ? "BẢNG KIỂM TOÁN CHI TIẾT" : "DETAILED FINDINGS"}</span>
              <h2 style={{ fontSize: "18px", fontWeight: 700, marginTop: "4px" }}>
                {isVi ? "Bảng Theo dõi Quy tắc & Vi phạm Dữ liệu" : "Ruleset & Violation Audit Table"}
              </h2>
              <p className="muted" style={{ fontSize: "13px", marginTop: "4px" }}>
                {isVi
                  ? `Đang hiển thị ${filteredRules.length}/${totalRules} quy tắc kiểm định trong lượt chạy này.`
                  : `Showing ${filteredRules.length}/${totalRules} evaluated assertions in this run.`}
              </p>
            </div>

            {/* Filter Pills */}
            <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
              <button
                type="button"
                className={`button small ${tableFilter === "ALL" ? "primary" : "secondary"}`}
                onClick={() => setTableFilter("ALL")}
              >
                {isVi ? `Tất cả (${totalRules})` : `All (${totalRules})`}
              </button>
              <button
                type="button"
                className={`button small ${tableFilter === "FAIL" ? "primary" : "secondary"}`}
                onClick={() => setTableFilter("FAIL")}
                style={{ color: tableFilter === "FAIL" ? undefined : failedRules > 0 ? "#ef4444" : undefined }}
              >
                {isVi ? `Vi phạm (${failedRules})` : `Failed (${failedRules})`}
              </button>
              <button
                type="button"
                className={`button small ${tableFilter === "PASS" ? "primary" : "secondary"}`}
                onClick={() => setTableFilter("PASS")}
                style={{ color: tableFilter === "PASS" ? undefined : "#10b981" }}
              >
                {isVi ? `Đạt (${passedRules})` : `Passed (${passedRules})`}
              </button>
            </div>
          </div>

          <div className="analytics-table-scroll" style={{ marginTop: "16px", overflowX: "auto" }}>
            <table className="analytics-table" style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  <th style={{ textAlign: "left" }}>{isVi ? "Tên Quy tắc & Mã nhận diện" : "Rule Assertion & ID"}</th>
                  <th style={{ textAlign: "right" }}>{isVi ? "Bản ghi quét" : "Rows Audited"}</th>
                  <th style={{ textAlign: "right" }}>{isVi ? "Dòng vi phạm" : "Failed Rows"}</th>
                  <th style={{ textAlign: "center", width: "160px" }}>{isVi ? "Tỷ lệ vi phạm" : "Failure Rate"}</th>
                  <th style={{ textAlign: "center", width: "110px" }}>{isVi ? "Trạng thái" : "Status"}</th>
                </tr>
              </thead>
              <tbody>
                {filteredRules.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="empty-cell" style={{ textAlign: "center", padding: "32px", color: "var(--muted)" }}>
                      {isVi
                        ? "Không tìm thấy quy tắc nào khớp với bộ lọc đã chọn."
                        : "No rules match the selected filter criteria."}
                    </td>
                  </tr>
                ) : (
                  filteredRules.map((rule) => {
                    const failRateNum =
                      rule.checked_count > 0 ? (rule.failed_count / rule.checked_count) * 100 : 0;
                    const failRateStr = failRateNum.toFixed(2);
                    const isFail = rule.status === "FAIL";
                    return (
                      <tr key={rule.rule_id} style={{ borderBottom: "1px solid var(--border)" }}>
                        <td style={{ padding: "12px 14px" }}>
                          <strong style={{ fontSize: "13.5px", color: "var(--ink)" }}>{rule.rule_title}</strong>
                          <div className="muted" style={{ fontSize: "11px", fontFamily: "monospace", marginTop: "2px" }}>
                            {rule.rule_id}
                          </div>
                        </td>
                        <td style={{ textAlign: "right", padding: "12px 14px", fontVariantNumeric: "tabular-nums", color: "var(--muted)" }}>
                          {rule.checked_count?.toLocaleString() ?? "—"}
                        </td>
                        <td
                          style={{
                            textAlign: "right",
                            padding: "12px 14px",
                            fontVariantNumeric: "tabular-nums",
                            color: isFail ? "#ef4444" : "var(--ink)",
                            fontWeight: isFail ? 700 : "normal",
                          }}
                        >
                          {rule.failed_count?.toLocaleString() ?? 0}
                        </td>
                        <td style={{ padding: "12px 14px" }}>
                          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                            <div style={{ flex: 1, height: "6px", background: "var(--border)", borderRadius: "999px", overflow: "hidden" }}>
                              <div
                                style={{
                                  width: `${Math.min(100, Math.max(0, failRateNum))}%`,
                                  height: "100%",
                                  background: isFail ? "#ef4444" : "#10b981",
                                  borderRadius: "999px",
                                }}
                              />
                            </div>
                            <span
                              style={{
                                fontSize: "12px",
                                fontWeight: 600,
                                minWidth: "46px",
                                textAlign: "right",
                                fontVariantNumeric: "tabular-nums",
                                color: isFail ? "#ef4444" : "#10b981",
                              }}
                            >
                              {failRateStr}%
                            </span>
                          </div>
                        </td>
                        <td style={{ textAlign: "center", padding: "12px 14px" }}>
                          <span className={`status-pill ${!isFail ? "success" : "danger"}`}>
                            <span className="status-dot" />
                            {!isFail ? (isVi ? "ĐẠT" : "PASS") : (isVi ? "LỖI" : "FAIL")}
                          </span>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </section>

        {/* ================================================================= */}
        {/* SECTION 5: AI STEWARD ACTIONABLE GUIDANCE & NEXT STEPS */}
        {/* ================================================================= */}
        <section className="panel" style={{ padding: "24px" }}>
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{isVi ? "KHUYẾN NGHỊ & KẾ HOẠCH HÀNH ĐỘNG" : "ACTIONABLE AI GUIDANCE & NEXT STEPS"}</span>
              <h2 style={{ fontSize: "18px", fontWeight: 700, marginTop: "4px" }}>
                {isVi ? "Đề xuất Xử lý từ AI Data Steward" : "Steward Action Plan & Pipeline Hardening"}
              </h2>
              <p className="muted" style={{ fontSize: "13px", marginTop: "4px" }}>
                {isVi
                  ? "Các bước đề xuất cụ thể để hoàn thiện pipeline và đảm bảo độ tin cậy của tập dữ liệu."
                  : "Targeted operational recommendations to elevate data quality and harden pipeline integrity."}
              </p>
            </div>
          </div>

          <div className="results-recommendations-list" style={{ marginTop: "18px", display: "grid", gap: "14px" }}>
            {totalRules > 0 && passedRules === totalRules && decision === "NORMAL" && dqAnomalies.length === 0 ? (
              <div
                className="results-rec-item success"
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: "14px",
                  padding: "16px",
                  borderRadius: "10px",
                  border: "1px solid var(--border)",
                  borderLeft: "4px solid #10b981",
                  background: "var(--surface)",
                }}
              >
                <div style={{ color: "#10b981", flexShrink: 0, marginTop: "2px" }}>
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
                    <polyline points="22 4 12 14.01 9 11.01" />
                  </svg>
                </div>
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    <strong style={{ fontSize: "14.5px" }}>
                      {isVi ? "Dữ liệu đạt chuẩn toàn diện" : "Perfect Data Quality Compliance"}
                    </strong>
                    <span style={{ fontSize: "11px", fontWeight: 700, background: "color-mix(in srgb, #10b981 15%, transparent)", color: "#10b981", padding: "1px 7px", borderRadius: "4px" }}>
                      {isVi ? "SẴN SÀNG SẢN XUẤT" : "PRODUCTION READY"}
                    </span>
                  </div>
                  <p className="muted" style={{ margin: "4px 0 0 0", fontSize: "13px", lineHeight: "1.5" }}>
                    {isVi
                      ? "Tất cả quy tắc kiểm tra đều vượt qua với 0 dòng lỗi. Bạn có thể tự tin sử dụng tập dữ liệu này cho các mô hình phân tích hạ nguồn và bảng điều khiển báo cáo."
                      : "All assertions passed with 0 failing records. The dataset is ready for downstream analytical pipelines and ML model ingestion."}
                  </p>
                </div>
              </div>
            ) : (
              <>
                {failedRules > 0 && (
                  <div
                    className="results-rec-item warning"
                    style={{
                      display: "flex",
                      alignItems: "flex-start",
                      justifyContent: "space-between",
                      gap: "14px",
                      padding: "16px",
                      borderRadius: "10px",
                      border: "1px solid var(--border)",
                      borderLeft: "4px solid #f59e0b",
                      background: "var(--surface)",
                      flexWrap: "wrap",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "flex-start", gap: "14px", flex: 1, minWidth: "280px" }}>
                      <div style={{ color: "#f59e0b", flexShrink: 0, marginTop: "2px" }}>
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                          <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" />
                          <line x1="12" y1="9" x2="12" y2="13" />
                          <line x1="12" y1="17" x2="12.01" y2="17" />
                        </svg>
                      </div>
                      <div>
                        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                          <strong style={{ fontSize: "14.5px" }}>
                            {isVi ? `Xử lý ${failedRules} quy tắc có bản ghi vi phạm` : `Remediate ${failedRules} failing assertions`}
                          </strong>
                          <span style={{ fontSize: "11px", fontWeight: 700, background: "color-mix(in srgb, #f59e0b 15%, transparent)", color: "#b45309", padding: "1px 7px", borderRadius: "4px" }}>
                            {isVi ? "ƯU TIÊN CAO" : "HIGH PRIORITY"}
                          </span>
                        </div>
                        <p className="muted" style={{ margin: "4px 0 0 0", fontSize: "13px", lineHeight: "1.5" }}>
                          {isVi
                            ? `Có ${totalRowsFailed.toLocaleString()} lượt vi phạm ràng buộc (một dòng có thể vi phạm nhiều quy tắc). Xem lại các cột tương ứng và quy tắc nghiệp vụ.`
                            : `${totalRowsFailed.toLocaleString()} checks violated assertions (one row can fail multiple rules). Review the affected columns and business rules.`}
                        </p>
                      </div>
                    </div>
                    <button
                      type="button"
                      className="button secondary small"
                      onClick={() => onNavigateToStep(3)}
                      style={{ alignSelf: "center", whiteSpace: "nowrap" }}
                    >
                      {isVi ? "Hiệu chỉnh Quy tắc →" : "Calibrate Rules →"}
                    </button>
                  </div>
                )}

                {(graph3NeedsReview || dqAnomalies.length > 0) && (
                  <div
                    className="results-rec-item info"
                    style={{
                      display: "flex",
                      alignItems: "flex-start",
                      justifyContent: "space-between",
                      gap: "14px",
                      padding: "16px",
                      borderRadius: "10px",
                      border: "1px solid var(--border)",
                      borderLeft: "4px solid #2563eb",
                      background: "var(--surface)",
                      flexWrap: "wrap",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "flex-start", gap: "14px", flex: 1, minWidth: "280px" }}>
                      <div style={{ color: "#2563eb", flexShrink: 0, marginTop: "2px" }}>
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                          <circle cx="12" cy="12" r="10" />
                          <line x1="12" y1="16" x2="12" y2="12" />
                          <line x1="12" y1="8" x2="12.01" y2="8" />
                        </svg>
                      </div>
                      <div>
                        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                          <strong style={{ fontSize: "14.5px" }}>
                            {isVi ? "Xem lại kết luận và bằng chứng Graph 3" : "Review the Graph 3 decision and evidence"}
                          </strong>
                          <span style={{ fontSize: "11px", fontWeight: 700, background: "color-mix(in srgb, #2563eb 15%, transparent)", color: "#1d4ed8", padding: "1px 7px", borderRadius: "4px" }}>
                            {isVi ? "ĐIỀU TRA NGUYÊN NHÂN" : "ROOT CAUSE"}
                          </span>
                        </div>
                        <p className="muted" style={{ margin: "4px 0 0 0", fontSize: "13px", lineHeight: "1.5" }}>
                          {isVi
                            ? "Xem các giả thuyết nguyên nhân gốc được agent suy luận và đọc Báo cáo Steward chi tiết ở Bước 5 để nắm hành động phòng ngừa."
                            : "Navigate to Step 5 to examine agent-inferred root-cause hypotheses and inspect the Markdown Steward report."}
                        </p>
                      </div>
                    </div>
                    <button
                      type="button"
                      className="button secondary small"
                      onClick={() => onNavigateToStep(5)}
                      style={{ alignSelf: "center", whiteSpace: "nowrap" }}
                    >
                      {isVi ? "Xem Báo cáo Điều tra →" : "View Report →"}
                    </button>
                  </div>
                )}
              </>
            )}

            {/* Always Recommended Operational Guardrail */}
            <div
              className="results-rec-item neutral"
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: "14px",
                padding: "16px",
                borderRadius: "10px",
                border: "1px solid var(--border)",
                borderLeft: "4px solid var(--muted)",
                background: "var(--surface)",
              }}
            >
              <div style={{ color: "var(--muted)", flexShrink: 0, marginTop: "2px" }}>
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21.5 2v6h-6" />
                  <path d="M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67" />
                </svg>
              </div>
              <div>
                <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <strong style={{ fontSize: "14.5px" }}>
                    {isVi ? "Thiết lập giám sát dữ liệu tự động định kỳ" : "Establish Continuous Quality Monitoring"}
                  </strong>
                  <span style={{ fontSize: "11px", fontWeight: 700, background: "var(--surface-muted)", color: "var(--muted)", padding: "1px 7px", borderRadius: "4px" }}>
                    {isVi ? "VẬN HÀNH DÀI HẠN" : "BEST PRACTICE"}
                  </span>
                </div>
                <p className="muted" style={{ margin: "4px 0 0 0", fontSize: "13px", lineHeight: "1.5" }}>
                  {isVi
                    ? "Tích hợp bộ quy tắc dbt đã phê duyệt vào lịch chạy tự động để liên tục theo dõi chất lượng các đợt nạp dữ liệu mới."
                    : "Integrate approved dbt rulesets into scheduled pipeline runs to safeguard subsequent data batches automatically."}
                </p>
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
};
