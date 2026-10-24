import { useEffect, useState } from "react";

/**
 * An <img> that loads its bytes through the global fetch instead of the src attribute.
 *
 * Why: the session JWT lives in memory + sessionStorage and is attached by the patched
 * window.fetch (session.ts). Plain <img src="/api/..."> requests carry no Authorization header,
 * so with password login enabled (and no edge-injected token) every media endpoint answers 401
 * and the UI falls back to placeholders. Routing the load through fetch attaches the session.
 * The byte response is shown via a blob object URL, revoked on unmount/src change.
 *
 * Errors surface through onError exactly like a plain <img>, so callers keep their existing
 * fallback rendering. While loading, nothing is rendered.
 */
export function AuthImg({
  src,
  onError,
  ...rest
}: Omit<React.ImgHTMLAttributes<HTMLImageElement>, "src"> & { src: string }) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let created: string | null = null;
    setObjectUrl(null);
    fetch(src)
      .then((res) => {
        if (!res.ok) throw new Error(`image fetch failed: ${res.status}`);
        return res.blob();
      })
      .then((blob) => {
        if (cancelled) return;
        created = URL.createObjectURL(blob);
        setObjectUrl(created);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        onError?.(
          err as unknown as React.SyntheticEvent<HTMLImageElement>,
        );
      });
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [src]);

  if (!objectUrl) return null;
  return <img src={objectUrl} onError={onError} {...rest} />;
}
