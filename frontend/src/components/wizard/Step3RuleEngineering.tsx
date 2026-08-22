import React from "react";
import { useI18n } from "../../i18n/context";

export interface RuleProposal {
  id: string;
  rule_type: string;
  column_name?: string;
  description: string;
  selected?: boolean;
  status?: string;
}

interface Step3RuleEngineeringProps {
  rules: RuleProposal[];
  selectedRuleIds: Set<string>;
  onToggleRule: (id: string) => void;
  onSelectAll: () => void;
  onDeselectAll: () => void;
  onGenerateRules: () => Promise<void>;
  onNext: () => void;
  onBack: () => void;
  loading: boolean;
}

export const Step3RuleEngineering: React.FC<Step3RuleEngineeringProps> = ({
  rules,
  selectedRuleIds,
  onToggleRule,
  onSelectAll,
  onDeselectAll,
  onGenerateRules,
  onNext,
  onBack,
  loading,
}) => {
  const { t } = useI18n();

  return (
    <div className="wizard-step-container" style={{ padding: "24px", maxWidth: "1200px", margin: "0 auto" }}>
      <div className="step-header" style={{ marginBottom: "24px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h2 style={{ fontSize: "20px", fontWeight: 600, color: "var(--color-text-main, #1e293b)" }}>
            {t("wizard.step3Title")}
          </h2>
          <p style={{ color: "var(--color-text-muted, #64748b)", fontSize: "14px", marginTop: "4px" }}>
            {t("wizard.step3Desc")}
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
            disabled={selectedRuleIds.size === 0}
            style={{
              padding: "8px 16px",
              background: selectedRuleIds.size === 0 ? "#94a3b8" : "#2563eb",
              color: "#fff",
              border: "none",
              borderRadius: "6px",
              fontWeight: 500,
              cursor: selectedRuleIds.size === 0 ? "not-allowed" : "pointer",
            }}
          >
            {t("wizard.next")}
          </button>
        </div>
      </div>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
        <button
          onClick={onGenerateRules}
          disabled={loading}
          style={{
            padding: "10px 20px",
            background: "#16a34a",
            color: "#fff",
            border: "none",
            borderRadius: "6px",
            fontWeight: 600,
            cursor: loading ? "not-allowed" : "pointer",
          }}
        >
          {loading ? "Generating rules…" : t("rules.generateRules")}
        </button>

        {rules.length > 0 && (
          <div style={{ display: "flex", gap: "12px" }}>
            <button
              onClick={onSelectAll}
              style={{
                padding: "6px 12px",
                background: "#f1f5f9",
                border: "1px solid #cbd5e1",
                borderRadius: "4px",
                fontSize: "13px",
                cursor: "pointer",
              }}
            >
              {t("rules.selectAll")}
            </button>
            <button
              onClick={onDeselectAll}
              style={{
                padding: "6px 12px",
                background: "#f1f5f9",
                border: "1px solid #cbd5e1",
                borderRadius: "4px",
                fontSize: "13px",
                cursor: "pointer",
              }}
            >
              {t("rules.deselectAll")}
            </button>
          </div>
        )}
      </div>

      <div className="card" style={{ background: "#fff", borderRadius: "12px", border: "1px solid #e2e8f0", padding: "20px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "16px" }}>
          <h3 style={{ fontSize: "16px", fontWeight: 600 }}>Rules Selection Panel</h3>
          <span style={{ fontSize: "14px", color: "#64748b" }}>
            Selected: <strong>{selectedRuleIds.size}</strong> / {rules.length}
          </span>
        </div>

        {rules.length === 0 ? (
          <div style={{ textAlign: "center", padding: "40px", color: "#64748b" }}>
            No rules available. Click "{t("rules.generateRules")}" to generate rules for the dataset.
          </div>
        ) : (
          <div style={{ display: "grid", gap: "12px" }}>
            {rules.map((rule) => {
              const isChecked = selectedRuleIds.has(rule.id);
              return (
                <div
                  key={rule.id}
                  onClick={() => onToggleRule(rule.id)}
                  style={{
                    padding: "16px",
                    borderRadius: "8px",
                    border: isChecked ? "1px solid #3b82f6" : "1px solid #e2e8f0",
                    background: isChecked ? "#f0f9ff" : "#fff",
                    display: "flex",
                    alignItems: "center",
                    gap: "16px",
                    cursor: "pointer",
                    transition: "all 0.15s ease",
                  }}
                >
                  <input
                    type="checkbox"
                    checked={isChecked}
                    onChange={() => {}} // Handled by parent div onClick
                    style={{ width: "18px", height: "18px", cursor: "pointer" }}
                  />
                  <div style={{ flex: 1 }}>
                    <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                      <span style={{ fontWeight: 600, color: "#1e293b" }}>{rule.rule_type}</span>
                      {rule.column_name && (
                        <span style={{ background: "#e2e8f0", padding: "2px 6px", borderRadius: "4px", fontSize: "12px", color: "#475569" }}>
                          {rule.column_name}
                        </span>
                      )}
                    </div>
                    <div style={{ fontSize: "14px", color: "#64748b", marginTop: "4px" }}>
                      {rule.description}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};
