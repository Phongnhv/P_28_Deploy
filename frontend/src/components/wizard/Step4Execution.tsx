import React from "react";
import { useI18n } from "../../i18n/context";

interface Step4ExecutionProps {
  selectedRulesCount: number;
  onRunChecks: () => Promise<void>;
  isRunning: boolean;
  runStatus: string | null;
  logs: string[];
  onNext: () => void;
  onBack: () => void;
}

export const Step4Execution: React.FC<Step4ExecutionProps> = ({
  selectedRulesCount,
  onRunChecks,
  isRunning,
  runStatus,
  logs,
  onNext,
  onBack,
}) => {
  const { t } = useI18n();

  return (
    <div className="wizard-step-container" style={{ padding: "24px", maxWidth: "1200px", margin: "0 auto" }}>
      <div className="step-header" style={{ marginBottom: "24px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h2 style={{ fontSize: "20px", fontWeight: 600, color: "var(--color-text-main, #1e293b)" }}>
            {t("wizard.step4Title")}
          </h2>
          <p style={{ color: "var(--color-text-muted, #64748b)", fontSize: "14px", marginTop: "4px" }}>
            {t("wizard.step4Desc")}
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
            disabled={!runStatus || isRunning}
            style={{
              padding: "8px 16px",
              background: !runStatus || isRunning ? "#94a3b8" : "#2563eb",
              color: "#fff",
              border: "none",
              borderRadius: "6px",
              fontWeight: 500,
              cursor: !runStatus || isRunning ? "not-allowed" : "pointer",
            }}
          >
            {t("wizard.next")}
          </button>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: "24px" }}>
        {/* Left Column: Action Card */}
        <div className="card" style={{ background: "#fff", borderRadius: "12px", border: "1px solid #e2e8f0", padding: "24px", height: "fit-content" }}>
          <h3 style={{ fontSize: "16px", fontWeight: 600, marginBottom: "12px" }}>Execute Selected Rules</h3>
          <p style={{ fontSize: "14px", color: "#64748b", marginBottom: "20px" }}>
            Ready to run <strong>{selectedRulesCount}</strong> rules against the active dataset.
          </p>

          <button
            onClick={onRunChecks}
            disabled={isRunning || selectedRulesCount === 0}
            style={{
              width: "100%",
              padding: "12px",
              background: "#2563eb",
              color: "#fff",
              border: "none",
              borderRadius: "8px",
              fontWeight: 600,
              fontSize: "15px",
              cursor: isRunning || selectedRulesCount === 0 ? "not-allowed" : "pointer",
              marginBottom: "16px",
            }}
          >
            {isRunning ? t("runs.executing") : t("runs.runChecks")}
          </button>

          {runStatus && (
            <div style={{ padding: "12px", background: "#f8fafc", borderRadius: "8px", border: "1px solid #e2e8f0" }}>
              <div style={{ fontSize: "13px", color: "#64748b" }}>Status</div>
              <div style={{ fontWeight: 600, color: runStatus === "completed" ? "#16a34a" : "#2563eb", marginTop: "2px" }}>
                {runStatus.toUpperCase()}
              </div>
            </div>
          )}
        </div>

        {/* Right Column: Execution Terminal / Logs */}
        <div className="card" style={{ background: "#0f172a", color: "#f8fafc", borderRadius: "12px", padding: "20px", minHeight: "350px", fontFamily: "monospace" }}>
          <div style={{ display: "flex", justifyContent: "space-between", borderBottom: "1px solid #334155", paddingBottom: "12px", marginBottom: "16px" }}>
            <span style={{ fontWeight: 600, color: "#94a3b8" }}>{t("runs.liveLogs")}</span>
            <span style={{ fontSize: "12px", color: "#64748b" }}>Real-time streaming</span>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: "8px", fontSize: "13px", maxHeight: "280px", overflowY: "auto" }}>
            {logs.length === 0 ? (
              <span style={{ color: "#64748b" }}>Click "{t("runs.runChecks")}" to begin execution.</span>
            ) : (
              logs.map((log, i) => (
                <div key={i} style={{ color: log.includes("FAILED") || log.includes("Error") ? "#f87171" : "#38bdf8" }}>
                  {log}
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
