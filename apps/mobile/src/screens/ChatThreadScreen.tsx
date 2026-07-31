import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";

import { useAuth } from "../auth/AuthContext";
import {
  createThread,
  getThreadMessages,
  streamChat,
  type ChatMessage,
  type ChatStreamHandle,
  type ChatTurn,
  type Citation,
} from "../api/chat";
import { MarkdownText } from "../components/MarkdownText";
import { colors, spacing, typeScale } from "../theme";

// One chat thread (#776): persisted server-side (created lazily on the first question, like the
// web), streamed answer rendering, citation chips that open the referenced document. If thread
// creation fails the conversation falls back to stateless client-held history (web parity).
interface LiveTurn {
  question: string;
  answer: string;
  citations: Citation[];
  streaming: boolean;
  error: string | null;
}

type Row = {
  key: string;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  live?: LiveTurn;
};

export function ChatThreadScreen({
  threadId: initialThreadId,
  onOpenDocument,
  onTitle,
}: {
  threadId: string | null;
  onOpenDocument: (documentId: string) => void;
  onTitle?: (title: string) => void;
}) {
  const { token } = useAuth();
  const [threadId, setThreadId] = useState<string | null>(initialThreadId);
  const [messages, setMessages] = useState<ChatMessage[] | null>(initialThreadId ? null : []);
  const [live, setLive] = useState<LiveTurn | null>(null);
  const [input, setInput] = useState("");
  const streamRef = useRef<ChatStreamHandle | null>(null);
  const listRef = useRef<FlatList<Row>>(null);
  // Client-held history for the stateless fallback only (threaded turns live server-side).
  const historyRef = useRef<ChatTurn[]>([]);

  useEffect(() => {
    if (!initialThreadId || !token) return;
    getThreadMessages(initialThreadId, token)
      .then(setMessages)
      .catch(() => setMessages([]));
  }, [initialThreadId, token]);

  const scrollToEnd = useCallback(() => {
    requestAnimationFrame(() => listRef.current?.scrollToEnd({ animated: false }));
  }, []);

  async function send() {
    const question = input.trim();
    if (!question || !token || live?.streaming) return;
    setInput("");

    // Lazy thread creation on the first turn (the server auto-titles it from the question).
    let tid = threadId;
    if (!tid) {
      try {
        tid = (await createThread(token)).id;
        setThreadId(tid);
      } catch {
        tid = null; // persistence unavailable -> stateless fallback
      }
    }

    setLive({ question, answer: "", citations: [], streaming: true, error: null });
    const handle = streamChat(
      {
        question,
        threadId: tid,
        history: tid ? [] : historyRef.current,
        token,
      },
      {
        onToken: (d) => setLive((l) => (l ? { ...l, answer: l.answer + d } : l)),
        onSources: (c) => setLive((l) => (l ? { ...l, citations: c } : l)),
        onError: (m) => setLive((l) => (l ? { ...l, error: m } : l)),
      },
    );
    streamRef.current = handle;
    try {
      await handle.done;
    } catch (e) {
      setLive((l) =>
        l ? { ...l, streaming: false, error: e instanceof Error ? e.message : "stream failed" } : l,
      );
      streamRef.current = null;
      return;
    }
    streamRef.current = null;

    if (tid) {
      // Authoritative reload: persisted turns carry ids + citations (and the auto-title).
      try {
        const msgs = await getThreadMessages(tid, token);
        setMessages(msgs);
        const lastUser = [...msgs].reverse().find((m) => m.role === "user");
        if (lastUser) onTitle?.(lastUser.content.slice(0, 60));
      } catch {
        // keep the live turn visible; a later open reloads from the server
      }
    } else {
      setLive((l) => {
        if (l) {
          historyRef.current = [
            ...historyRef.current,
            { role: "user", content: l.question },
            { role: "assistant", content: l.answer },
          ];
          setMessages((prev) => [
            ...(prev ?? []),
            {
              id: `local-u-${Date.now()}`,
              role: "user",
              content: l.question,
              created_at: new Date().toISOString(),
            },
            {
              id: `local-a-${Date.now()}`,
              role: "assistant",
              content: l.answer,
              created_at: new Date().toISOString(),
              citations: l.citations,
            },
          ]);
        }
        return null;
      });
      return;
    }
    setLive(null);
  }

  function stop() {
    streamRef.current?.abort();
    streamRef.current = null;
    setLive((l) => (l ? { ...l, streaming: false } : l));
  }

  const rows: Row[] = [
    ...(messages ?? []).map((m) => ({
      key: m.id,
      role: m.role,
      content: m.content,
      citations: m.citations,
    })),
    ...(live
      ? ([
          { key: "live-q", role: "user", content: live.question },
          { key: "live-a", role: "assistant", content: live.answer, citations: live.citations, live },
        ] as Row[])
      : []),
  ];

  return (
    <KeyboardAvoidingView
      style={styles.root}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
      keyboardVerticalOffset={90}
    >
      {messages === null ? (
        <ActivityIndicator color={colors.accent} style={{ marginTop: spacing.xl }} />
      ) : (
        <FlatList
          ref={listRef}
          data={rows}
          keyExtractor={(r) => r.key}
          contentContainerStyle={{ padding: spacing.md, flexGrow: 1 }}
          onContentSizeChange={scrollToEnd}
          ListEmptyComponent={
            <Text style={[typeScale.muted, styles.empty]}>
              ask anything about your documents — e.g. "what did I pay for insurance last year?"
            </Text>
          }
          renderItem={({ item }) => (
            <View style={[styles.bubble, item.role === "user" ? styles.userBubble : styles.assistantBubble]}>
              {item.role === "user" ? (
                <Text style={styles.userText} selectable>
                  {item.content}
                </Text>
              ) : item.live ? (
                <View>
                  {item.live.streaming && item.content === "" && item.live.error === null && (
                    <View style={styles.thinking}>
                      <ActivityIndicator color={colors.accent} size="small" />
                      <Text style={typeScale.muted}>thinking…</Text>
                    </View>
                  )}
                  {item.content !== "" && <MarkdownText>{item.content}</MarkdownText>}
                  {item.live.error && (
                    <Text style={styles.errorText}>{item.live.error}</Text>
                  )}
                  <CitationChips citations={item.citations ?? []} onOpen={onOpenDocument} />
                </View>
              ) : (
                <View>
                  <MarkdownText>{item.content}</MarkdownText>
                  <CitationChips citations={item.citations ?? []} onOpen={onOpenDocument} />
                </View>
              )}
            </View>
          )}
        />
      )}

      <View style={styles.inputRow}>
        <TextInput
          style={styles.input}
          value={input}
          onChangeText={setInput}
          placeholder="ask your documents…"
          placeholderTextColor={colors.muted}
          multiline
          editable={!live?.streaming}
        />
        {live?.streaming ? (
          <TouchableOpacity style={styles.stopBtn} onPress={stop}>
            <Text style={styles.sendText}>stop</Text>
          </TouchableOpacity>
        ) : (
          <TouchableOpacity
            style={[styles.sendBtn, !input.trim() && styles.sendDisabled]}
            onPress={send}
            disabled={!input.trim()}
          >
            <Text style={styles.sendText}>send</Text>
          </TouchableOpacity>
        )}
      </View>
    </KeyboardAvoidingView>
  );
}

/** Tappable "[n] title" chips under an assistant answer; tapping opens the document. */
function CitationChips({
  citations,
  onOpen,
}: {
  citations: Citation[];
  onOpen: (documentId: string) => void;
}) {
  if (citations.length === 0) return null;
  return (
    <View style={styles.citations}>
      {citations.map((c) => (
        <TouchableOpacity
          key={`${c.index}-${c.chunk_id}`}
          style={styles.citationChip}
          onPress={() => onOpen(c.document_id)}
        >
          <Text style={styles.citationText} numberOfLines={1}>
            [{c.index}] {c.title || c.original_filename || "document"}
            {c.page_start ? ` · p.${c.page_start}` : ""}
          </Text>
        </TouchableOpacity>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg },
  empty: { textAlign: "center", marginTop: spacing.xl, paddingHorizontal: spacing.xl },
  bubble: {
    borderRadius: 10,
    padding: spacing.md,
    marginBottom: spacing.sm,
    maxWidth: "92%",
  },
  userBubble: {
    alignSelf: "flex-end",
    backgroundColor: colors.accentSoft,
  },
  assistantBubble: {
    alignSelf: "flex-start",
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
  },
  userText: { color: colors.text, fontSize: 14, lineHeight: 20 },
  thinking: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  errorText: { ...typeScale.small, color: colors.danger, marginTop: spacing.xs },
  citations: { flexDirection: "row", flexWrap: "wrap", gap: spacing.xs, marginTop: spacing.sm },
  citationChip: {
    borderColor: colors.accent,
    borderWidth: 1,
    borderRadius: 7,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
    maxWidth: "100%",
  },
  citationText: { color: colors.accent, fontSize: 11, fontWeight: "500" },
  inputRow: {
    flexDirection: "row",
    alignItems: "flex-end",
    gap: spacing.sm,
    padding: spacing.sm,
    borderTopColor: colors.border,
    borderTopWidth: 1,
    backgroundColor: colors.surface,
  },
  input: {
    flex: 1,
    color: colors.text,
    backgroundColor: colors.bg,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    maxHeight: 120,
    fontSize: 14,
  },
  sendBtn: {
    backgroundColor: colors.accent,
    borderRadius: 8,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm + 2,
  },
  stopBtn: {
    backgroundColor: colors.danger,
    borderRadius: 8,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm + 2,
  },
  sendDisabled: { backgroundColor: colors.borderStrong },
  sendText: { color: colors.bg, fontWeight: "700", fontSize: 14 },
});
