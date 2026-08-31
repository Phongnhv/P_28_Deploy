import { useEffect, useRef, useState } from "react";

export interface AppNotification {
  id: string;
  kind: "success" | "error";
  message: string;
  at: string;
}

function formatTimestamp(iso: string, vi: boolean): string {
  const date = new Date(iso);
  const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
  if (seconds < 60) return vi ? "vừa xong" : "just now";
  if (seconds < 3600) {
    const minutes = Math.floor(seconds / 60);
    return vi ? `${minutes} phút trước` : `${minutes}m ago`;
  }
  if (seconds < 86400) {
    const hours = Math.floor(seconds / 3600);
    return vi ? `${hours} giờ trước` : `${hours}h ago`;
  }
  return date.toLocaleString(vi ? "vi-VN" : "en-US", { hour12: !vi });
}

/**
 * The running log of what the app has told you, behind one bell.
 *
 * Toasts vanish after 3.5 seconds. Anything you missed — a job that finished
 * while you were reading another panel, an error that scrolled past — was gone
 * with no way back. The toasts still appear; this keeps them.
 */
export function NotificationBell({
  notifications,
  unreadCount,
  language,
  onOpen,
  onClear,
}: {
  notifications: AppNotification[];
  unreadCount: number;
  language: "en" | "vi";
  onOpen: () => void;
  onClear: () => void;
}) {
  const vi = language === "vi";
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function toggle() {
    setOpen((previous) => {
      // Opening is what marks them read; a badge that clears on any click
      // would hide notifications you never actually looked at.
      if (!previous) onOpen();
      return !previous;
    });
  }

  return (
    <div className="notif" ref={containerRef}>
      <button
        type="button"
        className={`notif-button ${open ? "active" : ""}`}
        onClick={toggle}
        aria-haspopup="true"
        aria-expanded={open}
        aria-label={
          unreadCount
            ? vi ? `Thông báo, ${unreadCount} chưa đọc` : `Notifications, ${unreadCount} unread`
            : vi ? "Thông báo" : "Notifications"
        }
        title={vi ? "Thông báo" : "Notifications"}
      >
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">
          <path
            d="M12 3a5.5 5.5 0 0 0-5.5 5.5v3.1L5 15h14l-1.5-3.4V8.5A5.5 5.5 0 0 0 12 3Z"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinejoin="round"
          />
          <path d="M9.8 18a2.2 2.2 0 0 0 4.4 0" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </svg>
        {unreadCount > 0 && <span className="notif-badge">{unreadCount > 9 ? "9+" : unreadCount}</span>}
      </button>

      {open && (
        <div className="notif-panel" role="dialog" aria-label={vi ? "Thông báo" : "Notifications"}>
          <header className="notif-panel-head">
            <strong>{vi ? "Thông báo" : "Notifications"}</strong>
            {notifications.length > 0 && (
              <button type="button" className="notif-clear" onClick={onClear}>
                {vi ? "Xoá tất cả" : "Clear all"}
              </button>
            )}
          </header>
          <div className="notif-list">
            {notifications.length === 0 ? (
              <p className="notif-empty">{vi ? "Chưa có thông báo nào." : "No notifications yet."}</p>
            ) : (
              notifications.map((item) => (
                <article key={item.id} className={`notif-item ${item.kind}`}>
                  <span className="notif-item-dot" aria-hidden="true" />
                  <div className="notif-item-body">
                    <p>{item.message}</p>
                    <time dateTime={item.at}>{formatTimestamp(item.at, vi)}</time>
                  </div>
                </article>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
