"""DB-anchored tamper-evidence for the backup history (F-41, #653).

The sha256 chain in history.jsonl is UNKEYED and verified only over a tail window: a host
attacker who rewrites the file recomputes the chain, and silently deleting the tail verifies OK.
The newest line's (seq, sha256) is therefore anchored into the app settings on every intact read:
a later read flags the anchored seq re-appearing with a different hash (rewritten) or the head
seq regressing (tail deleted / rolled back). The anchor advances only on an intact window, so a
tampered file never blesses itself.
"""

from __future__ import annotations

import hashlib
import json


def _seq_of(line: str) -> int | None:
    try:
        seq = json.loads(line).get("seq")
    except json.JSONDecodeError:
        return None
    return seq if isinstance(seq, int) and not isinstance(seq, bool) else None


def _sha(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def anchor_check(
    lines: list[str], anchor: dict[str, object] | None
) -> tuple[bool, dict[str, object] | None]:
    """Check the history window against the anchored head; return (ok, anchor-to-store).

    ``lines`` are the oldest-first raw JSONL lines of the read window. ``anchor`` is the
    previously stored ``{"seq": int, "sha256": str}`` (None on first read). The returned anchor
    advances ONLY when the window is intact against the old one - a tampered file keeps the old
    anchor, so the failure persists instead of blessing the new state.
    """
    if not lines:
        return True, anchor
    head = lines[-1]
    head_seq = _seq_of(head)
    if head_seq is None:
        return True, anchor  # pre-seq legacy lines: nothing to anchor on
    ok = True
    if anchor is not None:
        anchored_seq = anchor.get("seq")
        anchored_sha = anchor.get("sha256")
        if isinstance(anchored_seq, int):
            if head_seq < anchored_seq:
                # The head REGRESSED: the tail was deleted or the file was rolled back.
                ok = False
            elif isinstance(anchored_sha, str):
                for line in lines:
                    if _seq_of(line) == anchored_seq:
                        if _sha(line) != anchored_sha:
                            # The anchored line's bytes changed: the file was rewritten (the
                            # attacker can recompute the unkeyed chain, but the head changes).
                            ok = False
                        break
    if not ok:
        return False, anchor
    return True, {"seq": head_seq, "sha256": _sha(head)}
