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
  type DocumentListStats,
  type DokDocument,
  type ProcessingSummary,
  type DocumentTag,
} from "../api/documents";
import { fetchDocumentFeatures, fetchFeatureGroups, type DocumentFeature, type FeatureGroup } from "../api/features";
import { DocumentGridCard } from "../components/DocumentGridCard";
import { DocumentTile } from "../components/DocumentTile";
import { EMPTY_FILTERS, SearchFilters, type SearchFiltersValue } from "../components/SearchFilters";
import AsyncStorage from "@react-native-async-storage/async-storage";

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
  stats: Record<string, DocumentListStats>;
}

function dedupeById(docs: DokDocument[]): DokDocument[] {
  const seen = new Set<string>();
  return docs.filter((d) => (seen.has(d.id) ? false : (seen.add(d.id), true)));
}

const EMPTY: RowState = { items: [], total: 0, nextCursor: null, processing: {}, tags: {}, stats: {} };


export function DocumentsScreen({ onOpenDocument }: { onOpenDocument?: (id: string) => void }) {
  const { token } = useAuth();
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [filters, setFilters] = useState<SearchFiltersValue>(EMPTY_FILTERS);
  const [state, setState] = useState<RowState>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [featureGroups, setFeatureGroups] = useState<FeatureGroup[]>([]);
  const [featuresByDoc, setFeaturesByDoc] = useState<Record<string, DocumentFeature[]>>({});

  // Feature groups for the enrichment badges (#814): fetched once per login.
  useEffect(() => {
    if (!token) return;
    fetchFeatureGroups(token)
      .then(setFeatureGroups)
      .catch(() => setFeatureGroups([]));
  }, [token]);
  const [viewMode, setViewMode] = useState<"tiles" | "grid">("tiles");
  const [gridCols, setGridCols] = useState<1 | 2 | 3>(2);

  // Grid column count is a per-user preference (#811): persist it locally so the choice survives
  // restarts (server-side user_preferences can take over later, like the web).
  useEffect(() => {
    AsyncStorage.getItem("doktok.prefs.gridCols").then((v) => {
      if (v === "1" || v === "2" || v === "3") setGridCols(Number(v) as 1 | 2 | 3);
    });
  }, []);
  const pickCols = (n: 1 | 2 | 3) => {
    setGridCols(n);
    void AsyncStorage.setItem("doktok.prefs.gridCols", String(n));
  };
  const stateRef = useRef(state);
  stateRef.current = state;
  const filtersRef = useRef(filters);
  filtersRef.current = filters;

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
      const f = filtersRef.current;
      try {
        const page = await fetchDocuments(
          {
            title: debounced,
            cursor,
            limit: PAGE_SIZE,
            sort: "created",
            dir: "desc",
            tokens: f.tokens,
            tokenMatch: f.tokenMatch,
            category: f.category,
            status: f.status,
            needsAttention: f.needsAttention,
            unidentifiable: f.unidentifiable,
            tags: f.tags,
            tagMatch: f.tagMatch,
          },
          token,
        );
        void fetchDocumentFeatures(
          page.items.map((d) => d.id),
          token,
        )
          .then((rows) =>
            setFeaturesByDoc((prev) => {
              const next = { ...prev };
              for (const r of rows) {
                next[r.document_id] = [...(next[r.document_id] ?? []), r];
              }
              // replace per document for freshness on replace mode
              if (mode === "replace") {
                const ids = new Set(rows.map((r) => r.document_id));
                for (const k of Object.keys(next)) if (!ids.has(k)) delete next[k];
                const byDoc: Record<string, DocumentFeature[]> = {};
                for (const r of rows) (byDoc[r.document_id] ??= []).push(r);
                return byDoc;
              }
              return next;
            }),
          )
          .catch(() => {});
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
          stats: { ...(mode === "more" ? s.stats : {}), ...(page.stats ?? {}) },
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

  // Initial load + whenever the debounced search or the filters change.
  useEffect(() => {
    setLoading(true);
    void load("replace");
  }, [load, debounced, filters]);

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
    if (viewMode === "grid") {
      return (
        <DocumentGridCard
          doc={item}
          processing={state.processing[item.id]}
          compact={gridCols === 1}
          featureGroups={featureGroups}
          features={featuresByDoc[item.id]}
          onOpen={onOpenDocument}
        />
      );
    }
    return (
      <DocumentTile
        doc={item}
        processing={state.processing[item.id]}
        stats={state.stats[item.id]}
        tags={state.tags[item.id]}
        featureGroups={featureGroups}
        features={featuresByDoc[item.id]}
        expanded={expandedId === item.id}
        onToggle={() => setExpandedId(expandedId === item.id ? null : item.id)}
        onOpen={onOpenDocument}
      />
    );
  }

  return (
    <View style={styles.root}>
      <View style={styles.searchRow}>
        <TextInput
          style={[styles.search, styles.searchFlex]}
          placeholder="search titles"
          placeholderTextColor={colors.muted}
          autoCapitalize="none"
          value={search}
          onChangeText={setSearch}
        />
        {viewMode === "grid" && (
          <View style={styles.seg}>
            {([1, 2, 3] as const).map((n) => (
              <TouchableOpacity
                key={n}
                style={[styles.segBtn, gridCols === n && styles.segBtnActive]}
                onPress={() => pickCols(n)}
                accessibilityLabel={`${n} per row`}
              >
                <Text style={[styles.segText, gridCols === n && styles.segTextActive]}>{n}</Text>
              </TouchableOpacity>
            ))}
          </View>
        )}
        <TouchableOpacity
          style={styles.viewToggle}
          onPress={() => setViewMode(viewMode === "tiles" ? "grid" : "tiles")}
          accessibilityLabel="toggle view"
        >
          <Text style={styles.viewToggleText}>{viewMode === "tiles" ? "▦" : "☰"}</Text>
        </TouchableOpacity>
      </View>
      <SearchFilters value={filters} onChange={setFilters} />
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
          key={`${viewMode}-${gridCols}`}
          data={state.items}
          keyExtractor={(d) => d.id}
          numColumns={viewMode === "grid" ? gridCols : 1}
          columnWrapperStyle={viewMode === "grid" && gridCols > 1 ? styles.gridRow : undefined}
          contentContainerStyle={viewMode === "grid" ? styles.gridContent : undefined}
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
  searchRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm, marginRight: spacing.md },
  searchFlex: { flex: 1 },
  viewToggle: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: spacing.sm + 2,
    paddingVertical: spacing.sm,
  },
  viewToggleText: { color: colors.accent, fontSize: 16 },
  seg: {
    flexDirection: "row",
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 8,
    overflow: "hidden",
  },
  segBtn: { paddingHorizontal: spacing.sm + 2, paddingVertical: spacing.sm },
  segBtnActive: { backgroundColor: colors.accentSoft },
  segText: { ...typeScale.small, fontWeight: "700" },
  segTextActive: { color: colors.accent },
  gridRow: { gap: spacing.sm, paddingHorizontal: spacing.md },
  gridContent: { gap: spacing.md, paddingBottom: spacing.xl },
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
