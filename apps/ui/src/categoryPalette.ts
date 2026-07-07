/**
 * The 20-colour qualitative palette used by the backend to colour categories
 * (core/doktok_core/visualizations/map_service.py `_PALETTE`). Mirrored here
 * so the Categories bar chart and the Embedding Map assign the identical colour
 * to each category rank without a round-trip to the visualisation endpoint.
 *
 * Assignment rule (same as the server): categories are sorted by
 * document_count DESC then name ASC, and rank 0 gets colour 0, rank 1 gets
 * colour 1, and so on (wrapping at 20 for large vocabulary sets).
 */
export const CATEGORY_PALETTE: readonly string[] = [
  "#4e79a7",
  "#f28e2b",
  "#59a14f",
  "#e15759",
  "#76b7b2",
  "#edc948",
  "#b07aa1",
  "#ff9da7",
  "#9c755f",
  "#bab0ac",
  "#1f77b4",
  "#ff7f0e",
  "#2ca02c",
  "#d62728",
  "#9467bd",
  "#8c564b",
  "#e377c2",
  "#7f7f7f",
  "#bcbd22",
  "#17becf",
] as const;

/** Return the palette colour for a zero-based category rank. Wraps at 20. */
export function paletteColor(rank: number): string {
  return CATEGORY_PALETTE[rank % CATEGORY_PALETTE.length] as string;
}
