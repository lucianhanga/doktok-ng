import React from "react";
import { StyleSheet, Text, TouchableOpacity, View } from "react-native";

import { colors, spacing } from "../theme";

// Tag chips (#777). `color` is a palette TOKEN (never hex) - this dark-theme map mirrors the
// web's tagPalette DARK table (apps/ui/src/tagPalette.ts); unknown tokens fall back to slate.
interface TagColor {
  dot: string;
  bg: string;
  text: string;
  border: string;
}

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

export function tagColor(token: string): TagColor {
  return DARK[token] ?? DARK.slate;
}

export function TagChip({
  name,
  color,
  small = false,
  onPress,
}: {
  name: string;
  /** Palette token from the API (Tag.color). */
  color: string;
  small?: boolean;
  onPress?: () => void;
}) {
  const c = tagColor(color);
  const chip = (
    <View
      style={[
        styles.chip,
        small && styles.chipSmall,
        { backgroundColor: c.bg, borderColor: c.border },
      ]}
    >
      <View style={[styles.dot, { backgroundColor: c.dot }]} />
      <Text style={[styles.text, small && styles.textSmall, { color: c.text }]} numberOfLines={1}>
        {name}
      </Text>
    </View>
  );
  if (!onPress) return chip;
  return (
    <TouchableOpacity onPress={onPress} activeOpacity={0.7}>
      {chip}
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  chip: {
    flexDirection: "row",
    alignItems: "center",
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: spacing.sm,
    paddingVertical: 1,
    gap: 5,
    maxWidth: 180,
  },
  chipSmall: { borderRadius: 8, paddingHorizontal: 6, paddingVertical: 0, gap: 4 },
  dot: { width: 6, height: 6, borderRadius: 3 },
  text: { fontSize: 12, color: colors.text },
  textSmall: { fontSize: 10 },
});
