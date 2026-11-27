import { isValidElement, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/utils";
import { useThrottledText } from "@/hooks/useThrottledText";
import { CopyButton } from "./CopyButton";
import { stabilizeStreamingMarkdown } from "./streamingMarkdown";

// A streamed answer is re-parsed in full on every token. Capping that at ~15fps
// keeps the panel from reflowing on each frame, and is well under the rate a
// reader notices.
const STREAM_FRAME_MS = 60;

// Styled renderers for assistant messages. Only GFM markdown is parsed — no raw
// HTML. Images are the one construct that makes a request on its own: the model
// can be steered by text it reads (tool output, metadata) into writing an image
// whose URL carries data, which the browser would send on render. So an image
// renders as its alt text and never loads. Tables scroll horizontally because
// the panel is narrow.

/** The text inside rendered markdown, for what a copy button puts on the clipboard. */
function textOf(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (isValidElement<{ children?: ReactNode }>(node)) {
    return textOf(node.props.children);
  }
  return "";
}

/** Whether a link points back into this app. Only http(s) on this origin counts. */
function isOwnAddress(href: string | undefined): boolean {
  if (!href) return false;
  try {
    const url = new URL(href, window.location.href);
    return (
      (url.protocol === "http:" || url.protocol === "https:") &&
      url.origin === window.location.origin
    );
  } catch {
    return false;
  }
}

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
      className="text-[var(--link)] underline"
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
  pre: ({ node: _n, children, ...props }) => (
    <div className="relative my-1.5">
      <pre
        className="overflow-x-auto rounded-md border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-2 pr-8"
        {...props}
      >
        {children}
      </pre>
      <CopyButton
        text={textOf(children).replace(/\n$/, "")}
        label="Copy code"
        className="absolute right-1 top-1"
      />
    </div>
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
  img: ({ alt }) => (
    <span className="text-text-secondary">
      {alt ? `[image: ${alt}]` : "[image]"}
    </span>
  ),
};

// What the model writes can be steered by text it read, so a link it writes can
// carry data in its address: a click is the one step left to the reader. A link
// into this app opens as one. Any other address is shown in full as text, so the
// reader sees where it goes and chooses to copy it, rather than a label hiding it.
const NAMED_LINKS: Components = {
  ...COMPONENTS,
  a: ({ node: _n, href, children, ...props }) => {
    if (isOwnAddress(href)) {
      return (
        <a
          className="text-[var(--link)] underline"
          target="_blank"
          rel="noreferrer"
          href={href}
          {...props}
        >
          {children}
        </a>
      );
    }
    // An address written out as its own label would show twice.
    const labelIsAddress = typeof children === "string" && children === href;
    return (
      <span>
        {children}
        {href && !labelIsAddress && (
          <span className="break-all text-text-secondary"> ({href})</span>
        )}
      </span>
    );
  },
};

/**
 * Render markdown (GFM: tables, code, lists, …) as styled HTML.
 *
 * Pass `streaming` for a reply that is still arriving: its text is throttled and
 * rewritten so a half-finished construct renders as the shape it will settle
 * into. A persisted transcript renders its text verbatim.
 *
 * `links="named"` is for text a model wrote: only links into this app are
 * clickable. The default opens every link, for text a person wrote.
 */
export function Markdown({
  children,
  streaming = false,
  links = "open",
}: {
  children: string;
  streaming?: boolean;
  links?: "open" | "named";
}) {
  const throttled = useThrottledText(children, STREAM_FRAME_MS);
  const text = streaming ? stabilizeStreamingMarkdown(throttled) : children;
  return (
    <div className="text-sm leading-relaxed">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={links === "named" ? NAMED_LINKS : COMPONENTS}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
