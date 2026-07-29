import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";

import { ApiError } from "../api/client";
import {
  fetchDocuments,
  type DokDocument,
  type ProcessingSummary,
  type DocumentTag,
} from "../api/documents";
import { useAuth } from "../auth/AuthContext";
import { colors, spacing, typeScale } from "../theme";

// Documents list (#772): search (title filter, debounced), processing-state badges that poll
// while any document is mid-pipeline, pull-to-refresh, and cursor-based infinite scroll.
const PAGE_SIZE = 25;

interface RowState {
  items: DokDocument[];
  total: number;
  nextCursor: string | null;
  processing: Record<string, ProcessingSummary>;
  tags: Record<string, DocumentTag[]>;
}

function dedupeById(docs: DokDocument[]): DokDocument[] {
  const seen = new Set<string>();
  return docs.filter((d) => (seen.has(d.id) ? false : (seen.add(d.id), true)));
}

const EMPTY: RowState = { items: [], total: 0, nextCursor: null, processing: {}, tags: {} };

function badgeFor(doc: DokDocument, proc?: ProcessingSummary): { text: string; color: string } | null {
  if (proc) {
    if (proc.features_failed > 0) return { text: "failed", color: colors.danger };
    if (proc.status && proc.status !== "active") return { text: proc.status, color: colors.warning };
  }
  if (doc.status !== "active") return { text: doc.status, color: colors.warning };
  return null;
}

export function DocumentsScreen({ onOpenDocument }: { onOpenDocument?: (id: string) => void }) {
  const { token } = useAuth();
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [state, setState] = useState<RowState>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const stateRef = useRef(state);
  stateRef.current = state;

  // Debounce the title filter (300ms) so typing doesn't fire a request per keystroke.
  useEffect(() => {
    const id = setTimeout(() => setDebounced(search), 300);
    return () => clearTimeout(id);
  }, [search]);

  const load = useCallback(
    async (mode: "replace" | "more") => {
      if (!token) return;
      const prev = stateRef.current;
      const cursor = mode === "more" ? prev.nextCursor : null;
      if (mode === "more" && !cursor) return;
      if (mode === "more") setLoadingMore(true);
      try {
        const page = await fetchDocuments(
          { title: debounced, cursor, limit: PAGE_SIZE, sort: "created", dir: "desc" },
          token,
        );
        setState((s) => ({
          // Dedupe by id: a replace (poll/search) can overlap with an in-flight "more" append, or
          // the cursor can shift under new ingest - the same doc must never appear twice (#772).
          items:
            mode === "more"
              ? [...s.items, ...page.items.filter((d) => !s.items.some((e) => e.id === d.id))]
              : dedupeById(page.items),
          total: page.total,
          nextCursor: page.next_cursor,
          processing: { ...(mode === "more" ? s.processing : {}), ...(page.processing ?? {}) },
          tags: { ...(mode === "more" ? s.tags : {}), ...(page.tags ?? {}) },
        }));
        setError(null);
      } catch (e) {
        setError(e instanceof ApiError ? e.message : "failed to load documents");
      } finally {
        setLoading(false);
        setLoadingMore(false);
      }
    },
    [token, debounced],
  );

  // Initial load + whenever the debounced search changes.
  useEffect(() => {
    setLoading(true);
    void load("replace");
  }, [load]);

  // Live badges: poll only while any visible document is still mid-pipeline.
  const anyProcessing = useMemo(
    () =>
      state.items.some((d) => {
        const p = state.processing[d.id];
        return d.status !== "active" || (p && p.status && p.status !== "active");
      }),
    [state],
  );
  useEffect(() => {
    if (!anyProcessing) return;
    const id = setInterval(() => void load("replace"), 5000);
    return () => clearInterval(id);
  }, [anyProcessing, load]);

  function renderItem({ item }: { item: DokDocument }) {
    const proc = state.processing[item.id];
    const badge = badgeFor(item, proc);
    const docTags = state.tags[item.id] ?? [];
    return (
      <TouchableOpacity
        style={styles.row}
        onPress={() => onOpenDocument?.(item.id)}
        accessibilityRole="button"
      >
        <View style={styles.rowMain}>
          <Text style={styles.rowTitle} numberOfLines={1}>
            {item.title || item.original_filename}
          </Text>
          <Text style={styles.rowMeta} numberOfLines={1}>
            {new Date(item.created_at).toLocaleDateString()} · {item.original_filename}
          </Text>
          {docTags.length > 0 && (
            <View style={styles.tagsRow}>
              {docTags.slice(0, 3).map((t) => (
                <Text key={t.id} style={[styles.tag, { borderColor: t.color || colors.borderStrong }]}>
                  {t.name}
                </Text>
              ))}
              {docTags.length > 3 && <Text style={styles.rowMeta}>+{docTags.length - 3}</Text>}
            </View>
          )}
        </View>
        {badge && <Text style={[styles.badge, { color: badge.color }]}>{badge.text}</Text>}
        {item.status !== "active" && !badge && (
          <ActivityIndicator color={colors.warning} size="small" />
        )}
      </TouchableOpacity>
    );
  }

  return (
    <View style={styles.root}>
      <TextInput
        style={styles.search}
        placeholder="search documents"
        placeholderTextColor={colors.muted}
        autoCapitalize="none"
        value={search}
        onChangeText={setSearch}
      />
      {error && (
        <Text style={styles.error} role="alert">
          {error}
        </Text>
      )}
      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.accent} size="large" />
        </View>
      ) : (
        <FlatList
          data={state.items}
          keyExtractor={(d) => d.id}
          renderItem={renderItem}
          refreshControl={
            <RefreshControl
              refreshing={false}
              onRefresh={() => void load("replace")}
              tintColor={colors.accent}
            />
          }
          onEndReached={() => void load("more")}
          onEndReachedThreshold={0.3}
          ListEmptyComponent={
            <Text style={styles.empty}>
              {debounced ? `no documents match "${debounced}"` : "no documents yet"}
            </Text>
          }
          ListFooterComponent={
            loadingMore ? (
              <ActivityIndicator color={colors.accent} style={styles.footer} />
            ) : (
              <Text style={styles.footerMuted}>
                {state.total > 0 ? `${state.items.length} of ${state.total}` : ""}
              </Text>
            )
          }
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg },
  search: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 8,
    color: colors.text,
    margin: spacing.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm + 2,
    fontSize: 15,
  },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  error: { color: colors.danger, textAlign: "center", marginBottom: spacing.sm },
  empty: { ...typeScale.muted, textAlign: "center", marginTop: spacing.xl * 2 },
  row: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    borderBottomColor: colors.border,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  rowMain: { flex: 1, marginRight: spacing.sm },
  rowTitle: { ...typeScale.body, fontWeight: "600" },
  rowMeta: { ...typeScale.small, marginTop: 2 },
  tagsRow: { flexDirection: "row", gap: spacing.xs, marginTop: spacing.xs },
  tag: {
    ...typeScale.small,
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: spacing.sm,
    paddingVertical: 1,
    overflow: "hidden",
  },
  badge: { ...typeScale.small, fontWeight: "700", textTransform: "uppercase" },
  footer: { marginVertical: spacing.md },
  footerMuted: { ...typeScale.small, textAlign: "center", marginVertical: spacing.md },
});
