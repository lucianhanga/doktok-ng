import { useEffect, useState } from "react";

/**
 * Authenticated media loading for surfaces that cannot carry the session JWT themselves.
 *
 * The session token lives in memory + sessionStorage and is attached by the patched window.fetch
 * (session.ts). Raw /api/... URLs in <iframe>, <img>, <a target="_blank"> or download links go
 * out WITHOUT it, so with password login enabled (and no edge-injected token) they get a 401.
 * These helpers fetch the bytes through the patched fetch and hand out blob: object URLs, which
 * render/navigate same-origin without any credential.
 */

/** Load `url` via the authenticated fetch and expose it as a blob object URL. */
export function useAuthBlobUrl(url: string | null): { blobUrl: string | null; error: boolean } {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!url) {
      setBlobUrl(null);
      setError(false);
      return;
    }
    let cancelled = false;
    let created: string | null = null;
    setBlobUrl(null);
    setError(false);
    fetch(url)
      .then((res) => {
        if (!res.ok) throw new Error(`media fetch failed: ${res.status}`);
        return res.blob();
      })
      .then((blob) => {
        if (cancelled) return;
        created = URL.createObjectURL(blob);
        setBlobUrl(created);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [url]);

  return { blobUrl, error };
}

async function fetchBlobUrl(url: string): Promise<string> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`media fetch failed: ${res.status}`);
  return URL.createObjectURL(await res.blob());
}

/** Open an authenticated API URL in a new tab. The window is opened synchronously (popup
 * blockers kill async window.open), then pointed at the blob URL once the bytes arrive. */
export function openInNewTab(url: string): void {
  const w = window.open("", "_blank", "noopener");
  fetchBlobUrl(url)
    .then((blobUrl) => {
      if (w) w.location.href = blobUrl;
    })
    .catch(() => w?.close());
}

/** Download an authenticated API URL under its original filename. */
export function downloadFile(url: string, filename: string): void {
  fetchBlobUrl(url)
    .then((blobUrl) => {
      const a = document.createElement("a");
      a.href = blobUrl;
      a.download = filename;
      a.click();
      // The anchor click is synchronous; give the browser a moment before revoking.
      setTimeout(() => URL.revokeObjectURL(blobUrl), 10_000);
    })
    .catch(() => undefined);
}
