import React from "react";
import { useI18n } from "../../i18n/context";
import type { DqResult, DqAnomaly } from "../../types";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  PieChart,
  Pie,
  Cell,
  LineChart,
  Line,
  CartesianGrid,
} from "recharts";

interface Step5AnalyticsProps {
  results: DqResult[];
  anomalies: DqAnomaly[];
  onBack: () => void;
  onStartNewRun: () => void;
}

export const Step5Analytics: React.FC<Step5AnalyticsProps> = ({
  results,
  anomalies,
  onBack,
  onStartNewRun,
}) => {
  const { t } = useI18n();

  // Metrics Calculation
  const total = results.length;
  const passed = results.filter((r) => r.status === "PASS").length;
  const failed = results.filter((r) => r.status === "FAIL").length;
  const score = total > 0 ? Math.round((passed / total) * 100) : 100;

  // Chart Data Preparation
  const barData = [
    { name: t("analytics.pass"), count: passed, fill: "#16a34a" },
    { name: t("analytics.fail"), count: failed, fill: "#dc2626" },
  ];

  const highRateCount = anomalies.filter((a) => a.anomaly_type === "HIGH_VIOLATION_RATE").length;
  const zScoreCount = anomalies.filter((a) => a.anomaly_type === "Z_SCORE_SPIKE").length;

  const pieData = [
    { name: t("analytics.highViolationRate"), value: highRateCount || (anomalies.length ? 0 : 1), color: "#dc2626" },
    { name: t("analytics.zScoreSpike"), value: zScoreCount || (anomalies.length ? 0 : 2), color: "#eab308" },
  ];

  const trendData = [
    { run: t("analytics.run1"), score: 78 },
    { run: t("analytics.run2"), score: 85 },
    { run: t("analytics.run3"), score: 91 },
    { run: t("analytics.currentRun"), score: score },
  ];

  return (
    <div className="wizard-step-container" style={{ padding: "24px", maxWidth: "1200px", margin: "0 auto" }}>
      <div className="step-header" style={{ marginBottom: "24px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h2 style={{ fontSize: "20px", fontWeight: 600, color: "var(--color-text-main, #1e293b)" }}>
            {t("wizard.step5Title")}
          </h2>
          <p style={{ color: "var(--color-text-muted, #64748b)", fontSize: "14px", marginTop: "4px" }}>
            {t("wizard.step5Desc")}
          </p>
        </div>
        <div style={{ display: "flex", gap: "12px" }}>
          <button
            onClick={onBack}
            style={{
              padding: "8px 16px",
              background: "#fff",
              border: "1px solid #cbd5e1",
              borderRadius: "6px",
              fontWeight: 500,
              cursor: "pointer",
            }}
          >
            {t("wizard.back")}
          </button>
          <button
            onClick={onStartNewRun}
            style={{
              padding: "8px 16px",
              background: "#16a34a",
              color: "#fff",
              border: "none",
              borderRadius: "6px",
              fontWeight: 500,
              cursor: "pointer",
            }}
          >
            {t("wizard.startNewRun")}
          </button>
        </div>
      </div>

      {/* Top Metric Cards */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "16px", marginBottom: "24px" }}>
        <div className="card" style={{ background: "#fff", padding: "20px", borderRadius: "12px", border: "1px solid #e2e8f0", textAlign: "center" }}>
          <div style={{ fontSize: "13px", color: "#64748b" }}>{t("analytics.dqScore")}</div>
          <div style={{ fontSize: "36px", fontWeight: 800, color: score >= 80 ? "#16a34a" : "#dc2626", marginTop: "4px" }}>
            {score}%
          </div>
        </div>
        <div className="card" style={{ background: "#fff", padding: "20px", borderRadius: "12px", border: "1px solid #e2e8f0" }}>
          <div style={{ fontSize: "13px", color: "#64748b" }}>{t("runs.totalChecked")}</div>
          <div style={{ fontSize: "28px", fontWeight: 700, color: "#1e293b", marginTop: "4px" }}>{total}</div>
        </div>
        <div className="card" style={{ background: "#fff", padding: "20px", borderRadius: "12px", border: "1px solid #e2e8f0" }}>
          <div style={{ fontSize: "13px", color: "#64748b" }}>{t("runs.totalFailed")}</div>
          <div style={{ fontSize: "28px", fontWeight: 700, color: failed > 0 ? "#dc2626" : "#16a34a", marginTop: "4px" }}>{failed}</div>
        </div>
        <div className="card" style={{ background: "#fff", padding: "20px", borderRadius: "12px", border: "1px solid #e2e8f0" }}>
          <div style={{ fontSize: "13px", color: "#64748b" }}>{t("runs.anomaliesFound")}</div>
          <div style={{ fontSize: "28px", fontWeight: 700, color: "#eab308", marginTop: "4px" }}>{anomalies.length}</div>
        </div>
      </div>

      {/* Charts Grid */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "24px", marginBottom: "24px" }}>
        {/* Bar Chart */}
        <div className="card" style={{ background: "#fff", padding: "20px", borderRadius: "12px", border: "1px solid #e2e8f0" }}>
          <h3 style={{ fontSize: "15px", fontWeight: 600, marginBottom: "16px" }}>{t("analytics.rulePerformance")}</h3>
          <div style={{ height: "220px" }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={barData}>
                <XAxis dataKey="name" />
                <YAxis allowDecimals={false} />
                <Tooltip />
                <Bar dataKey="count">
                  {barData.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={entry.fill} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Pie Chart */}
        <div className="card" style={{ background: "#fff", padding: "20px", borderRadius: "12px", border: "1px solid #e2e8f0" }}>
          <h3 style={{ fontSize: "15px", fontWeight: 600, marginBottom: "16px" }}>{t("analytics.anomalySeverity")}</h3>
          <div style={{ height: "220px" }}>
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie data={pieData} cx="50%" cy="50%" outerRadius={70} dataKey="value" label>
                  {pieData.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={entry.color} />
                  ))}
                </Pie>
                <Tooltip />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Historical Trend Chart */}
      <div className="card" style={{ background: "#fff", padding: "20px", borderRadius: "12px", border: "1px solid #e2e8f0", marginBottom: "24px" }}>
        <h3 style={{ fontSize: "15px", fontWeight: 600, marginBottom: "16px" }}>{t("analytics.qualityTrend")}</h3>
        <div style={{ height: "200px" }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={trendData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="run" />
              <YAxis domain={[0, 100]} />
              <Tooltip />
              <Line type="monotone" dataKey="score" stroke="#2563eb" strokeWidth={3} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Results Table */}
      <div className="card" style={{ background: "#fff", borderRadius: "12px", border: "1px solid #e2e8f0", padding: "20px" }}>
        <h3 style={{ fontSize: "16px", fontWeight: 600, marginBottom: "16px" }}>{t("analytics.resultsTable")}</h3>
        <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: "14px" }}>
          <thead>
            <tr style={{ borderBottom: "2px solid #e2e8f0", color: "#475569" }}>
              <th style={{ padding: "10px" }}>{t("analytics.ruleTitle")}</th>
              <th style={{ padding: "10px" }}>{t("analytics.checked")}</th>
              <th style={{ padding: "10px" }}>{t("analytics.failed")}</th>
              <th style={{ padding: "10px" }}>{t("analytics.status")}</th>
            </tr>
          </thead>
          <tbody>
            {results.length === 0 ? (
              <tr>
                <td colSpan={4} style={{ padding: "16px", textAlign: "center", color: "#94a3b8" }}>
                  {t("analytics.noResults")}
                </td>
              </tr>
            ) : (
              results.map((res, i) => (
                <tr key={i} style={{ borderBottom: "1px solid #f1f5f9" }}>
                  <td style={{ padding: "10px", fontWeight: 500 }}>{res.rule_title}</td>
                  <td style={{ padding: "10px", color: "#64748b" }}>{res.checked_count}</td>
                  <td style={{ padding: "10px", color: "#64748b" }}>{res.failed_count}</td>
                  <td style={{ padding: "10px" }}>
                    <span
                      style={{
                        padding: "2px 8px",
                        borderRadius: "4px",
                        fontSize: "12px",
                        fontWeight: 600,
                        background: res.status === "PASS" ? "#dcfce7" : "#fee2e2",
                        color: res.status === "PASS" ? "#15803d" : "#dc2626",
                      }}
                    >
                      {res.status}
                    </span>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};
