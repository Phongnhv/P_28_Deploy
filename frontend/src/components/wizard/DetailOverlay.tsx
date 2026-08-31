import { useEffect, type ReactNode } from "react";

/**
 * A full-screen reading surface for a panel that belongs to the current step.
 *
 * The buttons on the profile panel used to call `onNavigate`, which on step 1
 * had no case for "visualization" or "audit" — so they silently did nothing.
 * Wiring them to the wizard's step switcher would have been worse than the
 * dead button: leaving step 1 to read a chart loses the dataset you were in
 * the middle of preparing. The content opens over the page instead, and closing
 * it puts you back exactly where you were.
 */
export function DetailOverlay({
  title,
  eyebrow,
  onClose,
  closeLabel,
  children,
}: {
  title: string;
  eyebrow?: string;
  onClose: () => void;
  closeLabel: string;
  children: ReactNode;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    // The page underneath must not scroll while this is open, or closing the
    // overlay returns you to a different scroll position than you left.
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [onClose]);

  return (
    <div className="detail-overlay" role="dialog" aria-modal="true" aria-label={title}>
      <header className="detail-overlay-head">
        <div>
          {eyebrow && <span className="eyebrow">{eyebrow}</span>}
          <h2>{title}</h2>
        </div>
        <button type="button" className="detail-overlay-close" onClick={onClose} aria-label={closeLabel} title={closeLabel}>
          ✕
        </button>
      </header>
      <div className="detail-overlay-body">{children}</div>
    </div>
  );
}
