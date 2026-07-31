import { BACKEND_URL } from "../config";
import { apiFetch, ApiError } from "./client";

// Chat API (#776): server-side threads + SSE streaming, mirroring the web client
// (apps/ui/src/api.ts). The stream is read incrementally over XMLHttpRequest because RN's fetch
// has no streaming response body; the parseSse framing is a direct port of the web's.
export interface Citation {
  index: number;
  document_id: string;
  chunk_id: string;
  original_filename: string | null;
  title: string | null;
  page_start: number | null;
  page_end: number | null;
  snippet: string;
  score?: number | null;
}

export interface ChatThread {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  title_source?: "auto" | "manual";
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  reasoning?: string;
  citations?: Citation[];
}

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

export function listThreads(token: string): Promise<ChatThread[]> {
  return apiFetch<ChatThread[]>("/api/v1/chat/threads", { token });
}

export function createThread(token: string): Promise<ChatThread> {
  return apiFetch<ChatThread>("/api/v1/chat/threads", { method: "POST", token });
}

export function getThreadMessages(threadId: string, token: string): Promise<ChatMessage[]> {
  return apiFetch<ChatMessage[]>(`/api/v1/chat/threads/${threadId}/messages`, { token });
}

export function deleteThread(threadId: string, token: string): Promise<void> {
  return apiFetch<void>(`/api/v1/chat/threads/${threadId}`, { method: "DELETE", token });
}

/** SSE event subset the mobile client consumes (steps/ranking/metrics stay web-only for now). */
export interface ChatEvent {
  type: "meta" | "step" | "reasoning" | "token" | "sources" | "ranking" | "metrics" | "error" | "done";
  delta?: string;
  citations?: Citation[];
  grounded?: boolean;
  message?: string;
}

/** Parse accumulated SSE text into complete frames; `rest` carries a trailing partial frame.
 * Pure port of the web's parseSse. */
export function parseSse(buffer: string): { events: ChatEvent[]; rest: string } {
  const blocks = buffer.split("\n\n");
  const rest = blocks.pop() ?? "";
  const events: ChatEvent[] = [];
  for (const block of blocks) {
    if (!block.trim()) continue;
    let data = "";
    for (const line of block.split("\n")) {
      if (line.startsWith("data:")) data += line.slice(5).trim();
    }
    if (!data) continue;
    try {
      events.push(JSON.parse(data) as ChatEvent);
    } catch {
      // ignore a malformed frame rather than tearing down the whole stream
    }
  }
  return { events, rest };
}

export interface ChatStreamHandlers {
  onToken?: (delta: string) => void;
  onSources?: (citations: Citation[]) => void;
  onError?: (message: string) => void;
}

export interface ChatStreamHandle {
  /** Resolves on a clean end of stream, rejects on transport/HTTP errors. */
  done: Promise<{ grounded: boolean }>;
  abort: () => void;
}

/** Stream a chat answer (POST /api/v1/chat/stream). With a threadId the server loads/persists
 * history; without one the caller's `history` is used (stateless fallback, same as the web). */
export function streamChat(
  opts: {
    question: string;
    threadId: string | null;
    history: ChatTurn[];
    token: string;
    agentMode?: string;
    remember?: boolean;
  },
  handlers: ChatStreamHandlers,
): ChatStreamHandle {
  const xhr = new XMLHttpRequest();
  let grounded = false;

  const done = new Promise<{ grounded: boolean }>((resolve, reject) => {
    let seen = 0;
    let buffer = "";

    xhr.open("POST", `${BACKEND_URL}/api/v1/chat/stream`);
    xhr.setRequestHeader("Content-Type", "application/json");
    xhr.setRequestHeader("Authorization", `Bearer ${opts.token}`);

    xhr.onprogress = () => {
      buffer += xhr.responseText.slice(seen);
      seen = xhr.responseText.length;
      const parsed = parseSse(buffer);
      buffer = parsed.rest;
      for (const event of parsed.events) {
        switch (event.type) {
          case "token":
            if (event.delta) handlers.onToken?.(event.delta);
            break;
          case "sources":
            handlers.onSources?.(event.citations ?? []);
            break;
          case "error":
            handlers.onError?.(event.message ?? "the model failed while answering");
            break;
          case "done":
            grounded = event.grounded ?? false;
            break;
          // meta/step/reasoning/ranking/metrics are parsed but not rendered on mobile (yet).
        }
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve({ grounded });
        return;
      }
      let detail = `HTTP ${xhr.status}`;
      try {
        const body = JSON.parse(xhr.responseText) as { detail?: unknown };
        if (typeof body.detail === "string") detail = body.detail;
      } catch {
        // keep the status text
      }
      reject(new ApiError(xhr.status, detail));
    };
    xhr.onerror = () => reject(new ApiError(0, `cannot reach the backend at ${BACKEND_URL}`));
    xhr.onabort = () => resolve({ grounded }); // user-stopped: keep whatever streamed

    xhr.send(
      JSON.stringify({
        question: opts.question,
        history: opts.history,
        thread_id: opts.threadId,
        agent_mode: opts.agentMode ?? "agent",
        remember: opts.remember ?? true,
      }),
    );
  });

  return { done, abort: () => xhr.abort() };
}
