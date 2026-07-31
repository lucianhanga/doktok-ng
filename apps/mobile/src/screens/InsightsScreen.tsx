import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import { useNavigation } from "@react-navigation/native";

import { useAuth } from "../auth/AuthContext";
import { fetchCategories, type CategorySummary } from "../api/documents";
import {
  fetchEmbeddingMap,
  fetchEntities,
  type EmbeddingMap,
  type EntitySummary,
  type VizPoint,
} from "../api/insights";
import { colors, spacing, typeScale } from "../theme";

// Insights, phone-adapted (#778): word cloud (flow layout, occurrence-scaled, tap for details),
// categories overview (tap filters the documents list), and the embedding projection as a cluster
// list. No WebGL/3D and no knowledge graph on phones - both are web-only by design.
type Sub = "cloud" | "categories" | "map";
const SUBS: { id: Sub; label: string }[] = [
  { id: "cloud", label: "Word Cloud" },
  { id: "categories", label: "Categories" },
  { id: "map", label: "Embedding Map" },
];

export function InsightsScreen() {
  const navigation = useNavigation();
  const [sub, setSub] = useState<Sub>("cloud");

  // Same cross-tab jumps as chat citations: Insights is a direct tab child, so this navigation
  // targets the tab navigator and nests into the Documents stack.
  const openDocument = useCallback(
    (documentId: string) => {
      const navigate = navigation.navigate as (screen: string, params?: unknown) => void;
      navigate("Documents", { screen: "DocumentDetail", params: { id: documentId } });
    },
    [navigation],
  );
  const filterByCategory = useCallback(
    (category: string) => {
      const navigate = navigation.navigate as (screen: string, params?: unknown) => void;
      navigate("Documents", { screen: "DocumentsList", params: { category } });
    },
    [navigation],
  );

  return (
    <View style={styles.root}>
      <View style={styles.subBar}>
        {SUBS.map((s) => (
          <TouchableOpacity
            key={s.id}
            style={[styles.subTab, sub === s.id && styles.subTabActive]}
            onPress={() => setSub(s.id)}
          >
            <Text style={[styles.subText, sub === s.id && styles.subTextActive]}>{s.label}</Text>
          </TouchableOpacity>
        ))}
      </View>
      {sub === "cloud" && <WordCloudPanel />}
      {sub === "categories" && <CategoriesPanel onFilter={filterByCategory} />}
      {sub === "map" && <EmbeddingClustersPanel onOpenDocument={openDocument} />}
    </View>
  );
}

// ---- Word cloud ----

const MAX_WORDS = 80;

// Stable, distinct hues per entity type (same table as the web's WordCloudPanel).
const TYPE_COLORS: Record<string, string> = {
  PERSON: "#6ea8fe",
  ORG: "#7ee787",
  GPE: "#f0883e",
  LOC: "#f0883e",
  DATE: "#d2a8ff",
  MONEY: "#e3b341",
  EMAIL: "#56d4dd",
  PHONE: "#ff9bce",
  URL: "#a5d6ff",
  PRODUCT: "#ffa657",
  EVENT: "#d29922",
};

function colorForType(entityType: string): string {
  return TYPE_COLORS[entityType.toUpperCase()] ?? colors.accent;
}

function WordCloudPanel() {
  const { token } = useAuth();
  const [entities, setEntities] = useState<EntitySummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<EntitySummary | null>(null);

  useEffect(() => {
    if (!token) return;
    fetchEntities(token)
      .then(setEntities)
      .catch((e) => setError(e instanceof Error ? e.message : "failed to load entities"));
  }, [token]);

  const words = useMemo(
    () =>
      (entities ?? [])
        .slice()
        .sort((a, b) => b.occurrences - a.occurrences)
        .slice(0, MAX_WORDS),
    [entities],
  );

  // sqrt scale (web parity) so the single most-frequent entity does not dwarf the rest.
  const sizeFor = useMemo(() => {
    if (words.length === 0) return () => 16;
    const values = words.map((w) => w.occurrences);
    const lo = Math.sqrt(Math.min(...values));
    const hi = Math.sqrt(Math.max(...values));
    const MIN_PX = 13;
    const MAX_PX = 30;
    return (w: EntitySummary) =>
      hi === lo ? (MIN_PX + MAX_PX) / 2 : MIN_PX + ((Math.sqrt(w.occurrences) - lo) / (hi - lo)) * (MAX_PX - MIN_PX);
  }, [words]);

  if (error) return <Text style={styles.errorText}>{error}</Text>;
  if (entities === null) return <ActivityIndicator color={colors.accent} style={styles.spinner} />;
  if (words.length === 0) {
    return <Text style={[typeScale.muted, styles.empty]}>no entities extracted yet</Text>;
  }

  return (
    <ScrollView contentContainerStyle={{ padding: spacing.md }}>
      <View style={styles.cloud}>
        {words.map((w) => (
          <TouchableOpacity
            key={`${w.entity_type}-${w.normalized_value}`}
            onPress={() => setSelected(selected === w ? null : w)}
          >
            <Text
              style={{
                fontSize: sizeFor(w),
                color: colorForType(w.entity_type),
                fontWeight: selected === w ? "800" : "600",
              }}
            >
              {w.normalized_value}
            </Text>
          </TouchableOpacity>
        ))}
      </View>
      {selected && (
        <View style={styles.detailCard}>
          <Text style={styles.detailTitle}>{selected.normalized_value}</Text>
          <Text style={typeScale.muted}>
            {selected.entity_type} · {selected.occurrences} occurrence(s) in{" "}
            {selected.document_count} document(s)
          </Text>
        </View>
      )}
      <Text style={[typeScale.small, styles.legendNote]}>
        color = entity type · size = occurrences · tap a word for details
      </Text>
    </ScrollView>
  );
}

// ---- Categories ----

function CategoriesPanel({ onFilter }: { onFilter: (category: string) => void }) {
  const { token } = useAuth();
  const [categories, setCategories] = useState<CategorySummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    fetchCategories(token)
      .then(setCategories)
      .catch((e) => setError(e instanceof Error ? e.message : "failed to load categories"));
  }, [token]);

  if (error) return <Text style={styles.errorText}>{error}</Text>;
  if (categories === null) return <ActivityIndicator color={colors.accent} style={styles.spinner} />;
  if (categories.length === 0) {
    return <Text style={[typeScale.muted, styles.empty]}>no categories yet</Text>;
  }

  return (
    <ScrollView contentContainerStyle={{ padding: spacing.md }}>
      {categories.map((c) => (
        <TouchableOpacity key={c.name} style={styles.catRow} onPress={() => onFilter(c.name)}>
          <Text style={typeScale.body}>{c.name}</Text>
          <Text style={typeScale.small}>{c.document_count} doc(s) ›</Text>
        </TouchableOpacity>
      ))}
      <Text style={[typeScale.small, styles.legendNote]}>tap a category to filter the documents list</Text>
    </ScrollView>
  );
}

// ---- Embedding map as a cluster list (the phone stand-in for the WebGL map) ----

interface ClusterGroup {
  cluster: number;
  points: VizPoint[];
  documents: number;
}

function EmbeddingClustersPanel({ onOpenDocument }: { onOpenDocument: (id: string) => void }) {
  const { token } = useAuth();
  const [map, setMap] = useState<EmbeddingMap | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    fetchEmbeddingMap(token)
      .then(setMap)
      .catch((e) => setError(e instanceof Error ? e.message : "failed to load the projection"));
  }, [token]);

  const clusters = useMemo<ClusterGroup[]>(() => {
    const byCluster = new Map<number, VizPoint[]>();
    for (const p of map?.points ?? []) {
      if (p.cluster === null || p.cluster < 0) continue; // HDBSCAN noise (-1) / unclustered
      const list = byCluster.get(p.cluster) ?? [];
      list.push(p);
      byCluster.set(p.cluster, list);
    }
    return [...byCluster.entries()]
      .map(([cluster, points]) => ({
        cluster,
        points,
        documents: new Set(points.map((p) => p.document_id)).size,
      }))
      .sort((a, b) => b.points.length - a.points.length);
  }, [map]);

  if (error) return <Text style={styles.errorText}>{error}</Text>;
  if (map === null) return <ActivityIndicator color={colors.accent} style={styles.spinner} />;

  return (
    <ScrollView contentContainerStyle={{ padding: spacing.md }}>
      <Text style={[typeScale.small, styles.legendNote]}>
        the interactive map needs WebGL (web only) — this is the same 2D projection as a cluster
        list; tap a snippet to open its document
      </Text>
      {!map.computed ? (
        <Text style={[typeScale.muted, styles.empty]}>
          {map.recompute_pending
            ? "the projection is being computed — check back shortly"
            : "no projection computed yet (open the web insights once to build it)"}
        </Text>
      ) : clusters.length === 0 ? (
        <Text style={[typeScale.muted, styles.empty]}>
          {map.points.length} chunk(s) projected, but no clusters were detected
        </Text>
      ) : (
        clusters.map((g) => (
          <View key={g.cluster} style={styles.clusterCard}>
            <Text style={styles.clusterTitle}>
              cluster {g.cluster + 1} · {g.points.length} chunk(s) · {g.documents} document(s)
            </Text>
            {g.points.slice(0, 5).map((p) => (
              <TouchableOpacity key={p.chunk_id} onPress={() => onOpenDocument(p.document_id)}>
                <Text style={styles.snippet} numberOfLines={2}>
                  {p.snippet}
                </Text>
              </TouchableOpacity>
            ))}
            {g.points.length > 5 && (
              <Text style={typeScale.small}>+{g.points.length - 5} more</Text>
            )}
          </View>
        ))
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg },
  subBar: {
    flexDirection: "row",
    borderBottomColor: colors.border,
    borderBottomWidth: 1,
    backgroundColor: colors.surface,
  },
  subTab: { flex: 1, alignItems: "center", paddingVertical: spacing.sm + 2 },
  subTabActive: { borderBottomColor: colors.accent, borderBottomWidth: 2 },
  subText: { ...typeScale.muted },
  subTextActive: { color: colors.accent, fontWeight: "700" },
  spinner: { marginTop: spacing.xl },
  empty: { textAlign: "center", marginTop: spacing.xl, paddingHorizontal: spacing.xl },
  errorText: { ...typeScale.body, color: colors.danger, textAlign: "center", marginTop: spacing.xl },
  cloud: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "center",
    alignItems: "center",
    columnGap: spacing.md,
    rowGap: spacing.xs,
    paddingVertical: spacing.md,
  },
  detailCard: {
    backgroundColor: colors.surface,
    borderColor: colors.borderStrong,
    borderWidth: 1,
    borderRadius: 8,
    padding: spacing.md,
    marginTop: spacing.sm,
  },
  detailTitle: { ...typeScale.section, marginBottom: 2 },
  legendNote: { textAlign: "center", marginTop: spacing.md, color: colors.muted },
  catRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 8,
    padding: spacing.md,
    marginBottom: spacing.sm,
  },
  clusterCard: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 8,
    padding: spacing.md,
    marginBottom: spacing.sm,
  },
  clusterTitle: { ...typeScale.body, fontWeight: "700", marginBottom: spacing.xs },
  snippet: { ...typeScale.small, color: colors.text, paddingVertical: 3 },
});
