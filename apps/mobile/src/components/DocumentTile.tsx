import React from "react";
import { StyleSheet, Text, TouchableOpacity, View } from "react-native";

import type {
  DocumentListStats,
  DocumentTag,
  DokDocument,
  ProcessingSummary,
} from "../api/documents";
import { documentThumbnailUrl } from "../api/documentDetail";
import { AuthImage } from "./AuthImage";
import { colors, spacing, typeScale } from "../theme";

// Accordion document tile (#805): collapsed shows title (bold) + the two dates + status badge;
// expanded reveals category, tags, counts and the open action. The parent keeps ONE tile open
// at a time (opening another collapses the previous).
export function DocumentTile({
  doc,
  processing,
  stats,
  tags,
  expanded,
  onToggle,
  onOpen,
}: {
  doc: DokDocument;
  processing?: ProcessingSummary;
  stats?: DocumentListStats;
  tags?: DocumentTag[];
  expanded: boolean;
  onToggle: () => void;
  onOpen?: (id: string) => void;
}) {
  const badge = badgeFor(doc, processing);
  return (
    <TouchableOpacity
      style={[styles.tile, expanded && styles.tileExpanded]}
      onPress={onToggle}
      activeOpacity={0.8}
      accessibilityRole="button"
      accessibilityState={{ expanded }}
    >
      <View style={styles.headRow}>
        <View style={styles.thumbWrap}>
          <Text style={styles.thumbPlaceholder}>{(doc.title || doc.original_filename).charAt(0).toUpperCase()}</Text>
          <View style={StyleSheet.absoluteFill}>
            <AuthImage uri={documentThumbnailUrl(doc.id)} style={styles.thumb} resizeMode="cover" />
          </View>
        </View>
        <View style={styles.headMain}>
          <Text style={styles.title} numberOfLines={expanded ? undefined : 2}>
            {doc.title || doc.original_filename}
          </Text>
          <Text style={styles.dates} numberOfLines={1}>
            {formatDates(doc)}
          </Text>
        </View>
        {badge && (
          <Text style={[styles.badge, { color: badge.color }]}>{badge.text}</Text>
        )}
      </View>

      {expanded && (
        <View style={styles.body}>
          {(stats?.category || (tags && tags.length > 0)) && (
            <View style={styles.chipsRow}>
              {stats?.category && (
                <Text style={[styles.chip, styles.chipCategory]}>{stats.category}</Text>
              )}
              {(tags ?? []).map((t) => (
                <Text key={t.id} style={[styles.chip, { borderColor: t.color || colors.borderStrong }]}>
                  {t.name}
                </Text>
              ))}
            </View>
          )}
          <Text style={typeScale.muted}>
            {factsLine(processing, stats)}
          </Text>
          {processing && (processing.features_failed > 0 || processing.features_done > 0) && (
            <Text style={typeScale.muted}>
              features: {processing.features_done} done
              {processing.features_failed > 0 ? ` · ${processing.features_failed} failed` : ""}
            </Text>
          )}
          <TouchableOpacity style={styles.openBtn} onPress={() => onOpen?.(doc.id)}>
            <Text style={styles.openText}>open ›</Text>
          </TouchableOpacity>
        </View>
      )}
    </TouchableOpacity>
  );
}

function formatDates(doc: DokDocument): string {
  const parts: string[] = [];
  const acquired = doc.ingested_at ?? doc.created_at;
  if (acquired) parts.push(`acq ${shortDate(acquired)}`);
  if (doc.document_date) parts.push(`doc ${shortDate(doc.document_date)}`);
  return parts.join("  ·  ");
}

function shortDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function badgeFor(doc: DokDocument, proc?: ProcessingSummary): { text: string; color: string } | null {
  if (proc) {
    if (proc.features_failed > 0) return { text: "failed", color: colors.danger };
    if (proc.status && proc.status !== "active") return { text: proc.status, color: colors.warning };
  }
  if (doc.status !== "active") return { text: doc.status, color: colors.warning };
  return null;
}

function factsLine(proc?: ProcessingSummary, stats?: DocumentListStats): string {
  const parts: string[] = [];
  if (stats?.entity_count) parts.push(`${stats.entity_count} entities`);
  if (stats?.chunk_count) parts.push(`${stats.chunk_count} chunks`);
  if (proc?.page_count) parts.push(`${proc.page_count} pages`);
  if (proc?.extraction_method) parts.push(proc.extraction_method);
  return parts.join(" · ");
}

const styles = StyleSheet.create({
  tile: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 10,
    marginHorizontal: spacing.md,
    marginTop: spacing.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm + 2,
  },
  tileExpanded: {
    borderColor: colors.borderStrong,
    backgroundColor: colors.surfaceAlt,
  },
  headRow: { flexDirection: "row", alignItems: "flex-start" },
  thumbWrap: {
    width: 44,
    height: 58,
    borderRadius: 6,
    backgroundColor: colors.surfaceAlt,
    borderColor: colors.border,
    borderWidth: 1,
    marginRight: spacing.sm,
    overflow: "hidden",
    alignItems: "center",
    justifyContent: "center",
  },
  thumbPlaceholder: { ...typeScale.muted, fontSize: 18, fontWeight: "600" },
  thumb: { width: 44, height: 58 },
  headMain: { flex: 1, marginRight: spacing.sm },
  title: { ...typeScale.body, fontWeight: "700" },
  dates: { ...typeScale.small, marginTop: 2 },
  badge: { ...typeScale.small, fontWeight: "700", textTransform: "uppercase", marginTop: 2 },
  body: { marginTop: spacing.sm, borderTopColor: colors.border, borderTopWidth: StyleSheet.hairlineWidth, paddingTop: spacing.sm, gap: spacing.xs },
  chipsRow: { flexDirection: "row", flexWrap: "wrap", gap: spacing.xs },
  chip: {
    ...typeScale.small,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    borderRadius: 10,
    paddingHorizontal: spacing.sm,
    paddingVertical: 1,
    overflow: "hidden",
  },
  chipCategory: { borderColor: colors.accent, color: colors.accent },
  openBtn: { alignSelf: "flex-end", paddingVertical: spacing.xs },
  openText: { color: colors.accent, fontWeight: "700", fontSize: 15 },
});
