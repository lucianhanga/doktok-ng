// Design tokens mirroring the web UI's spartan dark theme (apps/ui/src/styles.css).
// Single source of truth for colors/typography so the phone app reads as the same product.
export const colors = {
  bg: "#0f141b", // app background
  surface: "#151b24", // cards, panels
  surfaceAlt: "#1a222e",
  border: "#2a3342",
  borderStrong: "#3a465a",
  text: "#dbe4ee",
  muted: "#8b98ab",
  accent: "#6ea8fe",
  accentSoft: "#2b4a7a",
  success: "#7ee787",
  warning: "#e3b341",
  danger: "#ff7b72",
} as const;

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
} as const;

export const typeScale = {
  title: { fontSize: 20, fontWeight: "600" as const, color: colors.text },
  section: { fontSize: 16, fontWeight: "600" as const, color: colors.text },
  body: { fontSize: 14, fontWeight: "400" as const, color: colors.text },
  muted: { fontSize: 13, fontWeight: "400" as const, color: colors.muted },
  small: { fontSize: 12, fontWeight: "400" as const, color: colors.muted },
} as const;
