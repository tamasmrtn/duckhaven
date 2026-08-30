import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/utils";
import { useThrottledText } from "@/hooks/useThrottledText";
import { stabilizeStreamingMarkdown } from "./streamingMarkdown";

// A streamed answer is re-parsed in full on every token. Capping that at ~15fps
// keeps the panel from reflowing on each frame, and is well under the rate a
// reader notices.
const STREAM_FRAME_MS = 60;

// Styled renderers for assistant messages. Only GFM markdown is parsed — no raw
// HTML — so there's no injection surface. Tables scroll horizontally because the
// panel is narrow.
const COMPONENTS: Components = {
  p: ({ node: _n, ...props }) => (
    <p className="my-1.5 first:mt-0 last:mb-0" {...props} />
  ),
  ul: ({ node: _n, ...props }) => (
    <ul className="my-1.5 list-disc pl-5" {...props} />
  ),
  ol: ({ node: _n, ...props }) => (
    <ol className="my-1.5 list-decimal pl-5" {...props} />
  ),
  li: ({ node: _n, ...props }) => <li className="my-0.5" {...props} />,
  a: ({ node: _n, ...props }) => (
    <a
      className="text-[var(--brand-slate-blue)] underline"
      target="_blank"
      rel="noreferrer"
      {...props}
    />
  ),
  strong: ({ node: _n, ...props }) => (
    <strong className="font-semibold" {...props} />
  ),
  h1: ({ node: _n, ...props }) => (
    <h1 className="mb-1 mt-2 text-sm font-semibold" {...props} />
  ),
  h2: ({ node: _n, ...props }) => (
    <h2 className="mb-1 mt-2 text-sm font-semibold" {...props} />
  ),
  h3: ({ node: _n, ...props }) => (
    <h3 className="mb-1 mt-2 text-sm font-semibold" {...props} />
  ),
  blockquote: ({ node: _n, ...props }) => (
    <blockquote
      className="my-1.5 border-l-2 border-[var(--border-strong)] pl-2 text-text-secondary"
      {...props}
    />
  ),
  code: ({ node: _n, className, ...props }) => {
    const isBlock = /language-/.test(className ?? "");
    return isBlock ? (
      <code className={cn("font-mono text-[12px]", className)} {...props} />
    ) : (
      <code
        className="rounded bg-[var(--bg-elevated)] px-1 py-0.5 font-mono text-[12px]"
        {...props}
      />
    );
  },
  pre: ({ node: _n, ...props }) => (
    <pre
      className="my-1.5 overflow-x-auto rounded-md border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-2"
      {...props}
    />
  ),
  table: ({ node: _n, ...props }) => (
    <div className="my-1.5 overflow-x-auto">
      <table className="w-full border-collapse text-xs" {...props} />
    </div>
  ),
  th: ({ node: _n, ...props }) => (
    <th
      className="border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-2 py-1 text-left font-medium"
      {...props}
    />
  ),
  td: ({ node: _n, ...props }) => (
    <td className="border border-[var(--border-subtle)] px-2 py-1" {...props} />
  ),
  hr: ({ node: _n, ...props }) => (
    <hr className="my-2 border-[var(--border-subtle)]" {...props} />
  ),
};

/**
 * Render assistant markdown (GFM: tables, code, lists, …) as styled HTML.
 *
 * Pass `streaming` for a reply that is still arriving: its text is throttled and
 * rewritten so a half-finished construct renders as the shape it will settle
 * into. A persisted transcript renders its text verbatim.
 */
export function Markdown({
  children,
  streaming = false,
}: {
  children: string;
  streaming?: boolean;
}) {
  const throttled = useThrottledText(children, STREAM_FRAME_MS);
  const text = streaming ? stabilizeStreamingMarkdown(throttled) : children;
  return (
    <div className="text-sm leading-relaxed">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
