import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Renders model output (answer + reasoning) as Markdown. react-markdown does NOT render raw HTML by
 * default (no rehype-raw), so LLM output cannot inject markup - safe for untrusted content.
 * remark-gfm adds tables, task lists, strikethrough and autolinks. Wrapped in a `.markdown` div so
 * chat CSS can tame block spacing. Citation markers like [1] are left literal (undefined reference
 * links render as plain text per CommonMark).
 */
export function Markdown({ children }: { children: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  );
}
