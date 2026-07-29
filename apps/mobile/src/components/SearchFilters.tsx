import React, { useEffect, useState } from "react";
import { StyleSheet, Switch, Text, TextInput, TouchableOpacity, View } from "react-native";

import { fetchCategories, fetchTags, type DocumentTag } from "../api/documents";
import { useAuth } from "../auth/AuthContext";
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
  const [tokensText, setTokensText] = useState("");
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

  function commitTokens(text: string) {
    setTokensText(text);
    const tokens = text
      .split(/[\s,]+/)
      .map((t) => t.trim())
      .filter(Boolean);
    set({ tokens });
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
          <TextInput
            style={styles.tokensInput}
            placeholder="tokens (space or comma separated)"
            placeholderTextColor={colors.muted}
            autoCapitalize="none"
            value={tokensText}
            onChangeText={commitTokens}
          />
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
            <View style={styles.chipsRow}>
              <TouchableOpacity
                style={[styles.chip, !value.category && styles.chipActive]}
                onPress={() => set({ category: undefined })}
              >
                <Text style={[styles.chipText, !value.category && styles.chipTextActive]}>any category</Text>
              </TouchableOpacity>
              {categories.map((c) => (
                <TouchableOpacity
                  key={c}
                  style={[styles.chip, value.category === c && styles.chipActive]}
                  onPress={() => set({ category: value.category === c ? undefined : c })}
                >
                  <Text style={[styles.chipText, value.category === c && styles.chipTextActive]}>{c}</Text>
                </TouchableOpacity>
              ))}
            </View>
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
  tokensInput: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 8,
    color: colors.text,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm + 2,
    fontSize: 14,
  },
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
});
