import { useEffect, useState } from "react";

type Theme = "light" | "dark";

function getInitialTheme(): Theme {
  return (localStorage.getItem("datapulse.theme") || localStorage.getItem("ridepulse.theme")) === "dark" ? "dark" : "light";
}

export default function ThemeControl() {
  const [theme, setTheme] = useState<Theme>(getInitialTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("datapulse.theme", theme);
  }, [theme]);
  const nextTheme = theme === "light" ? "dark" : "light";

  return (
    <button
      className="theme-control"
      type="button"
      onClick={() => setTheme(nextTheme)}
      aria-label={`Switch to ${nextTheme === "dark" ? "night" : "white"} mode`}
      title={`Switch to ${nextTheme === "dark" ? "night" : "white"} mode`}
    >
      <span aria-hidden="true">{theme === "light" ? "☾" : "☀"}</span>
    </button>
  );
}
