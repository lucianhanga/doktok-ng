import React, { useCallback, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import { useFocusEffect } from "@react-navigation/native";

import { useAuth } from "../auth/AuthContext";
import { deleteThread, listThreads, type ChatThread } from "../api/chat";
import { colors, spacing, typeScale } from "../theme";

// Chat thread list (#776): pull-to-refresh, long-press to delete (with confirm), "new
// conversation" on top. Opening a thread (or starting a new one) pushes ChatThread.
export function ThreadsScreen({
  onOpenThread,
  onNewChat,
}: {
  onOpenThread: (threadId: string, title: string) => void;
  onNewChat: () => void;
}) {
  const { token } = useAuth();
  const [threads, setThreads] = useState<ChatThread[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      setThreads(await listThreads(token));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load conversations");
    }
  }, [token]);

  // Reload every time the tab/screen regains focus (a chat may have been titled meanwhile).
  useFocusEffect(
    useCallback(() => {
      void load();
    }, [load]),
  );

  async function refresh() {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  }

  function confirmDelete(thread: ChatThread) {
    if (!token) return;
    Alert.alert(
      "Delete conversation?",
      thread.title || "untitled conversation",
      [
        { text: "cancel", style: "cancel" },
        {
          text: "delete",
          style: "destructive",
          onPress: () => {
            void deleteThread(thread.id, token)
              .then(load)
              .catch((e) =>
                setError(e instanceof Error ? e.message : "failed to delete conversation"),
              );
          },
        },
      ],
    );
  }

  return (
    <View style={styles.root}>
      <TouchableOpacity style={styles.newBtn} onPress={onNewChat}>
        <Text style={styles.newBtnText}>new conversation</Text>
      </TouchableOpacity>
      {error && <Text style={styles.error}>{error}</Text>}
      {threads === null ? (
        <ActivityIndicator color={colors.accent} style={{ marginTop: spacing.xl }} />
      ) : threads.length === 0 ? (
        <Text style={[typeScale.muted, styles.empty]}>
          no conversations yet — ask your documents something
        </Text>
      ) : (
        <FlatList
          data={threads}
          keyExtractor={(t) => t.id}
          refreshing={refreshing}
          onRefresh={refresh}
          renderItem={({ item }) => (
            <TouchableOpacity
              style={styles.row}
              onPress={() => onOpenThread(item.id, item.title)}
              onLongPress={() => confirmDelete(item)}
            >
              <Text style={styles.rowTitle} numberOfLines={1}>
                {item.title || "untitled conversation"}
              </Text>
              <Text style={typeScale.small} numberOfLines={1}>
                {Math.ceil(item.message_count / 2)} turn(s) ·{" "}
                {new Date(item.updated_at).toLocaleDateString(undefined, {
                  day: "numeric",
                  month: "short",
                })}
              </Text>
            </TouchableOpacity>
          )}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg, padding: spacing.md },
  newBtn: {
    backgroundColor: colors.accent,
    borderRadius: 8,
    alignItems: "center",
    paddingVertical: spacing.md,
    marginBottom: spacing.md,
  },
  newBtnText: { color: colors.bg, fontWeight: "700", fontSize: 15 },
  error: { ...typeScale.small, color: colors.danger, marginBottom: spacing.sm },
  empty: { textAlign: "center", marginTop: spacing.xl },
  row: {
    backgroundColor: colors.surface,
    borderRadius: 8,
    borderColor: colors.border,
    borderWidth: 1,
    padding: spacing.md,
    marginBottom: spacing.sm,
  },
  rowTitle: { ...typeScale.body, fontWeight: "600", marginBottom: 2 },
});
