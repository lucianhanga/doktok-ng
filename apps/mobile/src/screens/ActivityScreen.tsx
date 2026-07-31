import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";

import { useAuth } from "../auth/AuthContext";
import { fetchActivity, type AuditEvent } from "../api/activity";
import { fetchDrpStatus, type BackupLegStatus, type DrpStatusResponse } from "../api/drp";
import { colors, spacing, typeScale } from "../theme";

// Activity tab (#779): the audit timeline (filterable, paginated) + a read-only DRP card view.
// DRP is admin-only by product decision (the endpoint itself is tenant-readable; mobile hides it
// for non-admins with a clean unavailable state). No mutations anywhere here.
type Sub = "activity" | "drp";
const SUBS: { id: Sub; label: string }[] = [
  { id: "activity", label: "Activity" },
  { id: "drp", label: "DRP" },
];

const PAGE = 100;

export function ActivityScreen() {
  const { user } = useAuth();
  const [sub, setSub] = useState<Sub>("activity");
  const isAdmin = user?.role === "admin";

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
      {sub === "activity" && <ActivityFeed />}
      {sub === "drp" && (isAdmin ? <DrpPanel /> : <DrpUnavailable />)}
    </View>
  );
}

/** Human "Nh ago" age from an ISO timestamp. */
export function timeAgo(iso: string): string {
  const seconds = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  if (seconds < 90) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

// ---- Activity feed ----

function eventPrefix(eventType: string): string {
  return eventType.split(".")[0] || eventType;
}

function ActivityFeed() {
  const { token } = useAuth();
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [exhausted, setExhausted] = useState(false);
  const [prefix, setPrefix] = useState<string>("all");

  const load = useCallback(
    async (offset: number, mode: "replace" | "more") => {
      if (!token) return;
      try {
        const page = await fetchActivity(token, { limit: PAGE, offset });
        if (page.length < PAGE) setExhausted(true);
        setEvents((prev) => (mode === "more" ? [...(prev ?? []), ...page] : page));
        setError(null);
      } catch (e) {
        setError(e instanceof Error ? e.message : "failed to load activity");
      }
    },
    [token],
  );

  useEffect(() => {
    void load(0, "replace");
  }, [load]);

  async function refresh() {
    setRefreshing(true);
    setExhausted(false);
    await load(0, "replace");
    setRefreshing(false);
  }

  async function more() {
    if (loadingMore || exhausted) return;
    setLoadingMore(true);
    await load(events?.length ?? 0, "more");
    setLoadingMore(false);
  }

  // Prefix filter chips derived from what's actually in the feed (client-side; the backend only
  // pages). Non-admin feeds are pre-scoped server-side, so chips reflect that subset.
  const prefixes = useMemo(
    () => ["all", ...new Set((events ?? []).map((e) => eventPrefix(e.event_type)))],
    [events],
  );
  const visible = useMemo(
    () => (prefix === "all" ? (events ?? []) : (events ?? []).filter((e) => eventPrefix(e.event_type) === prefix)),
    [events, prefix],
  );

  if (error) return <Text style={styles.errorText}>{error}</Text>;
  if (events === null) return <ActivityIndicator color={colors.accent} style={styles.spinner} />;

  return (
    <View style={{ flex: 1 }}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.chipsBar}>
        {prefixes.map((p) => (
          <TouchableOpacity
            key={p}
            style={[styles.chip, prefix === p && styles.chipActive]}
            onPress={() => setPrefix(p)}
          >
            <Text style={[styles.chipText, prefix === p && styles.chipTextActive]}>{p}</Text>
          </TouchableOpacity>
        ))}
      </ScrollView>
      <ScrollView
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.accent} />}
        contentContainerStyle={{ paddingBottom: spacing.lg }}
      >
        {visible.length === 0 ? (
          <Text style={[typeScale.muted, styles.empty]}>no activity yet</Text>
        ) : (
          visible.map((ev) => (
            <View key={ev.id} style={styles.eventRow}>
              <Text style={typeScale.body}>{ev.description || ev.event_type}</Text>
              <Text style={typeScale.small}>
                {ev.event_type} · {timeAgo(ev.timestamp)} · {ev.actor}
              </Text>
            </View>
          ))
        )}
        {!exhausted && visible.length > 0 && (
          <TouchableOpacity style={styles.moreBtn} onPress={more} disabled={loadingMore}>
            {loadingMore ? (
              <ActivityIndicator color={colors.accent} size="small" />
            ) : (
              <Text style={styles.moreText}>load more</Text>
            )}
          </TouchableOpacity>
        )}
      </ScrollView>
    </View>
  );
}

// ---- DRP read-only view ----

const LEGS: { key: "files" | "pg" | "offsite" | "drill"; label: string }[] = [
  { key: "files", label: "Files" },
  { key: "pg", label: "Postgres" },
  { key: "offsite", label: "Azure offsite" },
  { key: "drill", label: "Recovery drill" },
];

function legColor(state: string): string {
  if (state === "ok") return colors.success;
  if (state === "stale") return colors.warning;
  if (state === "failed") return colors.danger;
  return colors.muted;
}

function LegCard({ name, leg }: { name: string; leg: BackupLegStatus }) {
  return (
    <View style={styles.legCard}>
      <View style={styles.legHead}>
        <Text style={typeScale.section}>{name}</Text>
        <Text style={[styles.legState, { color: legColor(leg.state) }]}>{leg.state}</Text>
      </View>
      <Text style={typeScale.muted}>
        {leg.last_run_at ? timeAgo(leg.last_run_at) : "never run"}
        {leg.size ? ` · ${leg.size}` : ""}
        {leg.backup_id ? ` · ${leg.backup_id.slice(0, 12)}` : ""}
      </Text>
      {leg.detail !== "" && <Text style={typeScale.small}>{leg.detail}</Text>}
    </View>
  );
}

function DrpUnavailable() {
  return (
    <View style={styles.unavailable}>
      <Text style={typeScale.section}>DRP status</Text>
      <Text style={[typeScale.muted, { textAlign: "center", marginTop: spacing.sm }]}>
        disaster-recovery status is available to tenant admins — sign in with an admin account to
        see backup legs and drill results
      </Text>
    </View>
  );
}

function DrpPanel() {
  const { token } = useAuth();
  const [drp, setDrp] = useState<DrpStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      setDrp(await fetchDrpStatus(token));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load DRP status");
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  async function refresh() {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  }

  if (error) return <Text style={styles.errorText}>{error}</Text>;
  if (drp === null) return <ActivityIndicator color={colors.accent} style={styles.spinner} />;

  return (
    <ScrollView
      contentContainerStyle={{ padding: spacing.md }}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.accent} />}
    >
      {!drp.status.status_source_available && (
        <Text style={[typeScale.small, styles.warnNote]}>
          no backup status found yet — legs show as unknown until the first backup runs
        </Text>
      )}
      {LEGS.map(({ key, label }) => (
        <LegCard key={key} name={label} leg={drp.status[key]} />
      ))}
      {drp.status.wal_lag_seconds !== null && (
        <Text style={[typeScale.small, { textAlign: "center" }]}>
          WAL recovery point lag: {drp.status.wal_lag_seconds}s
        </Text>
      )}
      <View style={styles.configCard}>
        <Text style={[typeScale.small, { fontWeight: "700" }]}>targets &amp; config</Text>
        <Text style={typeScale.small}>
          RPO files {Math.round(drp.config.rpo_files_seconds / 3600)}h · pg{" "}
          {Math.round(drp.config.rpo_pg_seconds / 3600)}h · offsite{" "}
          {Math.round(drp.config.rpo_offsite_seconds / 86400)}d · RTO{" "}
          {Math.round(drp.config.rto_seconds / 3600)}h
        </Text>
        <Text style={typeScale.small} numberOfLines={1}>
          repo: {drp.config.repo_location}
        </Text>
        <Text style={typeScale.small} numberOfLines={1}>
          azure: {drp.config.azure_container || "not configured"}
          {drp.config.immutability_enabled ? " (immutable)" : ""}
        </Text>
      </View>
      <Text style={[typeScale.small, styles.legendNote]}>read-only — recovery actions run on the host</Text>
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
  empty: { textAlign: "center", marginTop: spacing.xl },
  errorText: { ...typeScale.body, color: colors.danger, textAlign: "center", marginTop: spacing.xl },
  chipsBar: {
    flexGrow: 0,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  chip: {
    borderColor: colors.borderStrong,
    borderWidth: 1,
    borderRadius: 12,
    paddingHorizontal: spacing.md,
    paddingVertical: 2,
    marginRight: spacing.sm,
  },
  chipActive: { borderColor: colors.accent, backgroundColor: colors.surfaceAlt },
  chipText: { ...typeScale.small },
  chipTextActive: { color: colors.accent, fontWeight: "700" },
  eventRow: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm + 2,
    borderBottomColor: colors.border,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  moreBtn: { alignItems: "center", paddingVertical: spacing.md },
  moreText: { color: colors.accent, fontWeight: "700" },
  legCard: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 8,
    padding: spacing.md,
    marginBottom: spacing.sm,
  },
  legHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  legState: { ...typeScale.small, fontWeight: "800", textTransform: "uppercase" },
  warnNote: { color: colors.warning, textAlign: "center", marginBottom: spacing.sm },
  configCard: {
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 8,
    padding: spacing.md,
    marginTop: spacing.xs,
    gap: 2,
  },
  unavailable: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xl },
  legendNote: { textAlign: "center", marginTop: spacing.md, color: colors.muted },
});
