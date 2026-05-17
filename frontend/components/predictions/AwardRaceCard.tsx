"use client";
import useSWR from "swr";
import Link from "next/link";
import { api } from "@/lib/api";
import { Card } from "../Card";

/**
 * MVP + OPOY leaderboards derived from composite percentiles.
 * Rendered as two stacked mini-leaderboards.
 */
export function AwardRaceCard() {
  const { data, isLoading } = useSWR(
    ["awards"],
    () => api.awards(),
    { revalidateOnFocus: false },
  );

  if (isLoading || !data) {
    return (
      <Card title="Award races">
        <p className="text-sm text-muted">Computing composite scores…</p>
      </Card>
    );
  }

  return (
    <Card title="Award races" action={<span className="text-[11px] text-muted">{data.season} season</span>}>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Leaderboard title="MVP" rows={data.mvp.slice(0, 6)} />
        <Leaderboard title="Offensive POY" rows={data.opoy.slice(0, 6)} />
      </div>
      <p className="text-[10px] text-muted mt-3">
        Odds normalized via tempered softmax over composite percentile scores. Not betting advice.
      </p>
    </Card>
  );
}

function Leaderboard({ title, rows }: { title: string; rows: any[] }) {
  if (rows.length === 0) {
    return (
      <div>
        <h3 className="text-xs uppercase tracking-wide text-muted mb-2">{title}</h3>
        <p className="text-sm text-muted">No data yet.</p>
      </div>
    );
  }
  return (
    <div>
      <h3 className="text-xs uppercase tracking-wide text-muted mb-2">{title}</h3>
      <ol className="space-y-1 text-sm">
        {rows.map((r, i) => (
          <li key={r.player_id ?? i} className="flex items-center justify-between gap-2">
            <div className="min-w-0">
              <span className="text-muted tabular-nums w-5 inline-block">{i + 1}.</span>
              {r.player_id ? (
                <Link href={`/players/${r.player_id}`} className="hover:underline font-medium">
                  {r.name}
                </Link>
              ) : (
                <span>{r.name}</span>
              )}
              <span className="text-muted text-xs ml-2">{r.position} · {r.team ?? "—"}</span>
            </div>
            <span className="text-xs tabular-nums font-medium text-emerald-400">
              {r.odds_pct.toFixed(1)}%
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
