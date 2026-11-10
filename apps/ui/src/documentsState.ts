import type { DocumentSort, SortDir, TokenMatch } from "./api";

export type DocumentOrder =
  | "newest"
  | "oldest"
  | "title-asc"
  | "title-desc"
  | "document-date-desc"
  | "document-date-asc"
  | "category-asc"
  | "category-desc";

export interface DocumentsUiPrefs {
  version: 2;
  view: "list" | "thumbnails";
  query: string;
  statuses: string[];
  categories: string[];
  needsAttention: boolean;
  unidentifiable: boolean;
  tags: string[];
  tagMatch: TokenMatch;
  sort: DocumentSort;
  dir: SortDir;
  title?: string;
  tokens?: string[];
  tokenMatch?: TokenMatch;
}

const DOCUMENT_ORDERS: Record<DocumentOrder, { sort: DocumentSort; dir: SortDir }> = {
  newest: { sort: "acquired", dir: "desc" },
  oldest: { sort: "acquired", dir: "asc" },
  "title-asc": { sort: "title", dir: "asc" },
  "title-desc": { sort: "title", dir: "desc" },
  "document-date-desc": { sort: "created", dir: "desc" },
  "document-date-asc": { sort: "created", dir: "asc" },
  "category-asc": { sort: "category", dir: "asc" },
  "category-desc": { sort: "category", dir: "desc" },
};

const STATE_PARAMS = [
  "q",
  "status",
  "category",
  "tag",
  "tag_match",
  "attention",
  "unidentifiable",
  "view",
  "order",
  "title",
  "token",
  "token_match",
] as const;

const DOCUMENT_STATUSES = new Set(["active", "failed", "quarantined", "duplicate"]);
const DOCUMENT_SORTS = new Set<DocumentSort>(["acquired", "created", "title", "category"]);

export function defaultDocumentsUiPrefs(): DocumentsUiPrefs {
  return {
    version: 2,
    view: "list",
    query: "",
    statuses: [],
    categories: [],
    needsAttention: false,
    unidentifiable: false,
    tags: [],
    tagMatch: "all",
    sort: "acquired",
    dir: "desc",
  };
}

function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? [...new Set(value.filter((item): item is string => typeof item === "string" && item.length > 0))]
    : [];
}

function match(value: unknown): TokenMatch {
  return value === "any" ? "any" : "all";
}

function orderFor(sort: DocumentSort, dir: SortDir): DocumentOrder {
  for (const [order, value] of Object.entries(DOCUMENT_ORDERS) as [
    DocumentOrder,
    { sort: DocumentSort; dir: SortDir },
  ][]) {
    if (value.sort === sort && value.dir === dir) return order;
  }
  return "newest";
}

export function orderToSort(order: DocumentOrder): { sort: DocumentSort; dir: SortDir } {
  return { ...DOCUMENT_ORDERS[order] };
}

export function migrateDocumentsUiPrefs(raw: unknown): DocumentsUiPrefs {
  const defaults = defaultDocumentsUiPrefs();
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return defaults;
  const value = raw as Record<string, unknown>;
  const statuses = Array.isArray(value.statuses)
    ? strings(value.statuses).filter((status) => DOCUMENT_STATUSES.has(status))
    : typeof value.status === "string" && DOCUMENT_STATUSES.has(value.status)
      ? [value.status]
      : [];
  const categories = Array.isArray(value.categories)
    ? strings(value.categories)
    : typeof value.category === "string" && value.category
      ? [value.category]
      : [];
  const tokens = strings(value.tokens);
  const prefs: DocumentsUiPrefs = {
    version: 2,
    view: value.view === "thumbnails" || value.view === "list" ? value.view : defaults.view,
    query:
      typeof value.query === "string"
        ? value.query
        : typeof value.title === "string"
          ? value.title
          : defaults.query,
    statuses,
    categories,
    needsAttention:
      typeof value.needsAttention === "boolean" ? value.needsAttention : defaults.needsAttention,
    unidentifiable:
      typeof value.unidentifiable === "boolean" ? value.unidentifiable : defaults.unidentifiable,
    tags: strings(value.tags),
    tagMatch: match(value.tagMatch),
    sort:
      typeof value.sort === "string" && DOCUMENT_SORTS.has(value.sort as DocumentSort)
        ? (value.sort as DocumentSort)
        : defaults.sort,
    dir: value.dir === "asc" || value.dir === "desc" ? value.dir : defaults.dir,
  };
  if (tokens.length > 0) {
    prefs.tokens = tokens;
    prefs.tokenMatch = match(value.tokenMatch);
  }
  return prefs;
}

export function parseDocumentsHash(
  hash: string,
  stored: DocumentsUiPrefs,
): { prefs: DocumentsUiPrefs; explicit: boolean } {
  const queryStart = hash.indexOf("?");
  const params = new URLSearchParams(queryStart >= 0 ? hash.slice(queryStart + 1) : "");
  const explicit = STATE_PARAMS.some((key) => params.has(key));
  if (!explicit) return { prefs: migrateDocumentsUiPrefs(stored), explicit: false };

  const defaults = defaultDocumentsUiPrefs();
  const order = params.get("order") as DocumentOrder | null;
  const sorted =
    order && Object.prototype.hasOwnProperty.call(DOCUMENT_ORDERS, order)
      ? orderToSort(order)
      : defaults;
  const statuses = strings(params.getAll("status")).filter((status) =>
    DOCUMENT_STATUSES.has(status),
  );
  const tokens = strings(params.getAll("token"));
  const prefs: DocumentsUiPrefs = {
    version: 2,
    view: params.get("view") === "thumbnails" || params.get("view") === "list"
      ? (params.get("view") as "list" | "thumbnails")
      : defaults.view,
    query: params.has("q") ? (params.get("q") ?? "") : (params.get("title") ?? ""),
    statuses,
    categories: strings(params.getAll("category")),
    needsAttention: params.get("attention") === "1",
    unidentifiable: params.get("unidentifiable") === "1",
    tags: strings(params.getAll("tag")),
    tagMatch: match(params.get("tag_match")),
    sort: sorted.sort,
    dir: sorted.dir,
  };
  if (tokens.length > 0) {
    prefs.tokens = tokens;
    prefs.tokenMatch = match(params.get("token_match"));
  }
  return { prefs, explicit: true };
}

export function serializeDocumentsHash(prefs: DocumentsUiPrefs): string {
  const migrated = migrateDocumentsUiPrefs(prefs);
  const params = new URLSearchParams();
  if (migrated.query) params.set("q", migrated.query);
  for (const status of migrated.statuses) params.append("status", status);
  for (const category of migrated.categories) params.append("category", category);
  for (const tag of migrated.tags) params.append("tag", tag);
  params.set("tag_match", migrated.tagMatch);
  if (migrated.needsAttention) params.set("attention", "1");
  if (migrated.unidentifiable) params.set("unidentifiable", "1");
  params.set("view", migrated.view);
  params.set("order", orderFor(migrated.sort, migrated.dir));
  for (const token of migrated.tokens ?? []) params.append("token", token);
  if (migrated.tokens?.length) params.set("token_match", migrated.tokenMatch ?? "all");
  return `#/documents?${params.toString()}`;
}
