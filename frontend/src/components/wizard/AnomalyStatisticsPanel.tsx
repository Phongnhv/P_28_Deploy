import { useMemo } from "react";
import type { DqAnomaly, DqResult } from "../../types";

/**
 * Everything Graph 3 measured, laid out so it can be read.
 *
 * The previous panel showed two numbers and a pie chart labelled "severity
 * distribution" that actually plotted anomaly *types* — with one type present it
 * rendered as a single filled circle carrying no information at all. Every
 * figure here comes from the anomaly payload; nothing is derived from a
 * threshold this screen invented.
 */

const TYPE_LABELS: Record<string, { vi: string; en: string }> = {
  HIGH_VIOLATION_RATE: { vi: "Tỷ lệ vi phạm cao", en: "High violation rate" },
  Z_SCORE_SPIKE: { vi: "Đột biến theo z-score", en: "Z-score spike" },
};

function percent(rate: number): string {
  const value = rate * 100;
  // Rates near 100% matter to two decimals; small ones do not need more than one.
  return value >= 99 || value < 1 ? `${value.toFixed(2)}%` : `${value.toFixed(1)}%`;
}

/** Pull the column out of ids shaped `rv_source_rows.<column>.<RULE_TYPE>`. */
function columnOf(ruleId: string): string | null {
  const parts = ruleId.replace(/^rv_/, "").split(".");
  return parts.length >= 3 ? parts[1] : null;
}

function ruleTypeOf(ruleId: string): string | null {
  const parts = ruleId.replace(/^rv_/, "").split(".");
  return parts.length >= 3 ? parts[2].split("#")[0] : null;
}

export function AnomalyStatisticsPanel({
  anomalies,
  results,
  language,
}: {
  anomalies: DqAnomaly[];
  results: DqResult[];
  language: "en" | "vi";
}) {
  const vi = language === "vi";

  const stats = useMemo(() => {
    const byType = new Map<string, number>();
    const byColumn = new Map<string, number>();
    const byRuleType = new Map<string, number>();
    let peakRate = 0;
    let peakZ = 0;
    let affectedRows = 0;
    let coldStart = 0;

    for (const anomaly of anomalies) {
      byType.set(anomaly.anomaly_type, (byType.get(anomaly.anomaly_type) ?? 0) + 1);
      const column = columnOf(anomaly.rule_id);
      if (column) byColumn.set(column, (byColumn.get(column) ?? 0) + 1);
      const ruleType = ruleTypeOf(anomaly.rule_id);
      if (ruleType) byRuleType.set(ruleType, (byRuleType.get(ruleType) ?? 0) + 1);
      peakRate = Math.max(peakRate, anomaly.current_rate);
      if (typeof anomaly.z_score === "number") peakZ = Math.max(peakZ, anomaly.z_score);
      affectedRows += anomaly.failed_count;
      if (anomaly.detection_mode === "COLD_START") coldStart += 1;
    }

    return {
      byType: [...byType.entries()].sort((a, b) => b[1] - a[1]),
      byColumn: [...byColumn.entries()].sort((a, b) => b[1] - a[1]),
      byRuleType: [...byRuleType.entries()].sort((a, b) => b[1] - a[1]),
      peakRate,
      peakZ,
      affectedRows,
      coldStart,
      allColdStart: anomalies.length > 0 && coldStart === anomalies.length,
    };
  }, [anomalies]);

  const ranked = useMemo(
    () => [...anomalies].sort((a, b) => b.current_rate - a.current_rate),
    [anomalies],
  );

  const checkedRules = results.length;
  const cleanRules = Math.max(0, checkedRules - anomalies.length);

  if (anomalies.length === 0) {
    return (
      <section className="anomaly-stats">
        <header className="anomaly-stats-head">
          <div>
            <span className="eyebrow">{vi ? "THỐNG KÊ BẤT THƯỜNG" : "ANOMALY STATISTICS"}</span>
            <h3>{vi ? "Không phát hiện bất thường" : "No anomalies detected"}</h3>
          </div>
        </header>
        <p className="anomaly-empty">
          {checkedRules > 0
            ? vi
              ? `Cả ${checkedRules} luật đều nằm trong ngưỡng cho phép ở lượt chạy này.`
              : `All ${checkedRules} rules stayed within their thresholds on this run.`
            : vi
              ? "Chạy bộ luật đã duyệt ở bước 4 để có dữ liệu phân tích."
              : "Run the approved rules in step 4 to produce something to analyse."}
        </p>
      </section>
    );
  }

  return (
    <section className="anomaly-stats">
      <header className="anomaly-stats-head">
        <div>
          <span className="eyebrow">{vi ? "THỐNG KÊ BẤT THƯỜNG" : "ANOMALY STATISTICS"}</span>
          <h3>{vi ? "Graph 3 đã đo được gì" : "What Graph 3 measured"}</h3>
          <p>
            {vi
              ? `${anomalies.length} tín hiệu lệch trên ${checkedRules} luật đã kiểm.`
              : `${anomalies.length} deviating signals across ${checkedRules} checked rules.`}
          </p>
        </div>
      </header>

      <div className="anomaly-kpis">
        <article className="anomaly-kpi alert">
          <span>{vi ? "Bất thường" : "Anomalies"}</span>
          <strong>{anomalies.length}</strong>
          <small>{vi ? `${cleanRules} luật bình thường` : `${cleanRules} rules clean`}</small>
        </article>
        <article className="anomaly-kpi">
          <span>{vi ? "Tỷ lệ vi phạm cao nhất" : "Peak violation rate"}</span>
          <strong>{percent(stats.peakRate)}</strong>
          <small>{vi ? "trên một luật đơn lẻ" : "on a single rule"}</small>
        </article>
        <article className="anomaly-kpi">
          <span>{vi ? "Z-score cao nhất" : "Peak z-score"}</span>
          <strong>{stats.peakZ ? stats.peakZ.toFixed(2) : "—"}</strong>
          <small>{vi ? "độ lệch so với baseline" : "deviation from baseline"}</small>
        </article>
        <article className="anomaly-kpi">
          <span>{vi ? "Dòng bị ảnh hưởng" : "Rows affected"}</span>
          <strong>{stats.affectedRows.toLocaleString()}</strong>
          <small>{vi ? "cộng dồn các luật lệch" : "summed across deviating rules"}</small>
        </article>
        <article className="anomaly-kpi">
          <span>{vi ? "Cột liên quan" : "Columns involved"}</span>
          <strong>{stats.byColumn.length}</strong>
          <small>
            {stats.byColumn.slice(0, 2).map(([name]) => name).join(", ") || "—"}
            {stats.byColumn.length > 2 ? "…" : ""}
          </small>
        </article>
        <article className="anomaly-kpi">
          <span>{vi ? "Chế độ phát hiện" : "Detection mode"}</span>
          <strong>{stats.allColdStart ? "Cold start" : vi ? "Hỗn hợp" : "Mixed"}</strong>
          <small>
            {vi
              ? `${stats.coldStart}/${anomalies.length} dùng ngưỡng tĩnh`
              : `${stats.coldStart}/${anomalies.length} on static thresholds`}
          </small>
        </article>
      </div>

      {stats.allColdStart && (
        <p className="anomaly-coldstart">
          {vi
            ? "Chưa có lịch sử chạy nên mọi tín hiệu đều so với ngưỡng Cold-Start tĩnh 5%, không phải với baseline của chính bộ dữ liệu này. Chạy thêm vài lượt để hệ thống chuyển sang so sánh theo lịch sử."
            : "There is no run history yet, so every signal is compared against the static 5% cold-start threshold rather than this dataset's own baseline. A few more runs will switch it to historical comparison."}
        </p>
      )}

      <div className="anomaly-panels">
        <article className="anomaly-panel">
          <h4>{vi ? "Xếp hạng theo tỷ lệ vi phạm" : "Ranked by violation rate"}</h4>
          <ul className="anomaly-rank">
            {ranked.map((anomaly) => {
              const share = stats.peakRate > 0 ? (anomaly.current_rate / stats.peakRate) * 100 : 0;
              return (
                <li key={anomaly.rule_id}>
                  <div className="anomaly-rank-label">
                    <strong title={anomaly.rule_title}>{anomaly.rule_title}</strong>
                    <code>{columnOf(anomaly.rule_id) ?? anomaly.rule_id}</code>
                  </div>
                  <div className="anomaly-rank-bar">
                    <span style={{ width: `${Math.max(share, 2)}%` }} />
                  </div>
                  <b>{percent(anomaly.current_rate)}</b>
                </li>
              );
            })}
          </ul>
        </article>

        <article className="anomaly-panel">
          {/* Labelled for what it actually counts. The old chart said "severity"
              while plotting types, which with one type present was a solid disc. */}
          <h4>{vi ? "Phân bố theo loại tín hiệu" : "Breakdown by signal type"}</h4>
          <ul className="anomaly-breakdown">
            {stats.byType.map(([type, count]) => (
              <li key={type}>
                <span>{TYPE_LABELS[type]?.[vi ? "vi" : "en"] ?? type}</span>
                <div className="anomaly-breakdown-bar">
                  <span style={{ width: `${(count / anomalies.length) * 100}%` }} />
                </div>
                <b>{count}</b>
              </li>
            ))}
          </ul>

          <h4 className="anomaly-subhead">{vi ? "Theo loại luật" : "By rule type"}</h4>
          <ul className="anomaly-breakdown">
            {stats.byRuleType.map(([type, count]) => (
              <li key={type}>
                <span><code>{type}</code></span>
                <div className="anomaly-breakdown-bar">
                  <span style={{ width: `${(count / anomalies.length) * 100}%` }} />
                </div>
                <b>{count}</b>
              </li>
            ))}
          </ul>
        </article>
      </div>

      <article className="anomaly-panel">
        <h4>{vi ? "Chi tiết từng tín hiệu" : "Signal detail"}</h4>
        <div className="anomaly-table-scroll">
          <table className="anomaly-table">
            <thead>
              <tr>
                <th>{vi ? "Luật" : "Rule"}</th>
                <th>{vi ? "Loại" : "Type"}</th>
                <th className="numeric">{vi ? "Hiện tại" : "Current"}</th>
                <th className="numeric">{vi ? "Baseline" : "Baseline"}</th>
                <th className="numeric">Z</th>
                <th className="numeric">{vi ? "Lịch sử" : "History"}</th>
                <th className="numeric">{vi ? "Dòng lỗi" : "Failed"}</th>
                <th>{vi ? "Lý do" : "Reason"}</th>
              </tr>
            </thead>
            <tbody>
              {ranked.map((anomaly) => (
                <tr key={anomaly.rule_id}>
                  <td>
                    <strong>{anomaly.rule_title}</strong>
                    <span className="anomaly-table-sub">{anomaly.rule_id.replace(/^rv_/, "")}</span>
                  </td>
                  <td>
                    <span className="anomaly-type-chip">
                      {TYPE_LABELS[anomaly.anomaly_type]?.[vi ? "vi" : "en"] ?? anomaly.anomaly_type}
                    </span>
                  </td>
                  <td className="numeric strong-red">{percent(anomaly.current_rate)}</td>
                  <td className="numeric">
                    {typeof anomaly.historical_mean === "number" ? percent(anomaly.historical_mean) : "—"}
                  </td>
                  <td className="numeric">
                    {typeof anomaly.z_score === "number" ? anomaly.z_score.toFixed(2) : "—"}
                  </td>
                  <td className="numeric">
                    {anomaly.history_size > 0
                      ? anomaly.history_size
                      : <span className="anomaly-coldstart-chip">cold start</span>}
                  </td>
                  <td className="numeric">{anomaly.failed_count.toLocaleString()}</td>
                  <td className="anomaly-reason">{anomaly.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </article>
    </section>
  );
}
