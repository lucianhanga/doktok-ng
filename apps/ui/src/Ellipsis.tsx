import type { ReactNode } from "react";

/**
 * One-line text that truncates with an ellipsis and exposes the full value in a native tooltip.
 *
 * This is the shared truncation primitive for single-line, variable-length values (names, titles,
 * filenames, ids, urls, paths, one-line details). It pairs the `.truncate` CSS utility with a
 * `title` so the complete text is reachable on hover. Do NOT use it for prose meant to wrap
 * (summaries, excerpts, help text).
 *
 * `text` is the full string used for both the visible label and the tooltip. Pass `children` when
 * the visible content is richer than the tooltip text (e.g. a value with a trailing badge); in that
 * case `text` is used only for the tooltip.
 *
 * Renders a <span> by default; pass `as` to render a different element (e.g. "td", "div").
 *
 * The truncating element must be able to shrink: inside a flex/grid parent, ensure the parent (and
 * the element's siblings) allow it to collapse (siblings `flex: 0 0 auto`). `.truncate` already
 * sets `min-width: 0` on the element itself.
 */
type EllipsisProps<E extends keyof JSX.IntrinsicElements> = {
  text: string;
  as?: E;
  className?: string;
  children?: ReactNode;
} & Omit<JSX.IntrinsicElements[E], "title" | "className" | "children">;

export function Ellipsis<E extends keyof JSX.IntrinsicElements = "span">({
  text,
  as,
  className,
  children,
  ...rest
}: EllipsisProps<E>) {
  const Tag = (as ?? "span") as keyof JSX.IntrinsicElements;
  const cls = className ? `truncate ${className}` : "truncate";
  return (
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    <Tag className={cls} title={text} {...(rest as any)}>
      {children ?? text}
    </Tag>
  );
}
