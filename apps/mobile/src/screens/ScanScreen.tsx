import React, { useState } from "react";
import {
  ActivityIndicator,
  Image,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import DocumentScanner, { ResponseType, ScanDocumentResponseStatus } from "react-native-document-scanner-plugin";

import { useAuth } from "../auth/AuthContext";
import { buildScanPdf, uploadScanPdf } from "../scan/upload";
import { colors, spacing, typeScale } from "../theme";

// Camera scanning (#774): ML Kit scanner -> page review (delete/reorder/add) -> one PDF ->
// upload with progress. Fully on-device until the upload step.
interface Page {
  uri: string;
}

export function ScanScreen({ onUploaded }: { onUploaded?: () => void }) {
  const { token } = useAuth();
  const [pages, setPages] = useState<Page[]>([]);
  const [busy, setBusy] = useState<"scan" | "upload" | null>(null);
  const [progress, setProgress] = useState(0);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  async function scanMore() {
    setMessage(null);
    setBusy("scan");
    try {
      const res = await DocumentScanner.scanDocument({
        responseType: ResponseType.ImageFilePath,
        croppedImageQuality: 90,
      });
      if (res.status === ScanDocumentResponseStatus.Success && res.scannedImages?.length) {
        setPages((p) => [...p, ...res.scannedImages!.map((uri) => ({ uri }))]);
      }
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : "scan failed" });
    } finally {
      setBusy(null);
    }
  }

  function removeAt(i: number) {
    setPages((p) => p.filter((_, j) => j !== i));
  }
  function move(i: number, dir: -1 | 1) {
    setPages((p) => {
      const next = [...p];
      const j = i + dir;
      if (j < 0 || j >= next.length) return p;
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
  }

  async function upload() {
    if (!token || pages.length === 0) return;
    setBusy("upload");
    setProgress(0);
    setMessage(null);
    try {
      const pdf = await buildScanPdf(pages.map((p) => p.uri));
      const res = await uploadScanPdf(pdf, token, setProgress);
      setMessage({
        ok: res.rejected.length === 0,
        text: `${res.accepted.length} document(s) queued for ingestion${
          res.rejected.length ? ` · ${res.rejected.length} rejected` : ""
        }`,
      });
      setPages([]);
      onUploaded?.();
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : "upload failed" });
    } finally {
      setBusy(null);
    }
  }

  return (
    <ScrollView style={styles.root} contentContainerStyle={{ padding: spacing.md }}>
      <Text style={typeScale.muted}>
        scan a paper document page by page, review the order, then upload it as one document
      </Text>

      {pages.map((p, i) => (
        <View key={`${p.uri}-${i}`} style={styles.pageRow}>
          <Image source={{ uri: p.uri }} style={styles.thumb} resizeMode="cover" />
          <View style={styles.pageMeta}>
            <Text style={typeScale.body}>page {i + 1}</Text>
            <View style={styles.pageActions}>
              <TouchableOpacity onPress={() => move(i, -1)} disabled={i === 0}>
                <Text style={[styles.action, i === 0 && styles.actionDisabled]}>up</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => move(i, 1)} disabled={i === pages.length - 1}>
                <Text style={[styles.action, i === pages.length - 1 && styles.actionDisabled]}>down</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => removeAt(i)}>
                <Text style={[styles.action, { color: colors.danger }]}>delete</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      ))}

      <TouchableOpacity style={styles.primaryBtn} onPress={scanMore} disabled={busy !== null}>
        {busy === "scan" ? (
          <ActivityIndicator color={colors.bg} />
        ) : (
          <Text style={styles.primaryText}>{pages.length ? "scan another page" : "scan document"}</Text>
        )}
      </TouchableOpacity>

      {pages.length > 0 && (
        <TouchableOpacity style={styles.uploadBtn} onPress={upload} disabled={busy !== null}>
          {busy === "upload" ? (
            <View style={styles.progressWrap}>
              <ActivityIndicator color={colors.bg} />
              <Text style={styles.primaryText}>{Math.round(progress * 100)}%</Text>
            </View>
          ) : (
            <Text style={styles.primaryText}>upload {pages.length} page(s)</Text>
          )}
        </TouchableOpacity>
      )}

      {message && (
        <Text style={[styles.message, { color: message.ok ? colors.success : colors.danger }]}>
          {message.text}
        </Text>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg },
  pageRow: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.surface,
    borderRadius: 8,
    borderColor: colors.border,
    borderWidth: 1,
    marginTop: spacing.md,
    padding: spacing.sm,
  },
  thumb: { width: 64, height: 88, borderRadius: 6, backgroundColor: colors.surfaceAlt },
  pageMeta: { flex: 1, marginLeft: spacing.md },
  pageActions: { flexDirection: "row", gap: spacing.lg, marginTop: spacing.sm },
  action: { ...typeScale.muted, color: colors.accent },
  actionDisabled: { color: colors.borderStrong },
  primaryBtn: {
    backgroundColor: colors.accent,
    borderRadius: 8,
    alignItems: "center",
    paddingVertical: spacing.md,
    marginTop: spacing.lg,
  },
  uploadBtn: {
    backgroundColor: colors.success,
    borderRadius: 8,
    alignItems: "center",
    paddingVertical: spacing.md,
    marginTop: spacing.sm,
  },
  primaryText: { color: colors.bg, fontWeight: "700", fontSize: 15 },
  progressWrap: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  message: { textAlign: "center", marginTop: spacing.lg, ...typeScale.body },
});
