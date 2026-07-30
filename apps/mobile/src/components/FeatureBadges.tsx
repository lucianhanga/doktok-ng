import React, { useMemo } from "react";
import { StyleSheet, Text, View } from "react-native";

import type { DocumentFeature, FeatureGroup } from "../api/features";
import { colors, spacing, typeScale } from "../theme";

// Per-document enrichment badges (#814), same grouping/semantics as the web: one chip per server
// feature group (entities, knowledge_graph) PLUS individual chips for ungrouped features with the
// web's friendly labels (text, rag, meta, tags, recs, thumb). Worst-of status wins
// (failed > running > pending > done); features with no ledger row render as muted "not started".
type GroupStatus = "failed" | "running" | "pending" | "done" | "none";

// Individual chip labels, mirroring the web's friendly names (DocumentsPanel.tsx).
const FEATURE_LABELS: Record<string, string> = {
  extract: "text",
  chunk_embed: "rag",
  doc_metadata: "meta",
  doc_classify: "tags",
  structured_records: "recs",
  thumbnail: "thumb",
};

// Display order for the individual chips (groups sit between rag and meta, like the web).
const INDIVIDUAL_ORDER = [
  "extract",
  "chunk_embed",
  "doc_metadata",
  "doc_classify",
  "structured_records",
  "thumbnail",
];

const STATUS_COLOR: Record<GroupStatus, string> = {
  failed: colors.danger,
  running: colors.warning,
  pending: colors.muted,
  done: colors.success,
  none: colors.borderStrong,
};

function worstOf(statuses: DocumentFeature["status"][]): GroupStatus {
  if (statuses.includes("failed")) return "failed";
  if (statuses.includes("running")) return "running";
  if (statuses.includes("pending")) return "pending";
  if (statuses.length > 0) return "done";
  return "none";
}

function Chip({ label, status }: { label: string; status: GroupStatus }) {
  return (
    <View style={styles.chip}>
      <View style={[styles.dot, { backgroundColor: STATUS_COLOR[status] }]} />
      <Text style={styles.chipText}>{label}</Text>
    </View>
  );
}

export function FeatureBadges({
  groups,
  rows,
  compact = true,
}: {
  groups: FeatureGroup[];
  rows: DocumentFeature[];
  compact?: boolean;
}) {
  const byFeature = useMemo(() => {
    const map = new Map<string, DocumentFeature["status"][]>();
    for (const r of rows) {
      const list = map.get(r.feature) ?? [];
      list.push(r.status);
      map.set(r.feature, list);
    }
    return map;
  }, [rows]);

  const chips = useMemo(() => {
    const grouped = new Set<string>();
    for (const g of groups) for (const m of g.badge_members) grouped.add(m);
    const out: { key: string; label: string; status: GroupStatus }[] = [];
    // Individual (ungrouped) features first, in web order.
    for (const f of INDIVIDUAL_ORDER) {
      if (grouped.has(f)) continue;
      const statuses = byFeature.get(f) ?? [];
      out.push({ key: f, label: FEATURE_LABELS[f] ?? f, status: worstOf(statuses) });
    }
    // Server groups (entities, knowledge_graph) - only shown once, before meta, like the web.
    const metaIdx = out.findIndex((c) => c.key === "doc_metadata");
    const insertAt = metaIdx === -1 ? out.length : metaIdx;
    const groupChips = groups.map((g) => ({
      key: g.id,
      label: g.label,
      status: worstOf(g.badge_members.flatMap((f) => byFeature.get(f) ?? [])),
    }));
    out.splice(insertAt, 0, ...groupChips);
    return out;
  }, [groups, byFeature]);

  return (
    <View style={styles.row}>
      {chips.map((c) => (
        <Chip key={c.key} label={c.label} status={c.status} />
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", flexWrap: "wrap", gap: spacing.xs },
  chip: {
    flexDirection: "row",
    alignItems: "center",
    borderColor: colors.borderStrong,
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
    gap: spacing.xs,
  },
  dot: { width: 7, height: 7, borderRadius: 4 },
  chipText: { ...typeScale.small },
});
