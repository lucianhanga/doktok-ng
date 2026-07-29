import React, { useEffect, useState } from "react";
import { FlatList, StyleSheet, Switch, Text, TouchableOpacity, View } from "react-native";

import { fetchCategories, fetchTags, type DocumentTag } from "../api/documents";
import { useAuth } from "../auth/AuthContext";
import { TokenInput } from "./TokenInput";
import { colors, spacing, typeScale } from "../theme";

// Complex document search (#800): title (handled by the parent) plus this collapsible panel -
// full-text tokens (all/any), category, status, attention flags, and tag chips. Mirrors the
// backend's /api/v1/documents filter semantics exactly.
export interface SearchFiltersValue {
  tokens: string[];
  tokenMatch: "all" | "any";
  category?: string;
  status?: string;
  needsAttention: boolean;
  unidentifiable: boolean;
  tags: string[];
  tagMatch: "all" | "any";
}

export const EMPTY_FILTERS: SearchFiltersValue = {
  tokens: [],
  tokenMatch: "all",
  needsAttention: false,
  unidentifiable: false,
  tags: [],
  tagMatch: "all",
};

// Compact single-select dropdown for filter fields with many options (#800): a button showing
// the current value that opens a small capped overlay list - replaces the category chip wall.
function FilterDropdown({
  label,
  value,
  options,
  onSelect,
}: {
  label: string;
  value?: string;
  options: string[];
  onSelect: (v: string | undefined) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <View style={styles.dropdownWrap}>
      <TouchableOpacity style={styles.dropdownBtn} onPress={() => setOpen(!open)}>
        <Text style={[styles.dropdownText, value ? styles.dropdownTextActive : undefined]}>
          {label}: {value ?? "any"} {open ? "▴" : "▾"}
        </Text>
      </TouchableOpacity>
      {open && (
        <View style={styles.dropdownOverlay}>
          <FlatList
            data={[undefined, ...options] as (string | undefined)[]}
            keyExtractor={(o) => o ?? "__any__"}
            keyboardShouldPersistTaps="handled"
            renderItem={({ item }) => (
              <TouchableOpacity
                style={styles.dropdownRow}
                onPress={() => {
                  onSelect(item);
                  setOpen(false);
                }}
              >
                <Text style={[typeScale.body, item === value && styles.dropdownTextActive]} numberOfLines={1}>
                  {item ?? "any"}
                </Text>
                {item === value && <Text style={styles.dropdownCheck}>✓</Text>}
              </TouchableOpacity>
            )}
          />
        </View>
      )}
    </View>
  );
}

const STATUS_OPTIONS: { value: string | undefined; label: string }[] = [
  { value: undefined, label: "any status" },
  { value: "active", label: "active" },
  { value: "processing", label: "processing" },
  { value: "failed", label: "failed" },
  { value: "duplicate", label: "duplicate" },
];

export function SearchFilters({
  value,
  onChange,
}: {
  value: SearchFiltersValue;
  onChange: (v: SearchFiltersValue) => void;
}) {
  const { token } = useAuth();
  const [open, setOpen] = useState(false);
  const [categories, setCategories] = useState<string[]>([]);
  const [tags, setTags] = useState<DocumentTag[]>([]);

  useEffect(() => {
    if (!token) return;
    fetchCategories(token)
      .then((rows) => setCategories(rows.map((r) => r.name)))
      .catch(() => setCategories([]));
    fetchTags(token)
      .then(setTags)
      .catch(() => setTags([]));
  }, [token]);

  const activeCount =
    value.tokens.length +
    (value.category ? 1 : 0) +
    (value.status ? 1 : 0) +
    (value.needsAttention ? 1 : 0) +
    (value.unidentifiable ? 1 : 0) +
    value.tags.length;

  function set(patch: Partial<SearchFiltersValue>) {
    onChange({ ...value, ...patch });
  }


  function toggleTag(name: string) {
    const has = value.tags.includes(name);
    set({ tags: has ? value.tags.filter((t) => t !== name) : [...value.tags, name] });
  }

  return (
    <View style={styles.root}>
      <TouchableOpacity style={styles.toggle} onPress={() => setOpen(!open)}>
        <Text style={[typeScale.muted, { color: activeCount ? colors.accent : colors.muted }]}>
          {open ? "▾ filters" : "▸ filters"}
          {activeCount > 0 ? ` (${activeCount} active)` : ""}
        </Text>
      </TouchableOpacity>

      {open && (
        <View style={styles.panel}>
          <TokenInput tokens={value.tokens} onChange={(tokens) => set({ tokens })} />
          <View style={styles.chipsRow}>
            {(["all", "any"] as const).map((m) => (
              <TouchableOpacity
                key={m}
                style={[styles.chip, value.tokenMatch === m && styles.chipActive]}
                onPress={() => set({ tokenMatch: m })}
              >
                <Text style={[styles.chipText, value.tokenMatch === m && styles.chipTextActive]}>
                  match {m}
                </Text>
              </TouchableOpacity>
            ))}
          </View>

          {categories.length > 0 && (
            <FilterDropdown
              label="category"
              value={value.category}
              options={categories}
              onSelect={(c) => set({ category: c })}
            />
          )}

          <View style={styles.chipsRow}>
            {STATUS_OPTIONS.map((o) => (
              <TouchableOpacity
                key={o.label}
                style={[styles.chip, value.status === o.value && styles.chipActive]}
                onPress={() => set({ status: o.value })}
              >
                <Text style={[styles.chipText, value.status === o.value && styles.chipTextActive]}>
                  {o.label}
                </Text>
              </TouchableOpacity>
            ))}
          </View>

          <View style={styles.switchRow}>
            <Text style={typeScale.muted}>needs attention</Text>
            <Switch
              value={value.needsAttention}
              onValueChange={(v) => set({ needsAttention: v })}
              trackColor={{ true: colors.accentSoft, false: colors.border }}
              thumbColor={value.needsAttention ? colors.accent : colors.muted}
            />
            <Text style={typeScale.muted}>unidentifiable</Text>
            <Switch
              value={value.unidentifiable}
              onValueChange={(v) => set({ unidentifiable: v })}
              trackColor={{ true: colors.accentSoft, false: colors.border }}
              thumbColor={value.unidentifiable ? colors.accent : colors.muted}
            />
          </View>

          {tags.length > 0 && (
            <View style={styles.chipsRow}>
              {tags.map((t) => {
                const active = value.tags.includes(t.name);
                return (
                  <TouchableOpacity
                    key={t.id}
                    style={[styles.chip, active && { borderColor: colors.accent, backgroundColor: colors.accentSoft }]}
                    onPress={() => toggleTag(t.name)}
                  >
                    <Text style={[styles.chipText, active && styles.chipTextActive]}>{t.name}</Text>
                  </TouchableOpacity>
                );
              })}
            </View>
          )}
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { paddingHorizontal: spacing.md },
  toggle: { paddingVertical: spacing.xs },
  panel: { paddingTop: spacing.xs, gap: spacing.sm },
  chipsRow: { flexDirection: "row", flexWrap: "wrap", gap: spacing.xs },
  chip: {
    borderColor: colors.borderStrong,
    borderWidth: 1,
    borderRadius: 14,
    paddingHorizontal: spacing.sm + 2,
    paddingVertical: spacing.xs + 1,
  },
  chipActive: { borderColor: colors.accent, backgroundColor: colors.accentSoft },
  chipText: { ...typeScale.small },
  chipTextActive: { color: colors.accent, fontWeight: "700" },
  switchRow: { flexDirection: "row", alignItems: "center", gap: spacing.md },
  dropdownWrap: { zIndex: 15 },
  dropdownBtn: {
    alignSelf: "flex-start",
    borderColor: colors.borderStrong,
    borderWidth: 1,
    borderRadius: 14,
    paddingHorizontal: spacing.sm + 2,
    paddingVertical: spacing.xs + 1,
  },
  dropdownText: { ...typeScale.small },
  dropdownTextActive: { color: colors.accent, fontWeight: "700" },
  dropdownOverlay: {
    position: "absolute",
    top: "100%",
    left: 0,
    minWidth: 220,
    maxWidth: 320,
    maxHeight: 8 * 44,
    backgroundColor: colors.surfaceAlt,
    borderColor: colors.borderStrong,
    borderWidth: 1,
    borderRadius: 8,
    marginTop: spacing.xs,
    overflow: "hidden",
    shadowColor: "#000",
    shadowOpacity: 0.35,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 4 },
    elevation: 8,
    zIndex: 25,
  },
  dropdownRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderBottomColor: colors.border,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  dropdownCheck: { color: colors.accent, fontWeight: "700" },
});
