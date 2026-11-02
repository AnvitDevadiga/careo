export type Theme = "light" | "dark";
const KEY = "careo-theme";

export function storedTheme(): Theme | null {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : null;
  } catch {
    return null;
  }
}

/** The theme in effect: an explicit choice wins; otherwise the OS preference; otherwise light. */
export function currentTheme(): Theme {
  const stored = storedTheme();
  if (stored) return stored;
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    /* storage unavailable: theme still applies for this page view */
  }
}
