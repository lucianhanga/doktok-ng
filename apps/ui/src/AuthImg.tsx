import { useEffect } from "react";

import { useAuthBlobUrl } from "./authMedia";

/**
 * An <img> that loads its bytes through the global fetch instead of the src attribute.
 *
 * Why: the session JWT lives in memory + sessionStorage and is attached by the patched
 * window.fetch (session.ts). Plain <img src="/api/..."> requests carry no Authorization header,
 * so with password login enabled (and no edge-injected token) every media endpoint answers 401
 * and the UI falls back to placeholders. Routing the load through fetch attaches the session.
 * The byte response is shown via a blob object URL (see authMedia.ts), revoked on
 * unmount/src change.
 *
 * Errors surface through onError exactly like a plain <img>, so callers keep their existing
 * fallback rendering. While loading, nothing is rendered.
 */
export function AuthImg({
  src,
  onError,
  ...rest
}: Omit<React.ImgHTMLAttributes<HTMLImageElement>, "src"> & { src: string }) {
  const { blobUrl, error } = useAuthBlobUrl(src);

  useEffect(() => {
    if (error) onError?.({} as React.SyntheticEvent<HTMLImageElement>);
  }, [error, onError]);

  if (!blobUrl) return null;
  return <img src={blobUrl} onError={onError} {...rest} />;
}
