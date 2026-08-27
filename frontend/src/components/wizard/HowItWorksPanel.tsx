import { GRAPHS } from "../../graph/registry";
import { GraphMap } from "./GraphMap";
import { TimelineNode } from "../../hooks/useNodeStream";
import { useI18n } from "../../i18n/context";

interface HowItWorksPanelProps {
  nodesByName?: Record<string, TimelineNode>;
}

export function HowItWorksPanel({ nodesByName }: HowItWorksPanelProps) {
  const { t } = useI18n();

  return (
    <details
      style={{
        marginTop: "16px",
        padding: "16px",
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: "12px",
      }}
      className="how-it-works-details"
    >
      <summary
        style={{
          cursor: "pointer",
          fontWeight: 600,
          fontSize: "14px",
          color: "var(--accent)",
          listStyle: "none",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          outline: "none"
        }}
      >
        <span>{t("graph.howItWorks.title") || "How the agents work"}</span>
        <span className="details-toggle-label" style={{ fontSize: "12px" }} />
      </summary>
      
      <div style={{ marginTop: "16px", display: "flex", flexDirection: "column", gap: "24px" }}>
        <p style={{ margin: 0, fontSize: "13px", color: "var(--muted)", lineHeight: 1.5 }}>
          {t("graph.howItWorks.subtitle") || "Each pipeline runs as a graph of nodes. This reference renders even before a run."}
          {!nodesByName && (
            <span style={{ display: "block", marginTop: "4px", fontStyle: "italic" }}>
              {t("graph.howItWorks.noRunHint") || "Start a run to see live status on these nodes."}
            </span>
          )}
        </p>

        {GRAPHS.map((g) => (
          <div
            key={g.graphId}
            style={{
              padding: "16px",
              background: "var(--surface-muted)",
              border: "1px solid var(--border)",
              borderRadius: "8px",
            }}
          >
            <div style={{ marginBottom: "12px" }}>
              <h3 style={{ margin: 0, fontSize: "15px", color: "var(--ink)", fontWeight: 700 }}>
                {t(g.titleKey)}
              </h3>
              <p style={{ margin: "4px 0 0", fontSize: "12px", color: "var(--muted)" }}>
                {t(g.purposeKey)}
              </p>
            </div>

            <GraphMap graph={g} nodesByName={nodesByName} />
            
            {/* Legend or list of nodes within this group */}
            <div style={{ marginTop: "12px", borderTop: "1px solid var(--border)", paddingTop: "8px" }}>
              <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "10px" }}>
                {g.nodes.map((n) => (
                  <li key={n.name} style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                      <strong style={{ fontSize: "12px", color: "var(--ink)" }}>{t(n.titleKey)}</strong>
                      <span className="status-pill gray" style={{ fontSize: "9px", padding: "1px 4px", height: "auto" }}>
                        {t(`graph.owner.${n.owner}`) || n.owner}
                      </span>
                    </div>
                    <span style={{ fontSize: "11px", color: "var(--muted)" }}>{t(n.purposeKey)}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        ))}
      </div>
    </details>
  );
}
