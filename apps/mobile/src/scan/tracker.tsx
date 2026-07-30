import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

import { useAuth } from "../auth/AuthContext";
import { fetchDocuments, type DokDocument } from "../api/documents";
import { fetchDocumentFeatures } from "../api/features";
import { notifyIngestion } from "./notify";

// Ingestion tracking (#775): the upload endpoint only returns filenames (the worker creates the
// document asynchronously), so we poll the recent-documents list until the file appears, then its
// feature ledger until every row is terminal. States: queued (not yet visible) -> processing ->
// ready | failed. Terminal transitions fire a local notification.
export type TrackedState = "queued" | "processing" | "ready" | "failed";

export interface TrackedUpload {
  id: string;
  filename: string;
  state: TrackedState;
  /** Failure reason (first failed feature's last_error, or the feature name). */
  detail?: string;
}

const POLL_MS = 4000;
const MAX_ITEMS = 20;

interface Tracker {
  uploads: TrackedUpload[];
  track: (filename: string) => void;
  clearFinished: () => void;
}

const TrackerContext = createContext<Tracker>({ uploads: [], track: () => {}, clearFinished: () => {} });

export function useIngestionTracker(): Tracker {
  return useContext(TrackerContext);
}

async function findDocument(filename: string, token: string): Promise<DokDocument | undefined> {
  const page = await fetchDocuments({ sort: "acquired", dir: "desc", limit: 25 }, token);
  return page.items.find((d) => d.original_filename === filename);
}

export function IngestionTrackerProvider({ children }: { children: React.ReactNode }) {
  const { token } = useAuth();
  const [uploads, setUploads] = useState<TrackedUpload[]>([]);
  // Latest snapshot for the poller (avoids stale closures); terminal items are skipped there.
  const uploadsRef = useRef(uploads);
  uploadsRef.current = uploads;
  const seq = useRef(0);

  const track = useCallback((filename: string) => {
    seq.current += 1;
    setUploads((prev) =>
      [{ id: `up-${seq.current}`, filename, state: "queued" as const }, ...prev].slice(0, MAX_ITEMS),
    );
  }, []);

  const clearFinished = useCallback(() => {
    setUploads((prev) => prev.filter((u) => u.state === "queued" || u.state === "processing"));
  }, []);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;

    async function pollOnce() {
      const active = uploadsRef.current.filter(
        (u) => u.state === "queued" || u.state === "processing",
      );
      if (active.length === 0) return;
      for (const item of active) {
        try {
          const doc = await findDocument(item.filename, token!);
          if (cancelled) return;
          if (!doc) continue; // still queued for the worker to pick up
          const rows = await fetchDocumentFeatures([doc.id], token!);
          if (cancelled) return;
          const failedRow = rows.find((r) => r.status === "failed");
          const busy = rows.some((r) => r.status === "pending" || r.status === "running");
          let next: TrackedUpload;
          if (failedRow) {
            next = {
              ...item,
              state: "failed",
              detail: failedRow.last_error ?? `${failedRow.feature} failed`,
            };
          } else if (doc.status === "active" && rows.length > 0 && !busy) {
            next = { ...item, state: "ready" };
          } else {
            next = { ...item, state: "processing" };
          }
          if (next.state !== item.state || next.detail !== item.detail) {
            setUploads((prev) => prev.map((u) => (u.id === item.id ? next : u)));
            if (next.state === "ready" || next.state === "failed") {
              void notifyIngestion(item.filename, next.state === "ready", next.detail);
            }
          }
        } catch {
          // Transient poll errors (backend restart, tunnel drop) keep the current state; the next
          // tick retries. Tracking is best-effort and must never crash the screen.
        }
      }
    }

    const id = setInterval(() => void pollOnce(), POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [token]);

  return (
    <TrackerContext.Provider value={{ uploads, track, clearFinished }}>
      {children}
    </TrackerContext.Provider>
  );
}
