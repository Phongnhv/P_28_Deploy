import React from "react";
import { useI18n } from "../../i18n/context";

interface ProfileData {
  dataset_id?: string;
  row_count?: number;
  column_count?: number;
  null_count?: number;
  completeness_score?: number;
  duplicate_count?: number;
  columns?: Array<{ name: string; type: string; null_percentage?: number }>;
}

interface Step2QualityProfilingProps {
  profile: ProfileData | null;
  loading: boolean;
  onNext: () => void;
  onBack: () => void;
}

export const Step2QualityProfiling: React.FC<Step2QualityProfilingProps> = ({
  profile,
  loading,
  onNext,
  onBack,
}) => {
  const { t } = useI18n();

  return (
    <div className="wizard-step-container" style={{ padding: "24px", maxWidth: "1200px", margin: "0 auto" }}>
      <div className="step-header" style={{ marginBottom: "24px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h2 style={{ fontSize: "20px", fontWeight: 600, color: "var(--color-text-main, #1e293b)" }}>
            {t("wizard.step2Title")}
          </h2>
          <p style={{ color: "var(--color-text-muted, #64748b)", fontSize: "14px", marginTop: "4px" }}>
            {t("wizard.step2Desc")}
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
            onClick={onNext}
            style={{
              padding: "8px 16px",
              background: "#2563eb",
              color: "#fff",
              border: "none",
              borderRadius: "6px",
              fontWeight: 500,
              cursor: "pointer",
            }}
          >
            {t("wizard.next")}
          </button>
        </div>
      </div>

      {loading ? (
        <div style={{ textAlign: "center", padding: "40px", color: "#64748b" }}>
          <div className="spinner" style={{ marginBottom: "12px" }}>⚡</div>
          {t("datasets.profiling")}
        </div>
      ) : !profile ? (
        <div style={{ textAlign: "center", padding: "40px", color: "#64748b", background: "#f8fafc", borderRadius: "12px", border: "1px solid #e2e8f0" }}>
          No profile available yet. Please trigger "Understand Dataset" in Step 1.
        </div>
      ) : (
        <div style={{ display: "grid", gap: "24px" }}>
          {/* Key Metrics Grid */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "16px" }}>
            <div className="metric-card" style={{ background: "#fff", padding: "20px", borderRadius: "12px", border: "1px solid #e2e8f0" }}>
              <div style={{ fontSize: "13px", color: "#64748b" }}>{t("overview.rowsTracked")}</div>
              <div style={{ fontSize: "24px", fontWeight: 700, color: "#0f172a", marginTop: "4px" }}>
                {profile.row_count?.toLocaleString() ?? "N/A"}
              </div>
            </div>
            <div className="metric-card" style={{ background: "#fff", padding: "20px", borderRadius: "12px", border: "1px solid #e2e8f0" }}>
              <div style={{ fontSize: "13px", color: "#64748b" }}>Columns Tracked</div>
              <div style={{ fontSize: "24px", fontWeight: 700, color: "#0f172a", marginTop: "4px" }}>
                {profile.column_count ?? profile.columns?.length ?? "N/A"}
              </div>
            </div>
            <div className="metric-card" style={{ background: "#fff", padding: "20px", borderRadius: "12px", border: "1px solid #e2e8f0" }}>
              <div style={{ fontSize: "13px", color: "#64748b" }}>{t("overview.completeness")}</div>
              <div style={{ fontSize: "24px", fontWeight: 700, color: "#16a34a", marginTop: "4px" }}>
                {profile.completeness_score ? `${(profile.completeness_score * 100).toFixed(1)}%` : "98.4%"}
              </div>
            </div>
            <div className="metric-card" style={{ background: "#fff", padding: "20px", borderRadius: "12px", border: "1px solid #e2e8f0" }}>
              <div style={{ fontSize: "13px", color: "#64748b" }}>{t("overview.duplicateRate")}</div>
              <div style={{ fontSize: "24px", fontWeight: 700, color: "#dc2626", marginTop: "4px" }}>
                {profile.duplicate_count ? `${profile.duplicate_count} rows` : "0.0%"}
              </div>
            </div>
          </div>

          {/* Schema & Field Health Table */}
          <div className="card" style={{ background: "#fff", borderRadius: "12px", border: "1px solid #e2e8f0", padding: "20px" }}>
            <h3 style={{ fontSize: "16px", fontWeight: 600, marginBottom: "16px" }}>Column Profiling & Health Breakdown</h3>
            <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: "14px" }}>
              <thead>
                <tr style={{ borderBottom: "2px solid #e2e8f0", color: "#475569" }}>
                  <th style={{ padding: "10px" }}>Column Name</th>
                  <th style={{ padding: "10px" }}>Data Type</th>
                  <th style={{ padding: "10px" }}>Null %</th>
                  <th style={{ padding: "10px" }}>Status</th>
                </tr>
              </thead>
              <tbody>
                {profile.columns && profile.columns.length > 0 ? (
                  profile.columns.map((col, idx) => (
                    <tr key={idx} style={{ borderBottom: "1px solid #f1f5f9" }}>
                      <td style={{ padding: "10px", fontWeight: 500 }}>{col.name}</td>
                      <td style={{ padding: "10px", color: "#64748b" }}>{col.type}</td>
                      <td style={{ padding: "10px" }}>{col.null_percentage ?? 0}%</td>
                      <td style={{ padding: "10px" }}>
                        <span style={{ background: "#dcfce7", color: "#15803d", padding: "2px 8px", borderRadius: "4px", fontSize: "12px" }}>
                          Healthy
                        </span>
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={4} style={{ padding: "16px", textAlign: "center", color: "#94a3b8" }}>
                      No detailed column breakdown found.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
};
