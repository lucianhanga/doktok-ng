import React, { useMemo } from "react";
import { StyleSheet, Text, View } from "react-native";

import type { DocumentFeature, FeatureGroup } from "../api/features";
import { colors, spacing, typeScale } from "../theme";

// Per-document enrichment badges (#814), same grouping/semantics as the web: one chip per feature
// group (extract, entities, metadata, ...), worst-of status wins (failed > running > pending >
// done); groups with no ledger rows render as muted "not started".
type GroupStatus = "failed" | "running" | "pending" | "done" | "none";

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

  if (groups.length === 0) return null;
  return (
    <View style={styles.row}>
      {groups.map((g) => {
        const statuses = g.badge_members.flatMap((f) => byFeature.get(f) ?? []);
        const status = worstOf(statuses);
        return (
          <View key={g.id} style={styles.chip}>
            <View style={[styles.dot, { backgroundColor: STATUS_COLOR[status] }]} />
            <Text style={styles.chipText}>{g.label}</Text>
          </View>
        );
      })}
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
