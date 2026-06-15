import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

/** Turn literal `[n]` citation markers (for n that has a real citation) into in-text links to
 * `#cite-n`, so the `a` renderer below can make them clickable. Brackets in the link text are
 * escaped so the marker still renders as "[n]". Other bracketed numbers are left untouched. */
function linkifyCitations(text: string, indices: Set<number>): string {
  return text.replace(/\[(\d+)\]/g, (whole, num: string) =>
    indices.has(Number(num)) ? `[\\[${num}\\]](#cite-${num})` : whole,
  );
}

/**
 * Renders model output (answer + reasoning) as Markdown. react-markdown does NOT render raw HTML by
 * default (no rehype-raw), so LLM output cannot inject markup - safe for untrusted content.
 * remark-gfm adds tables, task lists, strikethrough and autolinks. Wrapped in a `.markdown` div so
 * chat CSS can tame block spacing.
 *
 * When ``onCitationClick`` + ``citationIndices`` are given, literal [n] markers in the text become
 * clickable references (M8 #9): clicking one selects that source's document.
 */
export function Markdown({
  children,
  citationIndices,
  onCitationClick,
}: {
  children: string;
  citationIndices?: Set<number>;
  onCitationClick?: (index: number) => void;
}) {
  const clickable = !!onCitationClick && !!citationIndices && citationIndices.size > 0;
  const text = clickable ? linkifyCitations(children, citationIndices) : children;
  const components: Components | undefined = clickable
    ? {
        a({ href, children: inner, ...props }) {
          const m = typeof href === "string" ? href.match(/^#cite-(\d+)$/) : null;
          if (m) {
            const index = Number(m[1]);
            return (
              <button
                type="button"
                className="citation-ref"
                onClick={() => onCitationClick?.(index)}
                title={`Open source [${index}]`}
              >
                {inner}
              </button>
            );
          }
          return (
            <a href={href} {...props}>
              {inner}
            </a>
          );
        },
      }
    : undefined;
  return (
    <div className="markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
