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
import * as DocumentPicker from "expo-document-picker";
import DocumentScanner, { ResponseType, ScanDocumentResponseStatus } from "react-native-document-scanner-plugin";

import { useAuth } from "../auth/AuthContext";
import { basename, buildScanPdf, uploadFile, uploadScanPdf } from "../scan/upload";
import { useIngestionTracker, type TrackedUpload } from "../scan/tracker";
import { colors, spacing, typeScale } from "../theme";

// Camera scanning (#774) + library/files upload (#775): ML Kit scanner or picked PDFs/images ->
// upload with progress -> the tracker polls each file to ready/failed (list at the bottom).
interface Page {
  uri: string;
}

function guessMime(name: string, mime?: string): string {
  if (mime) return mime;
  const ext = name.split(".").pop()?.toLowerCase();
  if (ext === "pdf") return "application/pdf";
  if (ext === "jpg" || ext === "jpeg") return "image/jpeg";
  if (ext === "png") return "image/png";
  return "application/octet-stream";
}

const STATE_STYLE: Record<TrackedUpload["state"], { color: string; label: string }> = {
  queued: { color: colors.warning, label: "queued …" },
  processing: { color: colors.warning, label: "processing …" },
  ready: { color: colors.success, label: "ready ✓" },
  failed: { color: colors.danger, label: "failed ✗" },
};

export function ScanScreen({ onUploaded }: { onUploaded?: () => void }) {
  const { token } = useAuth();
  const { uploads, track, clearFinished } = useIngestionTracker();
  const [pages, setPages] = useState<Page[]>([]);
  const [busy, setBusy] = useState<"scan" | "upload" | "files" | null>(null);
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
      track(basename(pdf)); // server stores the file under its basename
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

  // Pick existing PDFs/images from the device and upload them one by one (#775).
  async function pickFiles() {
    if (!token) return;
    setMessage(null);
    try {
      const res = await DocumentPicker.getDocumentAsync({
        type: ["application/pdf", "image/*"],
        multiple: true,
        copyToCacheDirectory: true,
      });
      if (res.canceled || res.assets.length === 0) return;
      setBusy("files");
      let accepted = 0;
      let rejected = 0;
      for (let i = 0; i < res.assets.length; i++) {
        const asset = res.assets[i];
        setMessage({ ok: true, text: `uploading ${i + 1}/${res.assets.length}: ${asset.name}` });
        try {
          const out = await uploadFile(asset.uri, guessMime(asset.name, asset.mimeType), token, setProgress);
          accepted += out.accepted.length;
          rejected += out.rejected.length;
          // The server stores the file under the multipart filename = cache-copy basename.
          track(basename(asset.uri));
        } catch {
          rejected += 1;
        }
      }
      setMessage({
        ok: rejected === 0,
        text: `${accepted} document(s) queued for ingestion${rejected ? ` · ${rejected} rejected` : ""}`,
      });
      onUploaded?.();
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : "file pick failed" });
    } finally {
      setBusy(null);
      setProgress(0);
    }
  }

  const anyFinished = uploads.some((u) => u.state === "ready" || u.state === "failed");

  return (
    <ScrollView style={styles.root} contentContainerStyle={{ padding: spacing.md }}>
      <Text style={typeScale.muted}>
        scan a paper document page by page, review the order, then upload it as one document — or
        pick existing PDFs/images from the device
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

      <TouchableOpacity style={styles.secondaryBtn} onPress={pickFiles} disabled={busy !== null}>
        {busy === "files" ? (
          <View style={styles.progressWrap}>
            <ActivityIndicator color={colors.text} />
            <Text style={styles.secondaryText}>{Math.round(progress * 100)}%</Text>
          </View>
        ) : (
          <Text style={styles.secondaryText}>pick from files (PDF / images)</Text>
        )}
      </TouchableOpacity>

      {message && (
        <Text style={[styles.message, { color: message.ok ? colors.success : colors.danger }]}>
          {message.text}
        </Text>
      )}

      {uploads.length > 0 && (
        <View style={styles.trackerCard}>
          <View style={styles.trackerHead}>
            <Text style={typeScale.section}>uploads</Text>
            {anyFinished && (
              <TouchableOpacity onPress={clearFinished}>
                <Text style={[styles.action, { color: colors.accent }]}>clear finished</Text>
              </TouchableOpacity>
            )}
          </View>
          {uploads.map((u) => (
            <View key={u.id} style={styles.trackerRow}>
              <Text style={styles.trackerName} numberOfLines={1}>
                {u.filename}
              </Text>
              <Text style={[styles.trackerState, { color: STATE_STYLE[u.state].color }]}>
                {STATE_STYLE[u.state].label}
              </Text>
              {u.state === "failed" && u.detail && (
                <Text style={styles.trackerDetail} numberOfLines={2}>
                  {u.detail}
                </Text>
              )}
            </View>
          ))}
        </View>
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
  secondaryBtn: {
    backgroundColor: colors.surface,
    borderColor: colors.borderStrong,
    borderWidth: 1,
    borderRadius: 8,
    alignItems: "center",
    paddingVertical: spacing.md,
    marginTop: spacing.sm,
  },
  primaryText: { color: colors.bg, fontWeight: "700", fontSize: 15 },
  secondaryText: { color: colors.text, fontWeight: "600", fontSize: 15 },
  progressWrap: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  message: { textAlign: "center", marginTop: spacing.lg, ...typeScale.body },
  trackerCard: {
    backgroundColor: colors.surface,
    borderRadius: 8,
    borderColor: colors.border,
    borderWidth: 1,
    marginTop: spacing.lg,
    padding: spacing.md,
  },
  trackerHead: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: spacing.sm,
  },
  trackerRow: {
    borderTopColor: colors.border,
    borderTopWidth: 1,
    paddingVertical: spacing.sm,
  },
  trackerName: { ...typeScale.body, flexShrink: 1 },
  trackerState: { ...typeScale.small, fontWeight: "600", marginTop: 2 },
  trackerDetail: { ...typeScale.small, color: colors.danger, marginTop: 2 },
});
