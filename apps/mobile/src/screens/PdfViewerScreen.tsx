import React, { useEffect, useState } from "react";
import { ActivityIndicator, StyleSheet, Text, View } from "react-native";
import Pdf from "react-native-pdf";
import * as FileSystem from "expo-file-system/legacy";

import { documentFileUrl } from "../api/documentDetail";
import { useAuth } from "../auth/AuthContext";
import { colors, spacing, typeScale } from "../theme";

// Inline PDF viewer (#773 follow-up): downloads the document file with the bearer token into the
// cache (same mechanism as the share action) and renders it natively - fully offline, no external
// viewer app needed.
export function PdfViewerScreen({
  id,
  variant = "original",
  title,
}: {
  id: string;
  variant?: "original" | "normalized";
  title?: string;
}) {
  const { token } = useAuth();
  const [source, setSource] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState<{ current: number; total: number } | null>(null);

  useEffect(() => {
    let alive = true;
    if (!token) return;
    const target = `${FileSystem.cacheDirectory}${id}-${variant}.pdf`;
    (async () => {
      try {
        const info = await FileSystem.getInfoAsync(target);
        if (!info.exists) {
          await FileSystem.downloadAsync(documentFileUrl(id, variant), target, {
            headers: { Authorization: `Bearer ${token}` },
          });
        }
        if (alive) setSource(target);
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : "could not load the PDF");
      }
    })();
    return () => {
      alive = false;
    };
  }, [id, variant, token]);

  if (error) {
    return (
      <View style={styles.center}>
        <Text style={styles.error}>{error}</Text>
      </View>
    );
  }
  if (!source) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} size="large" />
      </View>
    );
  }

  return (
    <View style={styles.root}>
      {title && (
        <Text style={[typeScale.muted, styles.title]} numberOfLines={1}>
          {title} {page ? `· ${page.current}/${page.total}` : ""}
        </Text>
      )}
      <Pdf
        source={{ uri: source, cache: false }}
        style={styles.pdf}
        trustAllCerts={false}
        enablePaging
        horizontal={false}
        onLoadComplete={(numberOfPages) => setPage({ current: 1, total: numberOfPages })}
        onPageChanged={(p) => setPage((prev) => (prev ? { ...prev, current: p } : prev))}
        onError={(e) => setError(e instanceof Error ? e.message : "could not render the PDF")}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg },
  center: {
    flex: 1,
    backgroundColor: colors.bg,
    alignItems: "center",
    justifyContent: "center",
    padding: spacing.xl,
  },
  error: { color: colors.danger, textAlign: "center" },
  title: { paddingHorizontal: spacing.md, paddingTop: spacing.sm },
  pdf: { flex: 1, backgroundColor: colors.bg },
});
