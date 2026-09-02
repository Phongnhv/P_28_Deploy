import React, { useMemo } from "react";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Cell,
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

  // -------------------------------------------------------------------------
  // Quality Score Calculation
  // -------------------------------------------------------------------------
  const totalRules = dqResults.length;
  const passedRules = dqResults.filter((r) => r.status === "PASS").length;
  const failedRules = dqResults.filter((r) => r.status === "FAIL").length;
  const totalRowsChecked = dqResults.reduce((acc, r) => acc + (r.checked_count || 0), 0);
  const totalRowsFailed = dqResults.reduce((acc, r) => acc + (r.failed_count || 0), 0);

  const rulePassRate = totalRules > 0 ? (passedRules / totalRules) * 100 : null;

  // Composite Quality Score
  const qualityScore = useMemo(() => {
    if (totalRules === 0) {
      if (qualityTrends.length > 0) {
        return Math.round(qualityTrends[qualityTrends.length - 1].quality_score);
      }
      return null;
    }
    if (totalRowsChecked > 0) {
      const rowPassRate = Math.max(0, 1 - totalRowsFailed / totalRowsChecked) * 100;
      // 70% row compliance + 30% rule pass rate
      const combined = (rowPassRate * 0.7) + ((rulePassRate ?? 100) * 0.3);
      return Number(combined.toFixed(1));
    }
    return rulePassRate !== null ? Number(rulePassRate.toFixed(1)) : 100;
  }, [totalRules, totalRowsChecked, totalRowsFailed, rulePassRate, qualityTrends]);

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
          ? "Dữ liệu đạt mức độ toàn vẹn và độ tin cậy rất cao, sẵn sàng phục vụ báo cáo và mô hình ML."
          : "Data exhibits very high integrity and reliability, production-ready for reporting and ML.",
      };
    }
    if (qualityScore >= 75) {
      return {
        grade: "B",
        label: isVi ? "Đạt yêu cầu (Hạng B)" : "Good (Grade B)",
        tone: "good",
        color: "#10b981",
        description: isVi
          ? "Dữ liệu đáp ứng phần lớn các tiêu chuẩn, chỉ có một số vi phạm nhỏ cần lưu ý."
          : "Data meets most quality benchmarks with minor non-critical violations.",
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

  // Top failing or critical rules
  const criticalRules = useMemo(() => {
    return [...dqResults]
      .sort((a, b) => {
        if (a.status === "FAIL" && b.status !== "FAIL") return -1;
        if (b.status === "FAIL" && a.status !== "FAIL") return 1;
        return (b.failed_count || 0) - (a.failed_count || 0);
      })
      .slice(0, 8);
  }, [dqResults]);

  // Column and row counts from profile or dataset
  const columnCount = profile?.columns?.length || 0;
  const rowCount = profile?.row_count || dataset?.row_count || 0;

  return (
    <div className="results-summary-page">
      {/* Page Heading */}
      <div className="page-heading" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "16px" }}>
        <div>
          <span className="eyebrow">
            {isVi ? "KẾT QUẢ · TỔNG KẾT TOÀN DIỆN WORKFLOW" : "RESULTS · WORKFLOW EXECUTIVE SUMMARY"}
          </span>
          <h1>
            {isVi ? "Kết quả & Đánh giá Chất lượng Dữ liệu" : "Data Quality Results & Executive Summary"}
          </h1>
          <p>
            {isVi
              ? `Báo cáo tổng kết toàn bộ quy trình kiểm định chất lượng dữ liệu cho tập '${dataset?.name ?? "Dataset"}'.`
              : `Comprehensive data quality outcome and stage summary report for '${dataset?.name ?? "Dataset"}'.`}
          </p>
        </div>
        <div style={{ display: "flex", gap: "10px", flexWrap: "wrap" }}>
          <button
            type="button"
            className="button secondary"
            onClick={() => window.print()}
            title={isVi ? "In hoặc lưu báo cáo ra PDF" : "Print or save report as PDF"}
          >
            🖨️ {isVi ? "In báo cáo" : "Print Report"}
          </button>
        </div>
      </div>

      <div className="datasets-page" style={{ marginTop: "24px", display: "flex", flexDirection: "column", gap: "24px" }}>
        {/* ================================================================= */}
        {/* SECTION 1: EXECUTIVE QUALITY SCORECARD (HERO KPI) */}
        {/* ================================================================= */}
        <section className="panel results-hero-panel" style={{ padding: "28px" }}>
          <div className="results-score-grid">
            {/* Score Wheel / Hero Display */}
            <div className="results-score-card">
              <span className="eyebrow">
                {isVi ? "ĐIỂM CHẤT LƯỢNG DỮ LIỆU (DQ SCORE)" : "DATA QUALITY SCORE (DQ SCORE)"}
              </span>
              <div className="results-score-display">
                <div
                  className="results-score-badge"
                  style={{
                    borderColor: gradeInfo.color,
                    boxShadow: `0 0 24px ${gradeInfo.color}25`,
                  }}
                >
                  <span className="results-score-number" style={{ color: gradeInfo.color }}>
                    {qualityScore === null ? "—" : `${qualityScore}%`}
                  </span>
                  <span className="results-grade-pill" style={{ backgroundColor: gradeInfo.color }}>
                    {isVi ? `Hạng ${gradeInfo.grade}` : `Grade ${gradeInfo.grade}`}
                  </span>
                </div>
                <div className="results-score-verdict">
                  <h3 style={{ margin: "0 0 6px 0", color: gradeInfo.color }}>
                    {gradeInfo.label}
                  </h3>
                  <p className="muted" style={{ margin: 0, fontSize: "14px", lineHeight: "1.5" }}>
                    {gradeInfo.description}
                  </p>
                </div>
              </div>
            </div>

            {/* 4 Supporting Metric Cards */}
            <div className="results-kpi-quad">
              <div className="results-kpi-box">
                <span className="eyebrow">{isVi ? "QUY TẮC KIỂM ĐỊNH" : "RULES EVALUATED"}</span>
                <div className="results-kpi-val">
                  <strong style={{ color: failedRules > 0 ? "var(--ink)" : "#10b981" }}>
                    {passedRules}/{totalRules}
                  </strong>
                  <span className="results-kpi-sub">
                    {totalRules > 0 ? `${((passedRules / totalRules) * 100).toFixed(0)}% ${isVi ? "đạt" : "pass"}` : "—"}
                  </span>
                </div>
                <small className="muted">
                  {failedRules > 0
                    ? isVi ? `${failedRules} quy tắc có lỗi vi phạm` : `${failedRules} rules failed`
                    : isVi ? "Tất cả quy tắc đều vượt qua" : "All rules passed successfully"}
                </small>
              </div>

              <div className="results-kpi-box">
                <span className="eyebrow">{isVi ? "BẢN GHI ĐÃ QUÉT" : "ROWS AUDITED"}</span>
                <div className="results-kpi-val">
                  <strong>{totalRowsChecked > 0 ? totalRowsChecked.toLocaleString() : rowCount.toLocaleString()}</strong>
                  <span className="results-kpi-sub">{isVi ? "dòng" : "rows"}</span>
                </div>
                <small className="muted">
                  {totalRowsFailed > 0
                    ? isVi ? `${totalRowsFailed.toLocaleString()} dòng vi phạm quy tắc` : `${totalRowsFailed.toLocaleString()} invalid rows`
                    : isVi ? "0 bản ghi vi phạm" : "0 invalid rows"}
                </small>
              </div>

              <div className="results-kpi-box">
                <span className="eyebrow">{isVi ? "BẤT THƯỜNG PHÁT HIỆN" : "ANOMALIES FLAGGED"}</span>
                <div className="results-kpi-val">
                  <strong style={{ color: dqAnomalies.length > 0 ? "#f59e0b" : "#10b981" }}>
                    {dqAnomalies.length}
                  </strong>
                  <span className="results-kpi-sub">{isVi ? "tín hiệu" : "signals"}</span>
                </div>
                <small className="muted">
                  {dqAnomalies.length > 0
                    ? isVi ? "Đã điều tra nguyên nhân gốc ở Graph 3" : "Investigated via Graph 3"
                    : isVi ? "Không phát hiện đột biến bất thường" : "No anomaly spikes detected"}
                </small>
              </div>

              <div className="results-kpi-box">
                <span className="eyebrow">{isVi ? "THỜI GIAN THỰC THI" : "PIPELINE DURATION"}</span>
                <div className="results-kpi-val">
                  <strong>{formatDuration(totalPipelineTimeMs)}</strong>
                </div>
                <small className="muted">
                  {latestWorkflowNodeCount > 0
                    ? isVi ? `${latestWorkflowNodeCount} node agent đã chạy qua 4 graph` : `${latestWorkflowNodeCount} agent nodes executed`
                    : isVi ? "Tổng thời gian pipeline" : "Total pipeline time"}
                </small>
              </div>
            </div>
          </div>
        </section>

        {/* ================================================================= */}
        {/* SECTION 2: WORKFLOW STAGE JOURNEY SUMMARY */}
        {/* ================================================================= */}
        <section className="panel" style={{ padding: "24px" }}>
          <div className="panel-heading">
            <div>
              <span className="eyebrow">
                {isVi ? "HÀNH TRÌNH WORKFLOW · TỔNG KẾT 4 GRAPH" : "WORKFLOW JOURNEY · 4 GRAPH STAGES"}
              </span>
              <h2>{isVi ? "Tình trạng Thực thi qua các Giai đoạn" : "Stage-by-Stage Execution Status"}</h2>
              <p className="muted">
                {isVi
                  ? "Tổng hợp kết quả xử lý từ khâu hiểu ngữ nghĩa dữ liệu đến sinh luật, thực thi và điều tra bất thường."
                  : "Summary of outcomes from semantic data understanding to rule engineering, execution, and root-cause analysis."}
              </p>
            </div>
          </div>

          <div className="results-stages-grid" style={{ marginTop: "20px" }}>
            {/* Stage 1: Graph 1A */}
            <div className="results-stage-card">
              <div className="results-stage-header">
                <div>
                  <span className="eyebrow">BƯỚC 2 · GRAPH 1A</span>
                  <h4>{isVi ? "Hiểu Ngữ nghĩa Dữ liệu" : "Semantic Understanding"}</h4>
                </div>
                <span className={`status-pill ${contractConfirmed ? "success" : understandingArtifact ? "info" : "neutral"}`}>
                  <span className="status-dot" />
                  {contractConfirmed
                    ? isVi ? "Đã xác nhận" : "Confirmed"
                    : understandingArtifact
                      ? isVi ? "Đã phân tích" : "Analyzed"
                      : isVi ? "Chưa chạy" : "Pending"}
                </span>
              </div>
              <p className="results-stage-desc muted">
                {isVi
                  ? `AI Agent đã quét ${columnCount} cột, nhận diện kiểu ngữ nghĩa và vai trò kinh doanh của bảng dữ liệu.`
                  : `AI Agent scanned ${columnCount} columns and mapped semantic types and business roles.`}
              </p>
              <div className="results-stage-footer">
                <button type="button" className="button ghost small" onClick={() => onNavigateToStep(2)}>
                  {isVi ? "Xem Hợp đồng Ngữ nghĩa →" : "View Semantic Contract →"}
                </button>
              </div>
            </div>

            {/* Stage 2: Graph 1B */}
            <div className="results-stage-card">
              <div className="results-stage-header">
                <div>
                  <span className="eyebrow">BƯỚC 3 · GRAPH 1B</span>
                  <h4>{isVi ? "Kỹ nghệ Quy tắc" : "Rule Engineering"}</h4>
                </div>
                <span className={`status-pill ${approvedRules.length > 0 ? "success" : proposals.length > 0 ? "info" : "neutral"}`}>
                  <span className="status-dot" />
                  {approvedRules.length > 0
                    ? isVi ? `${approvedRules.length} luật đã duyệt` : `${approvedRules.length} approved`
                    : proposals.length > 0
                      ? isVi ? `${proposals.length} đề xuất` : `${proposals.length} proposed`
                      : isVi ? "Chưa có luật" : "No rules"}
                </span>
              </div>
              <p className="results-stage-desc muted">
                {isVi
                  ? `Đã sinh ${proposals.length} ứng viên quy tắc, cấu hình ngưỡng kiểm tra và phê duyệt ${approvedRules.length} quy tắc.`
                  : `Generated ${proposals.length} candidate rules, calibrated thresholds, and approved ${approvedRules.length} rules.`}
              </p>
              <div className="results-stage-footer">
                <button type="button" className="button ghost small" onClick={() => onNavigateToStep(3)}>
                  {isVi ? "Xem Danh sách Quy tắc →" : "View Ruleset →"}
                </button>
              </div>
            </div>

            {/* Stage 3: Graph 2 */}
            <div className="results-stage-card">
              <div className="results-stage-header">
                <div>
                  <span className="eyebrow">BƯỚC 4 · GRAPH 2</span>
                  <h4>{isVi ? "Thực thi & Kiểm định" : "Deterministic Execution"}</h4>
                </div>
                <span className={`status-pill ${activeRun ? (failedRules === 0 ? "success" : "warning") : "neutral"}`}>
                  <span className="status-dot" />
                  {activeRun
                    ? isVi ? `Đã chạy (${passedRules}/${totalRules})` : `Executed (${passedRules}/${totalRules})`
                    : isVi ? "Chưa thực thi" : "Not executed"}
                </span>
              </div>
              <p className="results-stage-desc muted">
                {isVi
                  ? `Biên dịch sang dbt / SQL runner, kiểm tra ${totalRules} quy tắc trên ${totalRowsChecked.toLocaleString()} dòng dữ liệu.`
                  : `Compiled to dbt / SQL runner, executed ${totalRules} rules across ${totalRowsChecked.toLocaleString()} rows.`}
              </p>
              <div className="results-stage-footer">
                <button type="button" className="button ghost small" onClick={() => onNavigateToStep(4)}>
                  {isVi ? "Xem Chi tiết Thực thi →" : "View Execution Results →"}
                </button>
              </div>
            </div>

            {/* Stage 4: Graph 3 */}
            <div className="results-stage-card">
              <div className="results-stage-header">
                <div>
                  <span className="eyebrow">BƯỚC 5 · GRAPH 3</span>
                  <h4>{isVi ? "Bất thường & Nguyên nhân gốc" : "Anomalies & Root Cause"}</h4>
                </div>
                <span className={`status-pill ${dqAnomalies.length === 0 ? "success" : "warning"}`}>
                  <span className="status-dot" />
                  {dqAnomalies.length === 0
                    ? isVi ? "Bình thường" : "Clean"
                    : isVi ? `${dqAnomalies.length} bất thường` : `${dqAnomalies.length} anomalies`}
                </span>
              </div>
              <p className="results-stage-desc muted">
                {isVi
                  ? `Phát hiện ${dqAnomalies.length} tín hiệu bất thường, suy luận giả thuyết nguyên nhân gốc và tạo báo cáo Steward.`
                  : `Detected ${dqAnomalies.length} anomaly signals, inferred root causes, and drafted Steward investigation.`}
              </p>
              <div className="results-stage-footer">
                <button type="button" className="button ghost small" onClick={() => onNavigateToStep(5)}>
                  {isVi ? "Xem Báo cáo Điều tra →" : "View Investigation →"}
                </button>
              </div>
            </div>
          </div>
        </section>

        {/* ================================================================= */}
        {/* SECTION 3: VISUAL CHARTS & TREND ANALYSIS */}
        {/* ================================================================= */}
        <div className="results-visuals-grid">
          {/* Chart 1: Rule Outcome Distribution */}
          <section className="panel" style={{ padding: "24px" }}>
            <div className="panel-heading">
              <div>
                <span className="eyebrow">{isVi ? "PHÂN BỔ QUY TẮC" : "RULE DISTRIBUTION"}</span>
                <h3>{isVi ? "Tỷ lệ Đạt / Vi phạm Quy tắc" : "Rule Pass vs Fail Ratio"}</h3>
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
                        tone: "pass",
                      },
                      {
                        name: isVi ? "Vi phạm" : "Failed",
                        count: failedRules,
                        tone: "fail",
                      },
                    ]}
                  >
                    <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                    <XAxis dataKey="name" stroke="currentColor" fontSize={12} />
                    <YAxis allowDecimals={false} stroke="currentColor" fontSize={12} />
                    <Tooltip
                      contentStyle={{
                        background: "var(--surface)",
                        borderColor: "var(--border)",
                        borderRadius: "8px",
                        fontSize: "13px",
                      }}
                    />
                    <Bar dataKey="count" radius={[6, 6, 0, 0]}>
                      <Cell fill="#10b981" />
                      <Cell fill="#ef4444" />
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              )}
            </div>
          </section>

          {/* Chart 2: Quality Trend Over Time */}
          <section className="panel" style={{ padding: "24px" }}>
            <div className="panel-heading">
              <div>
                <span className="eyebrow">{isVi ? "XU HƯỚNG CHẤT LƯỢNG" : "QUALITY TREND"}</span>
                <h3>{isVi ? "Biến động Điểm Chất lượng Dữ liệu" : "Data Quality Score Over Time"}</h3>
              </div>
            </div>

            <div style={{ height: "240px", marginTop: "16px" }}>
              {historicalTrendsData.length === 0 ? (
                <div className="workflow-artifact-empty" style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center" }}>
                  {isVi ? "Chưa có dữ liệu lịch sử các lượt chạy." : "No trend history recorded yet."}
                </div>
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={historicalTrendsData}>
                    <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                    <XAxis dataKey="runLabel" stroke="currentColor" fontSize={12} />
                    <YAxis domain={[0, 100]} stroke="currentColor" fontSize={12} />
                    <Tooltip
                      formatter={(val: any) => [`${val}%`, isVi ? "Điểm DQ" : "DQ Score"]}
                      contentStyle={{
                        background: "var(--surface)",
                        borderColor: "var(--border)",
                        borderRadius: "8px",
                        fontSize: "13px",
                      }}
                    />
                    <Line
                      type="monotone"
                      dataKey="score"
                      stroke="#2563eb"
                      strokeWidth={3}
                      dot={{ r: 4, fill: "#2563eb" }}
                      activeDot={{ r: 6 }}
                    />
                  </LineChart>
                </ResponsiveContainer>
              )}
            </div>
          </section>
        </div>

        {/* ================================================================= */}
        {/* SECTION 4: DETAILED RULE OBSERVATIONS TABLE */}
        {/* ================================================================= */}
        <section className="panel" style={{ padding: "24px" }}>
          <div className="panel-heading" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "12px" }}>
            <div>
              <span className="eyebrow">{isVi ? "CHI TIẾT KIỂM ĐỊNH" : "DETAILED FINDINGS"}</span>
              <h2>{isVi ? "Bảng Theo dõi Quy tắc & Vi phạm" : "Ruleset & Violation Audit Table"}</h2>
              <p className="muted">
                {isVi
                  ? "Danh sách các quy tắc được kiểm định trong lượt chạy này, sắp xếp theo mức độ vi phạm."
                  : "Rules evaluated in this execution, prioritized by violation severity."}
              </p>
            </div>
            <button type="button" className="button ghost small" onClick={() => onNavigateToStep(4)}>
              {isVi ? "Xem toàn bộ quy tắc →" : "View All Rules →"}
            </button>
          </div>

          <div className="analytics-table-scroll" style={{ marginTop: "16px" }}>
            <table className="analytics-table">
              <thead>
                <tr>
                  <th>{isVi ? "Tên Quy tắc" : "Rule Name"}</th>
                  <th>{isVi ? "Số bản ghi quét" : "Rows Audited"}</th>
                  <th>{isVi ? "Số dòng lỗi" : "Failed Rows"}</th>
                  <th>{isVi ? "Tỷ lệ vi phạm" : "Failure Rate"}</th>
                  <th>{isVi ? "Trạng thái" : "Status"}</th>
                </tr>
              </thead>
              <tbody>
                {criticalRules.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="empty-cell" style={{ textAlign: "center", padding: "24px" }}>
                      {isVi
                        ? "Chưa có dữ liệu kiểm định quy tắc. Hãy chạy kiểm định ở Bước 4."
                        : "No rule execution results found. Please run verification in Step 4."}
                    </td>
                  </tr>
                ) : (
                  criticalRules.map((rule) => {
                    const failRate =
                      rule.checked_count > 0
                        ? ((rule.failed_count / rule.checked_count) * 100).toFixed(2)
                        : "0.00";
                    return (
                      <tr key={rule.rule_id}>
                        <td>
                          <strong>{rule.rule_title}</strong>
                          <div className="muted" style={{ fontSize: "11px", fontFamily: "monospace" }}>
                            {rule.rule_id}
                          </div>
                        </td>
                        <td className="muted">{rule.checked_count?.toLocaleString() ?? "—"}</td>
                        <td style={{ color: rule.failed_count > 0 ? "#ef4444" : "inherit", fontWeight: rule.failed_count > 0 ? 600 : "normal" }}>
                          {rule.failed_count?.toLocaleString() ?? 0}
                        </td>
                        <td>
                          <span style={{ color: Number(failRate) > 0 ? "#ef4444" : "#10b981", fontWeight: 600 }}>
                            {failRate}%
                          </span>
                        </td>
                        <td>
                          <span className={`status-pill ${rule.status === "PASS" ? "success" : "danger"}`}>
                            <span className="status-dot" />
                            {rule.status === "PASS" ? (isVi ? "ĐẠT" : "PASS") : (isVi ? "LỖI" : "FAIL")}
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
        {/* SECTION 5: AI STEWARD RECOMMENDATIONS & NEXT STEPS */}
        {/* ================================================================= */}
        <section className="panel" style={{ padding: "24px" }}>
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{isVi ? "KHUYẾN NGHỊ & HÀNH ĐỘNG TIẾP THEO" : "AI RECOMMENDATIONS & NEXT STEPS"}</span>
              <h2>{isVi ? "Đề xuất Xử lý từ AI Data Steward" : "Actionable Guidance from AI Data Steward"}</h2>
              <p className="muted">
                {isVi
                  ? "Các bước đề xuất để nâng cao chất lượng dữ liệu và hoàn thiện pipeline."
                  : "Recommended actions to elevate data quality and harden pipeline integrity."}
              </p>
            </div>
          </div>

          <div className="results-recommendations-list" style={{ marginTop: "16px", display: "grid", gap: "12px" }}>
            {failedRules === 0 && dqAnomalies.length === 0 ? (
              <div className="results-rec-item success">
                <div className="results-rec-icon">✅</div>
                <div>
                  <strong>{isVi ? "Dữ liệu đạt chuẩn tuyệt đối" : "Perfect Data Quality Compliance"}</strong>
                  <p className="muted" style={{ margin: "4px 0 0 0", fontSize: "13px" }}>
                    {isVi
                      ? "Tất cả quy tắc kiểm tra đều vượt qua với 0 dòng lỗi. Bạn có thể tự tin sử dụng tập dữ liệu này cho các mô hình phân tích hạ nguồn."
                      : "All assertions passed with 0 failing records. The dataset is ready for downstream analytical and ML ingestion."}
                  </p>
                </div>
              </div>
            ) : (
              <>
                {failedRules > 0 && (
                  <div className="results-rec-item warning">
                    <div className="results-rec-icon">⚠️</div>
                    <div>
                      <strong>{isVi ? `Xử lý ${failedRules} quy tắc có bản ghi vi phạm` : `Remediate ${failedRules} failing rules`}</strong>
                      <p className="muted" style={{ margin: "4px 0 0 0", fontSize: "13px" }}>
                        {isVi
                          ? `Có ${totalRowsFailed.toLocaleString()} dòng dữ liệu không thoả mãn ràng buộc. Xem lại các cột tương ứng hoặc điều chỉnh ngưỡng kiểm định nếu có thay đổi nghiệp vụ.`
                          : `${totalRowsFailed.toLocaleString()} records violated business assertions. Review source transformations or adjust thresholds in Step 3.`}
                      </p>
                    </div>
                  </div>
                )}
                {dqAnomalies.length > 0 && (
                  <div className="results-rec-item info">
                    <div className="results-rec-icon">💡</div>
                    <div>
                      <strong>{isVi ? `Kiểm tra ${dqAnomalies.length} tín hiệu bất thường được Graph 3 ghi nhận` : `Investigate ${dqAnomalies.length} anomaly signals from Graph 3`}</strong>
                      <p className="muted" style={{ margin: "4px 0 0 0", fontSize: "13px" }}>
                        {isVi
                          ? "Truy cập Bước 5 (Graph 3) để xem các giả thuyết nguyên nhân gốc được agent suy luận và đọc Báo cáo Steward chi tiết."
                          : "Navigate to Step 5 (Graph 3) to review AI-generated root-cause hypotheses and inspect the detailed Steward report."}
                      </p>
                    </div>
                  </div>
                )}
              </>
            )}

            <div className="results-rec-item neutral">
              <div className="results-rec-icon">🔄</div>
              <div>
                <strong>{isVi ? "Thiết lập giám sát định kỳ liên tục" : "Establish Continuous Quality Monitoring"}</strong>
                <p className="muted" style={{ margin: "4px 0 0 0", fontSize: "13px" }}>
                  {isVi
                    ? "Tích hợp bộ quy tắc đã phê duyệt vào lịch chạy tự động để liên tục theo dõi chất lượng các đợt nạp dữ liệu mới."
                    : "Integrate the approved ruleset into scheduled ETL/dbt runs to monitor incoming dataset batches automatically."}
                </p>
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
};
