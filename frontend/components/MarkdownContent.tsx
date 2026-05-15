"use client";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Render AI responses as markdown. Bullet lists, code blocks, tables, links.
 * Constrained styling to fit our dark theme.
 */
export function MarkdownContent({ children }: { children: string }) {
  return (
    <div className="text-sm leading-relaxed prose-invert max-w-none">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: (props) => (
            <a
              {...props}
              target="_blank"
              rel="noreferrer"
              className="underline hover:no-underline text-team-primary"
            />
          ),
          ul: (props) => <ul {...props} className="list-disc pl-5 my-2 space-y-1" />,
          ol: (props) => <ol {...props} className="list-decimal pl-5 my-2 space-y-1" />,
          li: (props) => <li {...props} className="leading-relaxed" />,
          p: (props) => <p {...props} className="my-2" />,
          h1: (props) => <h1 {...props} className="text-lg font-semibold mt-3 mb-2" />,
          h2: (props) => <h2 {...props} className="text-base font-semibold mt-3 mb-2" />,
          h3: (props) => <h3 {...props} className="text-sm font-semibold mt-2 mb-1" />,
          code: ({ children, ...props }) => (
            <code {...props} className="px-1 py-0.5 rounded bg-bg/80 text-xs">
              {children}
            </code>
          ),
          pre: (props) => (
            <pre {...props} className="my-2 p-3 rounded bg-bg border divider overflow-x-auto text-xs" />
          ),
          table: (props) => (
            <div className="overflow-x-auto my-2">
              <table {...props} className="text-xs w-full" />
            </div>
          ),
          th: (props) => <th {...props} className="text-left text-muted py-1 pr-3 border-b divider" />,
          td: (props) => <td {...props} className="py-1 pr-3 border-b divider tabular-nums" />,
          strong: (props) => <strong {...props} className="font-semibold text-text" />,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
