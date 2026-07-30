import React from "react";
import { StyleSheet, Text, TouchableOpacity, View } from "react-native";

import type { DokDocument, ProcessingSummary } from "../api/documents";
import type { DocumentFeature, FeatureGroup } from "../api/features";
import { FeatureBadges } from "./FeatureBadges";
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
  onOpen,
}: {
  doc: DokDocument;
  processing?: ProcessingSummary;
  /** 1-per-row mode: cap the width and center instead of a giant full-width card. */
  compact?: boolean;
  featureGroups?: FeatureGroup[];
  features?: DocumentFeature[];
  onOpen?: (id: string) => void;
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
      {featureGroups && features && (
        <View style={styles.badges}>
          <FeatureBadges groups={featureGroups} rows={features} />
        </View>
      )}
    </TouchableOpacity>
  );
}

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
  badges: { marginTop: spacing.xs },
});
