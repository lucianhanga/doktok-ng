import { useCallback, useEffect, useState } from "react";

import {
  createTag,
  deleteTag,
  fetchTags,
  mergeTag,
  patchTag,
  type TagOut,
} from "./api";
import { TAG_PALETTE_TOKENS, tagChipStyle, tagColor } from "./tagPalette";

/** Token-set Jaccard over normalized tag names (#550): pins near-duplicates in the merge dialog. */
function tokenJaccard(a: string, b: string): number {
  const ta = new Set(a.split(" "));
  const tb = new Set(b.split(" "));
  if (ta.size === 0 || tb.size === 0) return 0;
  let inter = 0;
  for (const t of ta) if (tb.has(t)) inter += 1;
  return inter / (ta.size + tb.size - inter);
}

/** A tag badge: rounded pill + colored dot + low-alpha tint (#547 design, distinct from category chips). */
export function TagChip({ name, color }: { name: string; color: string }) {
  return (
    <span className="tag-chip" style={tagChipStyle(color)}>
      <span
        className="tag-dot"
        style={{ backgroundColor: tagColor(color).dot }}
        aria-hidden="true"
      />
      {name}
    </span>
  );
}

function TagEditor({
  initial,
  onDone,
  onCancel,
}: {
  /** null = create, a tag = edit. */
  initial: TagOut | null;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [color, setColor] = useState(initial?.color ?? "slate");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The create-time warnings from the API: exact duplicate (blocked) or near-miss (retryable).
  const [warning, setWarning] = useState<{ message: string; retryable: boolean } | null>(null);

  async function save(allowSimilar: boolean) {
    setSaving(true);
    setError(null);
    setWarning(null);
    if (initial) {
      try {
        await patchTag(initial.id, {
          name: name.trim(),
          description: description.trim(),
          color,
        });
        onDone();
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : "could not save");
      } finally {
        setSaving(false);
      }
      return;
    }
    const result = await createTag({
      name: name.trim(),
      color,
      description: description.trim(),
      allow_similar: allowSimilar,
    });
    setSaving(false);
    if (result.ok) {
      onDone();
    } else if (result.code === "duplicate") {
      setWarning({
        message: `A tag named "${result.existing?.name ?? name.trim()}" already exists.`,
        retryable: false,
      });
    } else if (result.code === "similar") {
      setWarning({
        message: `Similar tags exist: ${result.similar.map((s) => s.name).join(", ")}. Create anyway?`,
        retryable: true,
      });
    } else {
      setError(result.message);
    }
  }

  return (
    <form
      className="tag-editor"
      aria-label={initial ? `Edit tag ${initial.name}` : "New tag"}
      onSubmit={(e) => {
        e.preventDefault();
        void save(false);
      }}
    >
      <label>
        Name{" "}
        <input
          aria-label="Tag name"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <label>
        Description{" "}
        <input
          aria-label="Tag description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </label>
      <fieldset className="tag-swatches">
        <legend>Color</legend>
        <div role="radiogroup" aria-label="Tag color">
          {TAG_PALETTE_TOKENS.map((tok) => (
            <button
              key={tok}
              type="button"
              role="radio"
              aria-checked={color === tok}
              aria-label={`color ${tok}`}
              title={tok}
              className={`tag-swatch${color === tok ? " selected" : ""}`}
              style={{ backgroundColor: tagColor(tok).dot }}
              onClick={() => setColor(tok)}
            />
          ))}
        </div>
      </fieldset>
      {warning && (
        <p role="alert" className="status-error">
          {warning.message}{" "}
          {warning.retryable && (
            <button type="button" onClick={() => void save(true)}>
              Create anyway
            </button>
          )}
        </p>
      )}
      {error && (
        <p role="alert" className="status-error">
          {error}
        </p>
      )}
      <div className="tag-editor-actions">
        <button type="submit" disabled={saving || !name.trim()}>
          {saving ? "Saving…" : "Save"}
        </button>
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function DeleteConfirm({
  tag,
  onDone,
  onCancel,
}: {
  tag: TagOut;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [inUse, setInUse] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function del(force: boolean) {
    setError(null);
    const result = await deleteTag(tag.id, force);
    if (result.ok) {
      onDone();
    } else if (result.code === "in_use") {
      setInUse(result.document_count);
    } else {
      setError(result.message);
    }
  }

  return (
    <div className="tag-delete-confirm" role="alertdialog" aria-label={`Delete tag ${tag.name}`}>
      <p>
        Delete tag <strong>{tag.name}</strong>?
      </p>
      {inUse !== null && (
        <p role="alert" className="status-error">
          Used on {inUse} document{inUse === 1 ? "" : "s"} — they lose the tag.
        </p>
      )}
      {error && (
        <p role="alert" className="status-error">
          {error}
        </p>
      )}
      <div className="tag-editor-actions">
        {inUse === null ? (
          <button type="button" onClick={() => void del(false)}>
            Delete
          </button>
        ) : (
          <button type="button" onClick={() => void del(true)}>
            Delete anyway
          </button>
        )}
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}

function MergeDialog({
  tag,
  others,
  onDone,
  onCancel,
}: {
  tag: TagOut;
  others: TagOut[];
  onDone: () => void;
  onCancel: () => void;
}) {
  const [survivorId, setSurvivorId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Near-duplicates of the loser (token-set similarity) pinned to the top of the survivor list.
  const candidates = others
    .map((t) => ({ t, sim: tokenJaccard(tag.normalized, t.normalized) }))
    .sort((a, b) => b.sim - a.sim);

  async function confirm() {
    if (!survivorId || busy) return;
    setBusy(true);
    setError(null);
    try {
      await mergeTag(tag.id, survivorId);
      onDone();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "could not merge");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="tag-delete-confirm" role="dialog" aria-label={`Merge tag ${tag.name}`}>
      <p>
        Merge <strong>{tag.name}</strong> ({tag.document_count} doc
        {tag.document_count === 1 ? "" : "s"}) into:
      </p>
      <label>
        Surviving tag{" "}
        <select
          aria-label="Surviving tag"
          value={survivorId}
          onChange={(e) => setSurvivorId(e.target.value)}
        >
          <option value="">Pick the surviving tag…</option>
          {candidates.map(({ t, sim }) => (
            <option key={t.id} value={t.id}>
              {t.name}
              {sim >= 0.6 ? " (similar)" : ""}
            </option>
          ))}
        </select>
      </label>
      {error && (
        <p role="alert" className="status-error">
          {error}
        </p>
      )}
      <div className="tag-editor-actions">
        <button type="button" disabled={!survivorId || busy} onClick={() => void confirm()}>
          {busy ? "Merging…" : "Merge"}
        </button>
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}

/** The Tags management panel (Insights → Tags, #547): list, create/edit with the curated palette,
 * delete with the in-use confirm. */
export function TagsPanel() {
  const [tags, setTags] = useState<TagOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editor, setEditor] = useState<"create" | TagOut | null>(null);
  const [deleting, setDeleting] = useState<TagOut | null>(null);
  const [merging, setMerging] = useState<TagOut | null>(null);

  const load = useCallback(() => {
    fetchTags()
      .then(setTags)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "failed to load tags"));
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="tags-panel">
      <div className="tags-head">
        <h3>Tags</h3>
        <button type="button" onClick={() => setEditor("create")}>
          New tag
        </button>
      </div>
      <p className="muted">Manual labels to group documents. Tags are shared across the tenant.</p>
      {error && (
        <p role="alert" className="status-error">
          {error}
        </p>
      )}
      {tags === null ? (
        <p role="status">Loading tags…</p>
      ) : tags.length === 0 ? (
        <p className="empty">No tags yet — create the first one.</p>
      ) : (
        <ul className="tags-list">
          {tags.map((t) => (
            <li key={t.id} className="tags-row">
              <TagChip name={t.name} color={t.color} />
              {t.description && <span className="muted tags-desc">{t.description}</span>}
              <span className="muted tags-count">
                {t.document_count} doc{t.document_count === 1 ? "" : "s"}
              </span>
              <button type="button" className="link-button" onClick={() => setEditor(t)}>
                Edit
              </button>
              {tags.length > 1 && (
                <button type="button" className="link-button" onClick={() => setMerging(t)}>
                  Merge
                </button>
              )}
              <button type="button" className="link-button" onClick={() => setDeleting(t)}>
                Delete
              </button>
            </li>
          ))}
        </ul>
      )}
      {editor !== null && (
        <TagEditor
          initial={editor === "create" ? null : editor}
          onDone={() => {
            setEditor(null);
            load();
          }}
          onCancel={() => setEditor(null)}
        />
      )}
      {deleting !== null && (
        <DeleteConfirm
          tag={deleting}
          onDone={() => {
            setDeleting(null);
            load();
          }}
          onCancel={() => setDeleting(null)}
        />
      )}
      {merging !== null && (
        <MergeDialog
          tag={merging}
          others={(tags ?? []).filter((t) => t.id !== merging.id)}
          onDone={() => {
            setMerging(null);
            load();
          }}
          onCancel={() => setMerging(null)}
        />
      )}
    </div>
  );
}
