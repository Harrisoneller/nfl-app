"use client";
import { useMemo, useState } from "react";
import useSWR from "swr";
import { Card } from "./Card";

type FeedItem = {
  id: string;
  source: string;          // 'rss' | 'reddit' | 'twitter'
  source_label: string;
  title: string;
  summary?: string;
  link: string;
  author: string;
  image_url?: string;
  published_at: string | null;
};

/**
 * Auto-refreshing news/social feed. Source chips let the user filter to
 * just RSS, just Reddit, etc. Refreshes every 60s by default.
 */
export function LiveFeed({
  title,
  fetcher,
  cacheKey,
  emptyText = "No items yet — the scheduler refreshes news every 5 min.",
  refreshMs = 60_000,
}: {
  title: string;
  fetcher: () => Promise<FeedItem[]>;
  cacheKey: any[];
  emptyText?: string;
  refreshMs?: number;
}) {
  const [filter, setFilter] = useState<string>("all");
  const { data, isLoading } = useSWR(cacheKey, fetcher, {
    refreshInterval: refreshMs,
    revalidateOnFocus: false,
  });

  const items = data ?? [];
  const sources = useMemo(() => {
    const s = new Set<string>(items.map((i) => i.source));
    return ["all", ...Array.from(s)];
  }, [items]);

  const filtered = filter === "all" ? items : items.filter((i) => i.source === filter);

  return (
    <Card
      title={title}
      action={
        <div className="flex items-center gap-2 text-xs">
          {sources.map((s) => (
            <button
              key={s}
              onClick={() => setFilter(s)}
              className={`px-2 py-0.5 rounded border divider transition-colors ${
                filter === s ? "bg-team-primary text-white" : "text-muted hover:text-text"
              }`}
            >
              {s}
            </button>
          ))}
          <span className="text-muted" title="auto-refresh every 60s">⟳ live</span>
        </div>
      }
    >
      {isLoading && items.length === 0 && (
        <p className="text-sm text-muted">Loading…</p>
      )}
      {!isLoading && filtered.length === 0 && (
        <p className="text-sm text-muted">{emptyText}</p>
      )}
      <ul className="space-y-2.5">
        {filtered.map((i) => (
          <li key={i.id} className="flex items-start gap-3 text-sm">
            <span className="text-muted text-xs whitespace-nowrap mt-0.5 w-28">
              [{i.source_label}]
            </span>
            <div className="flex-1 min-w-0">
              <a
                href={i.link}
                target="_blank"
                rel="noreferrer"
                className="hover:underline block truncate"
              >
                {i.title}
              </a>
              {i.published_at && (
                <div className="text-xs text-muted mt-0.5">
                  {relativeTime(i.published_at)}
                </div>
              )}
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function relativeTime(iso: string): string {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return iso;
  const diff = Date.now() - t;
  const mins = Math.round(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}
