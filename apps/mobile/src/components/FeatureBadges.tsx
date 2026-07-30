import React, { useMemo } from "react";
import { StyleSheet, Text, View } from "react-native";

import type { DocumentFeature, FeatureGroup } from "../api/features";
import { colors, spacing, typeScale } from "../theme";

// Per-document enrichment badges, mirroring the web (apps/ui/src/DocumentsPanel.tsx):
// status-tinted "label glyph" chips (✓ done, ✗ failed, … pending/running) built only from the
// document's actual feature-ledger rows — features with no row are omitted, like the web.
// Group chips first (entities, knowledge_graph), then individual chips sorted alphabetically by
// feature name with the web's friendly labels (text, rag, meta, tags, recs, thumb). The overlay
// variant (grid thumbnails) shrinks the chips and supports a cap with a "+N" overflow chip,
// exactly like the web's ThumbnailFeatureChips; the parent supplies the translucent scrim.
type GroupStatus = "failed" | "running" | "pending" | "done";

// Individual chip labels, mirroring the web's friendly names (DocumentsPanel.tsx).
const FEATURE_LABELS: Record<string, string> = {
  extract: "text",
  chunk_embed: "rag",
  doc_metadata: "meta",
  doc_classify: "tags",
  structured_records: "recs",
  thumbnail: "thumb",
};

// Same tints as the web's .feat-* classes: 18% status fill, 50% border, full-color text.
const STATUS_TINT: Record<GroupStatus, { bg: string; border: string; text: string }> = {
  done: { bg: "rgba(46, 160, 67, 0.18)", border: "rgba(46, 160, 67, 0.5)", text: colors.success },
  failed: { bg: "rgba(220, 60, 60, 0.18)", border: "rgba(220, 60, 60, 0.5)", text: colors.danger },
  running: { bg: "rgba(255, 196, 0, 0.18)", border: "rgba(255, 196, 0, 0.5)", text: colors.warning },
  pending: { bg: "rgba(255, 196, 0, 0.18)", border: "rgba(255, 196, 0, 0.5)", text: colors.warning },
};

function glyph(status: GroupStatus): string {
  return status === "done" ? "✓" : status === "failed" ? "✗" : "…";
}

function worstOf(statuses: DocumentFeature["status"][]): GroupStatus {
  if (statuses.includes("failed")) return "failed";
  if (statuses.includes("running")) return "running";
  if (statuses.includes("pending")) return "pending";
  return "done";
}

type ChipData = { key: string; label: string; status: GroupStatus };

function Chip({ label, status, small }: ChipData & { small: boolean }) {
  const tint = STATUS_TINT[status];
  return (
    <View
      style={[
        styles.chip,
        small && styles.chipSmall,
        { backgroundColor: tint.bg, borderColor: tint.border },
      ]}
    >
      <Text style={[small ? styles.chipTextSmall : styles.chipText, { color: tint.text }]}>
        {label} {glyph(status)}
      </Text>
    </View>
  );
}

export function FeatureBadges({
  groups,
  rows,
  overlay = false,
  cap,
}: {
  groups: FeatureGroup[];
  rows: DocumentFeature[];
  /** Grid-thumbnail variant: tiny chips over the parent's scrim, "+N" overflow when capped. */
  overlay?: boolean;
  /** Max visible chips; the rest collapse into a "+N" chip (web: THUMB_CHIP_CAP). */
  cap?: number;
}) {
  const chips = useMemo<ChipData[]>(() => {
    const byFeature = new Map<string, DocumentFeature["status"][]>();
    for (const r of rows) {
      const list = byFeature.get(r.feature) ?? [];
      list.push(r.status);
      byFeature.set(r.feature, list);
    }
    const out: ChipData[] = [];
    // Group chips first — only groups with at least one member row on this document (web rule).
    const grouped = new Set<string>();
    for (const g of groups) {
      for (const m of g.badge_members) grouped.add(m);
      const statuses = g.badge_members.flatMap((f) => byFeature.get(f) ?? []);
      if (statuses.length === 0) continue;
      out.push({ key: g.id, label: g.label, status: worstOf(statuses) });
    }
    // Individual (ungrouped) features, alphabetical by feature name (web: localeCompare).
    const individuals = [...byFeature.keys()]
      .filter((f) => !grouped.has(f))
      .sort((a, b) => a.localeCompare(b));
    for (const f of individuals) {
      out.push({ key: f, label: FEATURE_LABELS[f] ?? f, status: worstOf(byFeature.get(f)!) });
    }
    return out;
  }, [groups, rows]);

  if (chips.length === 0) return null;

  const visible = cap !== undefined ? chips.slice(0, cap) : chips;
  const hiddenCount = chips.length - visible.length;

  return (
    <View style={styles.row}>
      {visible.map((c) => (
        <Chip key={c.key} label={c.label} status={c.status} small={overlay} />
      ))}
      {hiddenCount > 0 && (
        <View style={[styles.chip, overlay && styles.chipSmall, styles.chipOverflow]}>
          <Text style={[overlay ? styles.chipTextSmall : styles.chipText, styles.chipOverflowText]}>
            +{hiddenCount}
          </Text>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", flexWrap: "wrap", gap: spacing.xs },
  chip: {
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
  },
  // Overlay (thumbnail) chips: tiny, like the web's 0.55rem tiles.
  chipSmall: { borderRadius: 7, paddingHorizontal: 4, paddingVertical: 0 },
  chipText: { ...typeScale.small },
  chipTextSmall: { fontSize: 9, fontWeight: "500" },
  chipOverflow: {
    backgroundColor: "rgba(127, 127, 127, 0.22)",
    borderColor: "rgba(127, 127, 127, 0.45)",
  },
  chipOverflowText: { color: colors.muted },
});
