import React, { useEffect, useState } from "react";
import {
  ActivityIndicator,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import Markdown from "react-native-markdown-display";
import * as FileSystem from "expo-file-system/legacy";
import * as Sharing from "expo-sharing";

import { ApiError } from "../api/client";
import { AuthImage } from "../components/AuthImage";

// Make every markdown text node selectable (long-press -> copy) - this is how users copy text out
// of a document until a proper content-search exists.
const MD_RULES = {
  text: (
    node: { key: string; content: string },
    _children: unknown,
    _parent: unknown,
    styles: Record<string, unknown>,
    inheritedStyles: Record<string, unknown> = {},
  ) => (
    <Text key={node.key} style={[inheritedStyles, styles.text] as never} selectable>
      {node.content}
    </Text>
  ),
};
import {
  documentFileUrl,
  documentPageThumbnailUrl,
  documentThumbnailUrl,
  fetchDocument,
  fetchDocumentActivity,
  fetchDocumentContent,
  fetchDocumentEntities,
  type AuditEvent,
  type DocEntity,
} from "../api/documentDetail";
import type { DokDocument } from "../api/documents";
import { useAuth } from "../auth/AuthContext";
import { colors, spacing, typeScale } from "../theme";

// Document detail (#773): metadata header, Content/Entities/Activity tabs, page thumbnails,
// open-PDF action (download + system share sheet). Spartan like the web card.
type Tab = "content" | "entities" | "activity";
const TABS: { id: Tab; label: string }[] = [
  { id: "content", label: "Content" },
  { id: "entities", label: "Entities" },
  { id: "activity", label: "Activity" },
];

const authHeaders = (token: string) => ({ Authorization: `Bearer ${token}` });

export function DocumentDetailScreen({
  id,
  onOpenPdf,
}: {
  id: string;
  onOpenPdf?: (variant: "original" | "normalized") => void;
}) {
  const { token } = useAuth();
  const [doc, setDoc] = useState<DokDocument | null>(null);
  const [tab, setTab] = useState<Tab>("content");
  const [content, setContent] = useState<string | null>(null);
  const [entities, setEntities] = useState<DocEntity[] | null>(null);
  const [activity, setActivity] = useState<AuditEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sharing, setSharing] = useState(false);

  useEffect(() => {
    if (!token) return;
    fetchDocument(id, token)
      .then(setDoc)
      .catch((e) => setError(e instanceof ApiError ? e.message : "failed to load document"));
    fetchDocumentContent(id, token)
      .then(setContent)
      .catch(() => setContent(""));
    fetchDocumentEntities(id, token)
      .then(setEntities)
      .catch(() => setEntities([]));
    fetchDocumentActivity(id, token)
      .then(setActivity)
      .catch(() => setActivity([]));
  }, [id, token]);

  async function sharePdf(variant: "original" | "normalized") {
    if (!token || !doc) return;
    setSharing(true);
    try {
      const target = `${FileSystem.cacheDirectory}${doc.id}-${variant}.pdf`;
      const res = await FileSystem.downloadAsync(
        documentFileUrl(doc.id, variant),
        target,
        { headers: authHeaders(token) },
      );
      await Sharing.shareAsync(res.uri, { mimeType: "application/pdf", dialogTitle: doc.title ?? doc.original_filename });
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not open the PDF");
    } finally {
      setSharing(false);
    }
  }

  if (error) {
    return (
      <View style={styles.center}>
        <Text style={styles.error} role="alert">
          {error}
        </Text>
      </View>
    );
  }
  if (!doc) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} size="large" />
      </View>
    );
  }

  const pageCount = (doc.metadata?.page_count as number | undefined) ?? 0;

  return (
    <ScrollView style={styles.root} contentContainerStyle={{ paddingBottom: spacing.xl }}>
      <View style={styles.header}>
        <Text style={typeScale.title} numberOfLines={2}>
          {doc.title || doc.original_filename}
        </Text>
        <Text style={typeScale.muted} numberOfLines={1}>
          {new Date(doc.created_at).toLocaleDateString()} · {doc.original_filename}
        </Text>
        {doc.status !== "active" && (
          <Text style={[styles.badge, { color: colors.warning }]}>{doc.status}</Text>
        )}
      </View>

      {pageCount > 0 && (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.thumbs}>
          {Array.from({ length: Math.min(pageCount, 12) }, (_, i) => (
            <AuthImage
              key={i}
              uri={documentPageThumbnailUrl(doc.id, i + 1)}
              style={styles.thumb}
              resizeMode="cover"
            />
          ))}
        </ScrollView>
      )}
      {pageCount === 0 && token && (
        <AuthImage uri={documentThumbnailUrl(doc.id)} style={styles.thumbSingle} resizeMode="contain" />
      )}

      <View style={styles.actions}>
        <TouchableOpacity
          style={styles.actionBtn}
          onPress={() => (onOpenPdf ? onOpenPdf("original") : void sharePdf("original"))}
          disabled={sharing}
        >
          <Text style={styles.actionText}>{sharing ? "opening…" : "view PDF"}</Text>
        </TouchableOpacity>
        {(doc.metadata?.system_document as string | undefined) && (
          <TouchableOpacity
            style={styles.actionBtn}
            onPress={() => (onOpenPdf ? onOpenPdf("normalized") : void sharePdf("normalized"))}
            disabled={sharing}
          >
            <Text style={styles.actionText}>view searchable</Text>
          </TouchableOpacity>
        )}
        <TouchableOpacity style={styles.actionBtn} onPress={() => void sharePdf("original")} disabled={sharing}>
          <Text style={styles.actionText}>share</Text>
        </TouchableOpacity>
      </View>

      <View style={styles.tabBar}>
        {TABS.map((t) => (
          <TouchableOpacity key={t.id} onPress={() => setTab(t.id)} style={[styles.tab, tab === t.id && styles.tabActive]}>
            <Text style={[styles.tabText, tab === t.id && styles.tabTextActive]}>{t.label}</Text>
          </TouchableOpacity>
        ))}
      </View>

      {tab === "content" &&
        (content === null ? (
          <ActivityIndicator color={colors.accent} style={styles.sectionSpinner} />
        ) : content === "" ? (
          <Text style={typeScale.muted}>no extracted content yet</Text>
        ) : (
          <Markdown
            style={{ body: styles.md, heading3: styles.mdH, code_inline: styles.mdCode, fence: styles.mdCode }}
            rules={MD_RULES}
          >
            {content}
          </Markdown>
        ))}

      {tab === "entities" &&
        (entities === null ? (
          <ActivityIndicator color={colors.accent} style={styles.sectionSpinner} />
        ) : entities.length === 0 ? (
          <Text style={typeScale.muted}>no entities extracted</Text>
        ) : (
          entities.map((e, i) => (
            <View key={`${e.entity_type}-${e.normalized_value}-${i}`} style={styles.listRow}>
              <Text style={typeScale.body}>{e.normalized_value ?? "?"}</Text>
              <Text style={typeScale.small}>
                {e.entity_type} · {e.frequency}×
              </Text>
            </View>
          ))
        ))}

      {tab === "activity" &&
        (activity === null ? (
          <ActivityIndicator color={colors.accent} style={styles.sectionSpinner} />
        ) : activity.length === 0 ? (
          <Text style={typeScale.muted}>no activity yet</Text>
        ) : (
          activity.map((ev) => (
            <View key={ev.id} style={styles.listRow}>
              <Text style={typeScale.body}>{ev.description || ev.event_type}</Text>
              <Text style={typeScale.small}>
                {new Date(ev.timestamp).toLocaleString()} · {ev.actor}
              </Text>
            </View>
          ))
        ))}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg },
  center: { flex: 1, backgroundColor: colors.bg, alignItems: "center", justifyContent: "center" },
  error: { color: colors.danger, padding: spacing.xl, textAlign: "center" },
  header: { padding: spacing.md, borderBottomColor: colors.border, borderBottomWidth: StyleSheet.hairlineWidth },
  badge: { ...typeScale.small, fontWeight: "700", textTransform: "uppercase", marginTop: spacing.xs },
  thumbs: { paddingHorizontal: spacing.sm, marginTop: spacing.sm },
  thumb: { width: 120, height: 160, borderRadius: 6, marginHorizontal: spacing.xs, backgroundColor: colors.surfaceAlt },
  thumbSingle: { height: 200, margin: spacing.md, borderRadius: 8, backgroundColor: colors.surfaceAlt },
  actions: { flexDirection: "row", gap: spacing.sm, paddingHorizontal: spacing.md, marginTop: spacing.md },
  actionBtn: {
    backgroundColor: colors.surfaceAlt,
    borderColor: colors.borderStrong,
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  actionText: { ...typeScale.body, color: colors.accent },
  tabBar: { flexDirection: "row", borderBottomColor: colors.border, borderBottomWidth: 1, marginTop: spacing.md },
  tab: { flex: 1, alignItems: "center", paddingVertical: spacing.sm },
  tabActive: { borderBottomColor: colors.accent, borderBottomWidth: 2 },
  tabText: { ...typeScale.muted },
  tabTextActive: { color: colors.accent, fontWeight: "700" },
  sectionSpinner: { marginTop: spacing.xl },
  listRow: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm + 2,
    borderBottomColor: colors.border,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  md: { color: colors.text, paddingHorizontal: spacing.md, paddingTop: spacing.md },
  mdH: { color: colors.text, marginTop: spacing.md },
  mdCode: {
    color: colors.text,
    backgroundColor: colors.surfaceAlt,
    fontFamily: "Courier",
    borderColor: colors.border,
    borderWidth: 0,
  },
});
