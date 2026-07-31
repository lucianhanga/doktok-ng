import React from "react";
import { StyleSheet, Text, TouchableOpacity, View } from "react-native";

import type { DokDocument, ProcessingSummary, DocumentTag } from "../api/documents";
import type { DocumentFeature, FeatureGroup } from "../api/features";
import { FeatureBadges } from "./FeatureBadges";
import { TagChip } from "./TagChip";
import { documentThumbnailUrl } from "../api/documentDetail";
import { AuthImage } from "./AuthImage";
import { colors, spacing, typeScale } from "../theme";

// Thumbnail grid card (#805): big first-page preview, title under it, date + status badge. Taps
// straight into the document detail (no accordion in grid mode). Width comes from the parent's
// FlatList numColumns; this fills its cell.
export function DocumentGridCard({
  doc,
  processing,
  compact,
  featureGroups,
  features,
  tags,
  onOpen,
  onTagPress,
}: {
  doc: DokDocument;
  processing?: ProcessingSummary;
  /** 1-per-row mode: cap the width and center instead of a giant full-width card. */
  compact?: boolean;
  featureGroups?: FeatureGroup[];
  features?: DocumentFeature[];
  tags?: DocumentTag[];
  onOpen?: (id: string) => void;
  /** Tap a tag chip -> filter the documents list to that tag (#777). */
  onTagPress?: (name: string) => void;
}) {
  const failed = processing && processing.features_failed > 0;
  const processingNow = doc.status !== "active" || (processing && processing.status !== "active");
  return (
    <TouchableOpacity
      style={[styles.card, compact && styles.cardCompact]}
      onPress={() => onOpen?.(doc.id)}
      activeOpacity={0.85}
    >
      <View style={styles.thumbWrap}>
        <Text style={styles.thumbPlaceholder}>
          {(doc.title || doc.original_filename).charAt(0).toUpperCase()}
        </Text>
        <View style={StyleSheet.absoluteFill}>
          <AuthImage uri={documentThumbnailUrl(doc.id)} style={styles.thumb} resizeMode="cover" />
        </View>
        {(failed || processingNow) && (
          <Text style={[styles.badge, { color: failed ? colors.danger : colors.warning }]}>
            {failed ? "failed" : (processing?.status ?? doc.status)}
          </Text>
        )}
        {featureGroups && features && features.length > 0 && (
          <View style={styles.badgeOverlay} pointerEvents="none">
            {SCRIM_SLICES.map((opacity, i) => (
              <View
                key={i}
                style={[
                  styles.scrimSlice,
                  { left: `${(i * 100) / SCRIM_SLICES.length}%`, backgroundColor: `rgba(0,0,0,${opacity})` },
                ]}
              />
            ))}
            <FeatureBadges groups={featureGroups} rows={features} overlay vertical />
          </View>
        )}
      </View>
      <Text style={styles.title} numberOfLines={2}>
        {doc.title || doc.original_filename}
      </Text>
      <Text style={typeScale.small} numberOfLines={1}>
        {new Date(doc.ingested_at ?? doc.created_at).toLocaleDateString(undefined, {
          day: "numeric",
          month: "short",
          year: "numeric",
        })}
      </Text>
      {tags && tags.length > 0 && (
        <View style={styles.tagsRow}>
          {tags.slice(0, 3).map((t) => (
            <TagChip
              key={t.id}
              name={t.name}
              color={t.color}
              small
              onPress={onTagPress ? () => onTagPress(t.name) : undefined}
            />
          ))}
          {tags.length > 3 && <Text style={typeScale.small}>+{tags.length - 3}</Text>}
        </View>
      )}
    </TouchableOpacity>
  );
}

// Left→right opacity ramp for the fake gradient (lightest at the image side, darkest at the edge).
const SCRIM_SLICES = [0.03, 0.09, 0.16, 0.24, 0.33, 0.45];

const styles = StyleSheet.create({
  card: { flex: 1 },
  // 1-per-row: centered, capped width (a full-width thumbnail looks oversized).
  cardCompact: { maxWidth: 260, width: "100%", alignSelf: "center" },
  thumbWrap: {
    aspectRatio: 0.72,
    borderRadius: 10,
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    overflow: "hidden",
    alignItems: "center",
    justifyContent: "center",
  },
  thumbPlaceholder: { ...typeScale.muted, fontSize: 34, fontWeight: "600" },
  thumb: { width: "100%", height: "100%" },
  badge: {
    position: "absolute",
    top: spacing.xs,
    right: spacing.xs,
    ...typeScale.small,
    fontWeight: "700",
    textTransform: "uppercase",
    backgroundColor: colors.surfaceAlt,
    borderRadius: 6,
    paddingHorizontal: spacing.xs,
    overflow: "hidden",
  },
  title: { ...typeScale.body, fontWeight: "600", marginTop: spacing.xs },
  // Feature badges: vertical stack hugging the thumbnail's right edge. The "gradient" is faked
  // with solid slices (RN has no gradients without a native module); chips sit on top.
  badgeOverlay: {
    position: "absolute",
    top: 0,
    right: 0,
    bottom: 0,
    width: "56%",
    paddingRight: spacing.xs,
    paddingVertical: spacing.xs,
    justifyContent: "center",
    alignItems: "flex-end",
  },
  scrimSlice: { position: "absolute", top: 0, bottom: 0, width: `${100 / SCRIM_SLICES.length}%` },
  tagsRow: { flexDirection: "row", flexWrap: "wrap", gap: spacing.xs, marginTop: spacing.xs },
});
