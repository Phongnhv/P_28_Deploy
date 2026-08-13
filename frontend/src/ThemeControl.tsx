import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

type Theme = "light" | "dark";

function getInitialTheme(): Theme {
  return localStorage.getItem("ridepulse.theme") === "dark" ? "dark" : "light";
}

export default function ThemeControl() {
  const [theme, setTheme] = useState<Theme>(getInitialTheme);
  const [mountNode, setMountNode] = useState<HTMLSpanElement | null>(null);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("ridepulse.theme", theme);
  }, [theme]);

  useEffect(() => {
    let host: HTMLSpanElement | null = null;

    const mount = () => {
      const actions = document.querySelector(".topbar-actions");
      if (!actions || host) return;
      host = document.createElement("span");
      host.className = "theme-control-slot";
      actions.insertBefore(host, actions.lastElementChild);
      setMountNode(host);
    };

    mount();
    const observer = new MutationObserver(mount);
    observer.observe(document.body, { childList: true, subtree: true });
    return () => {
      observer.disconnect();
      host?.remove();
    };
  }, []);

  const nextTheme = theme === "light" ? "dark" : "light";

  if (!mountNode) return null;

  return createPortal(
    <button
      className="theme-control"
      type="button"
      onClick={() => setTheme(nextTheme)}
      aria-label={`Switch to ${nextTheme === "dark" ? "night" : "white"} mode`}
      title={`Switch to ${nextTheme === "dark" ? "night" : "white"} mode`}
    >
      <span aria-hidden="true">{theme === "light" ? "☾" : "☀"}</span>
    </button>
    ,
    mountNode,
  );
}
