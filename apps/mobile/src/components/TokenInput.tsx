import React, { useEffect, useRef, useState } from "react";
import {
  FlatList,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";

import { suggestTokens, type TokenSuggestion } from "../api/documents";
import { useAuth } from "../auth/AuthContext";
import { colors, spacing, typeScale } from "../theme";

// Token chip input (#800), same behaviour as the web token field: type -> AND-constrained
// completions (prefix + already-selected tokens) with document counts -> tap to add a chip ->
// chips removable with x, backspace on empty input pops the last chip. Submit adds the typed
// text as a free token.
export function TokenInput({
  tokens,
  onChange,
}: {
  tokens: string[];
  onChange: (tokens: string[]) => void;
}) {
  const { token } = useAuth();
  const [text, setText] = useState("");
  const [suggestions, setSuggestions] = useState<TokenSuggestion[]>([]);
  const [loading, setLoading] = useState(false);
  const abortRef = useRef(0);

  // Debounced completion lookup (respecting the selected chips via AND).
  useEffect(() => {
    const prefix = text.trim();
    if (!token || prefix.length === 0) {
      setSuggestions([]);
      return;
    }
    const seq = ++abortRef.current;
    const id = setTimeout(() => {
      setLoading(true);
      suggestTokens(prefix, tokens, token)
        .then((rows) => {
          if (seq === abortRef.current) setSuggestions(rows);
        })
        .catch(() => {
          if (seq === abortRef.current) setSuggestions([]);
        })
        .finally(() => {
          if (seq === abortRef.current) setLoading(false);
        });
    }, 250);
    return () => clearTimeout(id);
  }, [text, tokens, token]);

  function add(value: string) {
    const v = value.trim();
    if (!v || tokens.includes(v)) {
      setText("");
      setSuggestions([]);
      return;
    }
    onChange([...tokens, v]);
    setText("");
    setSuggestions([]);
  }

  function removeAt(i: number) {
    onChange(tokens.filter((_, j) => j !== i));
  }

  function onKeyPress(e: { nativeEvent: { key: string } }) {
    if (e.nativeEvent.key === "Backspace" && text === "" && tokens.length > 0) {
      removeAt(tokens.length - 1);
    }
  }

  return (
    <View>
      <View style={styles.inputWrap}>
        {tokens.map((t, i) => (
          <View key={t} style={styles.chip}>
            <Text style={styles.chipText}>{t}</Text>
            <TouchableOpacity onPress={() => removeAt(i)} accessibilityLabel={`remove ${t}`}>
              <Text style={styles.chipX}>×</Text>
            </TouchableOpacity>
          </View>
        ))}
        <TextInput
          style={styles.input}
          placeholder={tokens.length ? "add token…" : "tokens (type to complete)"}
          placeholderTextColor={colors.muted}
          autoCapitalize="none"
          value={text}
          onChangeText={setText}
          onSubmitEditing={() => add(text)}
          onKeyPress={onKeyPress}
          returnKeyType="done"
        />
      </View>
      {(suggestions.length > 0 || loading) && text.trim().length > 0 && (
        <View style={styles.suggestions}>
          <FlatList
            data={suggestions}
            keyExtractor={(s) => s.value}
            keyboardShouldPersistTaps="handled"
            renderItem={({ item }) => (
              <TouchableOpacity style={styles.suggestionRow} onPress={() => add(item.value)}>
                <Text style={typeScale.body}>{item.value}</Text>
                <Text style={typeScale.small}>{item.document_count} docs</Text>
              </TouchableOpacity>
            )}
            ListFooterComponent={
              loading ? <Text style={[typeScale.small, styles.loading]}>…</Text> : null
            }
          />
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  inputWrap: {
    flexDirection: "row",
    flexWrap: "wrap",
    alignItems: "center",
    gap: spacing.xs,
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
  },
  chip: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.accentSoft,
    borderColor: colors.accent,
    borderWidth: 1,
    borderRadius: 14,
    paddingLeft: spacing.sm,
  },
  chipText: { ...typeScale.small, color: colors.accent, fontWeight: "600" },
  chipX: { color: colors.accent, fontSize: 16, paddingHorizontal: spacing.sm, paddingVertical: 2 },
  input: { flex: 1, minWidth: 120, color: colors.text, paddingVertical: spacing.sm, fontSize: 14 },
  suggestions: {
    backgroundColor: colors.surfaceAlt,
    borderColor: colors.borderStrong,
    borderWidth: 1,
    borderRadius: 8,
    marginTop: spacing.xs,
    maxHeight: 220,
  },
  suggestionRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderBottomColor: colors.border,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  loading: { textAlign: "center", paddingVertical: spacing.sm },
});
