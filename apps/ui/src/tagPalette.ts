/** The curated tag color palette (epic #543): per-token colors for light AND dark themes, so a
 * tag chip keeps WCAG contrast in both (Carbon/Primer pattern: strong dot + low-alpha tint).
 * The token set MUST mirror TAG_PALETTE in core/doktok_core/tags/normalize.py. */

import type { CSSProperties } from "react";

export interface TagColor {
  /** The solid hue (dots, swatches). */
  dot: string;
  /** Low-alpha tint for chip backgrounds. */
  bg: string;
  /** Readable text on the tint. */
  text: string;
  /** Chip border. */
  border: string;
}

const LIGHT: Record<string, TagColor> = {
  slate: { dot: "#64748b", bg: "#f1f5f9", text: "#334155", border: "#cbd5e1" },
  gray: { dot: "#6b7280", bg: "#f3f4f6", text: "#374151", border: "#d1d5db" },
  red: { dot: "#dc2626", bg: "#fef2f2", text: "#991b1b", border: "#fecaca" },
  orange: { dot: "#ea580c", bg: "#fff7ed", text: "#9a3412", border: "#fed7aa" },
  amber: { dot: "#d97706", bg: "#fffbeb", text: "#92400e", border: "#fde68a" },
  green: { dot: "#16a34a", bg: "#f0fdf4", text: "#166534", border: "#bbf7d0" },
  teal: { dot: "#0d9488", bg: "#f0fdfa", text: "#115e59", border: "#99f6e4" },
  blue: { dot: "#2563eb", bg: "#eff6ff", text: "#1e40af", border: "#bfdbfe" },
  violet: { dot: "#7c3aed", bg: "#f5f3ff", text: "#5b21b6", border: "#ddd6fe" },
  pink: { dot: "#db2777", bg: "#fdf2f8", text: "#9d174d", border: "#fbcfe8" },
};

const DARK: Record<string, TagColor> = {
  slate: { dot: "#94a3b8", bg: "rgba(148,163,184,0.14)", text: "#e2e8f0", border: "#475569" },
  gray: { dot: "#9ca3af", bg: "rgba(156,163,175,0.14)", text: "#e5e7eb", border: "#4b5563" },
  red: { dot: "#f87171", bg: "rgba(248,113,113,0.14)", text: "#fecaca", border: "#7f1d1d" },
  orange: { dot: "#fb923c", bg: "rgba(251,146,60,0.14)", text: "#fed7aa", border: "#7c2d12" },
  amber: { dot: "#fbbf24", bg: "rgba(251,191,36,0.14)", text: "#fde68a", border: "#78350f" },
  green: { dot: "#4ade80", bg: "rgba(74,222,128,0.14)", text: "#bbf7d0", border: "#14532d" },
  teal: { dot: "#2dd4bf", bg: "rgba(45,212,191,0.14)", text: "#99f6e4", border: "#134e4a" },
  blue: { dot: "#60a5fa", bg: "rgba(96,165,250,0.14)", text: "#bfdbfe", border: "#1e3a8a" },
  violet: { dot: "#a78bfa", bg: "rgba(167,139,250,0.14)", text: "#ddd6fe", border: "#4c1d95" },
  pink: { dot: "#f472b6", bg: "rgba(244,114,182,0.14)", text: "#fbcfe8", border: "#831843" },
};

export const TAG_PALETTE_TOKENS = Object.keys(LIGHT);

function themeIsLight(): boolean {
  return document.documentElement.getAttribute("data-theme") === "light";
}

/** The per-theme colors for a palette token (unknown tokens fall back to slate). */
export function tagColor(token: string): TagColor {
  const map = themeIsLight() ? LIGHT : DARK;
  return map[token] ?? map.slate;
}

/** Chip style for a tag badge (rounded pill + dot + tint), theme-aware. */
export function tagChipStyle(token: string): CSSProperties {
  const c = tagColor(token);
  return { backgroundColor: c.bg, color: c.text, borderColor: c.border };
}
