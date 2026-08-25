import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { useI18n } from "../../i18n/context";
import type { TimelineNode } from "../../hooks/useNodeStream";
import { GraphNodeDef } from "../../graph/registry";
import { hasPreview } from "../../graph/preview";
import { humanizeNode } from "../../graph/preview";

interface NodeDetailDrawerProps {
  node: TimelineNode | null;
  def?: GraphNodeDef;
  onClose: () => void;
}

export function NodeDetailDrawer({ node, def, onClose }: NodeDetailDrawerProps) {
  const { t } = useI18n();
  const [showRaw, setShowRaw] = useState(false);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const previousActiveElementRef = useRef<HTMLElement | null>(null);

  // Reset raw JSON toggle when node changes
  useEffect(() => {
    setShowRaw(false);
  }, [node]);

  // Accessibility: Focus close button on open, restore focus on close, ESC to close
  useEffect(() => {
    if (node) {
      previousActiveElementRef.current = document.activeElement as HTMLElement;
      // Focus close button on a short timeout to let the DOM settle
      const timer = setTimeout(() => {
        closeButtonRef.current?.focus();
      }, 50);

      const handleKeyDown = (e: KeyboardEvent) => {
        if (e.key === "Escape") {
          onClose();
        }
      };
      window.addEventListener("keydown", handleKeyDown);

      return () => {
        clearTimeout(timer);
        window.removeEventListener("keydown", handleKeyDown);
        if (previousActiveElementRef.current) {
          previousActiveElementRef.current.focus();
        }
      };
    }
  }, [node, onClose]);

  if (!node || !def) return null;

  const showOutput = hasPreview(node.preview);

  return (
    <div
      className="dialog-backdrop"
      onClick={onClose}
      style={{ zIndex: 100 }}
    >
      <div
        className="node-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="drawer-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
          <div>
            <h2 id="drawer-title" style={{ margin: 0, fontSize: "20px", color: "var(--ink)", fontWeight: 700 }}>
              {t(def.titleKey) || def.name}
            </h2>
            <span
              className="status-pill info"
              style={{
                marginTop: "6px",
                display: "inline-flex",
                fontSize: "11px",
                textTransform: "uppercase"
              }}
            >
              {t(`graph.owner.${def.owner}`) || def.owner}
            </span>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            aria-label={t("graph.detail.close") || "Close"}
            style={{
              background: "none",
              border: "none",
              color: "var(--muted)",
              cursor: "pointer",
              padding: "4px",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              borderRadius: "50%",
            }}
            className="close-btn-hover"
          >
            <X size={20} />
          </button>
        </div>

        <p style={{ margin: "16px 0", fontSize: "13px", color: "var(--ink-soft)", lineHeight: 1.5 }}>
          {t(def.purposeKey)}
        </p>

        {/* Data Inputs & Outputs (Reads / Produces) */}
        <div style={{ display: "flex", flexDirection: "column", gap: "14px", margin: "16px 0" }}>
          <div>
            <span style={{ fontSize: "11px", fontWeight: 700, textTransform: "uppercase", color: "var(--muted)", display: "block", marginBottom: "6px" }}>
              {t("graph.io.readsLabel") || "Reads"}
            </span>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
              {def.reads.length > 0 ? (
                def.reads.map((key) => (
                  <span key={key} className="evidence-chip">
                    {t(`graph.io.${key}`) || humanizeNode(key)}
                  </span>
                ))
              ) : (
                <span style={{ fontSize: "12px", color: "var(--muted)", fontStyle: "italic" }}>None</span>
              )}
            </div>
          </div>

          <div>
            <span style={{ fontSize: "11px", fontWeight: 700, textTransform: "uppercase", color: "var(--muted)", display: "block", marginBottom: "6px" }}>
              {t("graph.io.producesLabel") || "Produces"}
            </span>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
              {def.produces.length > 0 ? (
                def.produces.map((key) => (
                  <span key={key} className="evidence-chip">
                    {t(`graph.io.${key}`) || humanizeNode(key)}
                  </span>
                ))
              ) : (
                <span style={{ fontSize: "12px", color: "var(--muted)", fontStyle: "italic" }}>None</span>
              )}
            </div>
          </div>
        </div>

        <hr style={{ border: "none", borderTop: "1px solid var(--border)", margin: "16px 0" }} />

        {/* Node status / Execution metadata */}
        <div style={{ display: "flex", alignItems: "center", gap: "8px", margin: "12px 0" }}>
          <span style={{ fontSize: "13px", color: "var(--muted)" }}>Status:</span>
          <span
            style={{
              fontSize: "12px",
              fontWeight: 700,
              color: `var(--status-${node.status})`,
              textTransform: "uppercase",
            }}
          >
            {t(`nodeStream.status${node.status.charAt(0).toUpperCase() + node.status.slice(1)}`) || node.status}
          </span>
        </div>

        {/* Node error if failed */}
        {node.status === "failed" && node.error && (
          <div
            style={{
              margin: "12px 0",
              padding: "12px",
              background: "rgba(185, 28, 28, 0.06)",
              border: "1px solid var(--status-failed)",
              borderRadius: "8px",
              fontSize: "13px",
              color: "var(--status-failed)",
              wordBreak: "break-word",
            }}
          >
            <strong style={{ display: "block", marginBottom: "4px" }}>Error:</strong>
            {node.error}
          </div>
        )}

        {/* Outputs Preview */}
        <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
          <span style={{ fontSize: "11px", fontWeight: 700, textTransform: "uppercase", color: "var(--muted)", display: "block", marginBottom: "6px" }}>
            {t("graph.detail.outputLabel") || "Output"}
          </span>
          {showOutput ? (
            <div style={{ display: "flex", flexDirection: "column", gap: "10px", flex: 1, minHeight: 0 }}>
              <button
                type="button"
                onClick={() => setShowRaw((prev) => !prev)}
                style={{
                  alignSelf: "flex-start",
                  background: "none",
                  border: "none",
                  color: "var(--accent)",
                  fontSize: "13px",
                  padding: 0,
                  cursor: "pointer"
                }}
              >
                {showRaw ? t("nodeStream.hidePayload") || "Hide output" : t("nodeStream.showPayload") || "Show output"}
              </button>
              
              {showRaw ? (
                <pre
                  style={{
                    margin: 0,
                    padding: "12px",
                    background: "var(--surface-muted)",
                    border: "1px solid var(--border)",
                    borderRadius: "8px",
                    fontSize: "12px",
                    lineHeight: 1.5,
                    color: "var(--ink-soft)",
                    overflow: "auto",
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                    maxHeight: "350px",
                    flex: 1
                  }}
                >
                  {JSON.stringify(node.preview, null, 2)}
                </pre>
              ) : (
                <div
                  style={{
                    padding: "12px",
                    background: "var(--surface-muted)",
                    border: "1px solid var(--border)",
                    borderRadius: "8px",
                    fontSize: "13px",
                    color: "var(--ink)",
                    lineHeight: 1.5,
                    overflowY: "auto",
                    maxHeight: "200px"
                  }}
                >
                  {typeof node.preview === "string" ? (
                    node.preview
                  ) : typeof node.preview === "object" ? (
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "12px" }}>
                      <tbody>
                        {Object.entries(node.preview as Record<string, unknown>).map(([key, val]) => (
                          <tr key={key} style={{ borderBottom: "1px solid var(--border)" }}>
                            <td style={{ padding: "6px 0", fontWeight: 600, color: "var(--ink-soft)", verticalAlign: "top", width: "40%" }}>
                              {humanizeNode(key)}
                            </td>
                            <td style={{ padding: "6px 0 6px 8px", color: "var(--ink)", verticalAlign: "top", wordBreak: "break-all" }}>
                              {typeof val === "object" ? JSON.stringify(val) : String(val)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    String(node.preview)
                  )}
                </div>
              )}
            </div>
          ) : (
            <p style={{ fontSize: "13px", color: "var(--muted)", margin: "4px 0 0", fontStyle: "italic" }}>
              {t("graph.detail.noOutput") || "This step produced no preview."}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
