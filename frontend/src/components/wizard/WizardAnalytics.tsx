import React from "react";
import { useI18n } from "../../i18n/context";
import type { DqResult, DqAnomaly, QualityTrendPoint } from "../../types";
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

/**
 * Result views for the two execution graphs.
 *
 * These used to be one "analytics dashboard" at the end of the wizard, which put
 * the pass/fail chart four steps away from the run that produced it. Splitting
 * them lets each chart sit beside the graph whose output it visualises: Graph 2
 * results in step 4, Graph 3 anomalies in step 5.
 */

/** Shared empty frame: say why there is nothing to draw rather than drawing zeros. */
const EmptyChart: React.FC<{ message: string }> = ({ message }) => (
  <div className="analytics-empty">{message}</div>
);

// ---------------------------------------------------------------------------
// Graph 2 — deterministic execution results
// ---------------------------------------------------------------------------

export const Graph2Analytics: React.FC<{
  results: DqResult[];
  /** Quality score of previous runs. Absent means no history yet. */
  trends?: QualityTrendPoint[];
}> = ({ results, trends }) => {
  const { t } = useI18n();

  const total = results.length;
  const passed = results.filter((r) => r.status === "PASS").length;
  const failed = results.filter((r) => r.status === "FAIL").length;
  // No rules run means no score -- null, not 100. An unchecked dataset is not a
  // perfect dataset.
  const score = total > 0 ? Math.round((passed / total) * 100) : null;

  const barData = [
    { name: t("analytics.pass"), count: passed, tone: "pass" as const },
    { name: t("analytics.fail"), count: failed, tone: "fail" as const },
  ];

  // Score over time comes from real run history. An earlier version hardcoded
  // "Run #1 78, Run #2 85, Run #3 91" -- invented numbers inside a data quality
  // tool, so a viewer could not tell a real trend from an illustration.
  const trendData = (trends ?? [])
    .slice()
    .sort((left, right) => left.created_at.localeCompare(right.created_at))
    .map((point) => ({
      run: new Date(point.created_at).toLocaleDateString(),
      score: Math.round(point.quality_score),
    }));

  return (
    <div className="analytics-page">
      <div className="analytics-metrics">
        <div className="analytics-metric">
          <div className="label">{t("analytics.dqScore")}</div>
          <div className={`value ${score === null ? "none" : score >= 80 ? "good" : "bad"}`}>
            {score === null ? "—" : `${score}%`}
          </div>
          <div className="hint">
            {score === null ? t("analytics.noRulesRun") : t("analytics.rulesPassed", { passed, total })}
          </div>
        </div>
        <div className="analytics-metric">
          <div className="label">{t("runs.totalChecked")}</div>
          <div className="value">{total}</div>
          <div className="hint">{t("analytics.rulesExecuted")}</div>
        </div>
        <div className="analytics-metric">
          <div className="label">{t("runs.totalFailed")}</div>
          <div className={`value ${failed > 0 ? "bad" : "good"}`}>{failed}</div>
          <div className="hint">
            {failed > 0 ? t("analytics.reviewSource") : t("analytics.noRuleFailed")}
          </div>
        </div>
      </div>

      <div className="analytics-charts">
        <div className="analytics-card">
          <h3>{t("analytics.rulePerformance")}</h3>
          <div className="analytics-chart-frame">
            {total === 0 ? (
              <EmptyChart message={t("analytics.emptyResults")} />
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={barData}>
                  <XAxis dataKey="name" stroke="currentColor" className="chart-axis" />
                  <YAxis allowDecimals={false} stroke="currentColor" className="chart-axis" />
                  <Tooltip />
                  <Bar dataKey="count">
                    {barData.map((entry) => (
                      <Cell key={entry.name} className={`chart-fill-${entry.tone}`} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>

        <div className="analytics-card">
          <h3>{t("analytics.qualityTrend")}</h3>
          <div className="analytics-chart-frame">
            {trendData.length < 2 ? (
              <EmptyChart
                message={
                  trendData.length === 0 ? t("analytics.emptyTrendNone") : t("analytics.emptyTrendOne")
                }
              />
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={trendData}>
                  <CartesianGrid strokeDasharray="3 3" className="chart-grid-line" />
                  <XAxis dataKey="run" stroke="currentColor" className="chart-axis" />
                  <YAxis domain={[0, 100]} stroke="currentColor" className="chart-axis" />
                  <Tooltip />
                  <Line
                    type="monotone"
                    dataKey="score"
                    strokeWidth={3}
                    className="chart-line-accent"
                    stroke="currentColor"
                  />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>
      </div>

      <div className="analytics-card">
        <h3>{t("analytics.resultsTable")}</h3>
        <div className="analytics-table-scroll">
          <table className="analytics-table">
            <thead>
              <tr>
                <th>{t("analytics.colRule")}</th>
                <th>{t("analytics.colChecked")}</th>
                <th>{t("analytics.colFailed")}</th>
                <th>{t("analytics.colStatus")}</th>
              </tr>
            </thead>
            <tbody>
              {results.length === 0 ? (
                <tr>
                  <td className="empty-cell" colSpan={4}>
                    {t("analytics.emptyResultsTable")}
                  </td>
                </tr>
              ) : (
                results.map((res) => (
                  <tr key={res.rule_id}>
                    <td>{res.rule_title}</td>
                    <td className="muted">{res.checked_count}</td>
                    <td className="muted">{res.failed_count}</td>
                    <td>
                      <span className={`status-pill ${res.status === "PASS" ? "success" : "danger"}`}>
                        <span className="status-dot" />
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
    </div>
  );
};

// ---------------------------------------------------------------------------
// Graph 3 — anomaly detection results
// ---------------------------------------------------------------------------

