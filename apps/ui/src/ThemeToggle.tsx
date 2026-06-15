import { useEffect, useState } from "react";

type Theme = "light" | "dark";

const KEY = "doktok-theme";

function currentTheme(): Theme {
  const attr = document.documentElement.getAttribute("data-theme");
  return attr === "light" ? "light" : "dark";
}

function apply(theme: Theme): void {
  document.documentElement.setAttribute("data-theme", theme);
}

/**
 * Light/dark theme switch (M8): a sliding toggle with a sun on the left and a moon on the right.
 * The theme is applied to <html data-theme> before first paint by an inline script in index.html;
 * this control reflects + flips it, persisting an explicit choice to localStorage. With no stored
 * choice it follows the system preference live.
 */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(currentTheme);

  // Follow the system preference until the user makes an explicit choice.
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = (e: MediaQueryListEvent) => {
      if (localStorage.getItem(KEY)) return; // user override wins
      const next: Theme = e.matches ? "light" : "dark";
      apply(next);
      setTheme(next);
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const toggle = () => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    apply(next);
    try {
      localStorage.setItem(KEY, next);
    } catch {
      /* persistence is best-effort */
    }
    setTheme(next);
  };

  const isDark = theme === "dark";
  return (
    <button
      type="button"
      role="switch"
      aria-checked={isDark}
      aria-label="Dark mode"
      className="theme-toggle"
      onClick={toggle}
      title={isDark ? "Switch to light mode" : "Switch to dark mode"}
    >
      <span className="theme-toggle-icon theme-toggle-sun" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4" />
        </svg>
      </span>
      <span className="theme-toggle-icon theme-toggle-moon" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor">
          <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
        </svg>
      </span>
      <span className="theme-toggle-knob" aria-hidden="true" />
    </button>
  );
}
