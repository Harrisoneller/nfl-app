"use client";

import { Card } from "@/components/Card";
import { formatSigned, TeamLuck } from "@/lib/luck";

const SCALE_SPAN = 2.5;

function markerPercent(score: number | null): number {
  if (score == null) return 50;
  const clamped = Math.max(-SCALE_SPAN, Math.min(SCALE_SPAN, score));
  return ((clamped + SCALE_SPAN) / (SCALE_SPAN * 2)) * 100;
}

function LuckScale({ score, negative, positive }: { score: number | null; negative: string; positive: string }) {
  const pct = markerPercent(score);
  const label = score == null ? "No luck index" : `${formatSigned(score, 2)} on the scale from ${negative} to ${positive}`;
  return (
    <div>
      <div className="mb-1 flex justify-between text-xs text-muted">
        <span>{negative}</span>
        <span>{positive}</span>
      </div>
      <div className="relative h-2 rounded-full bg-white/10" role="img" aria-label={label}>
        <div
          className="absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border border-black/40 bg-white"
          style={{ left: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export function LuckIndex({
  luck,
  teamId,
  loading,
  error,
}: {
  luck: TeamLuck | undefined;
  teamId: string;
  loading: boolean;
  error: boolean;
}) {
  if (loading) {
    return <Card title="Regression candidate"><p className="text-sm text-muted">Loading the luck index…</p></Card>;
  }
  if (error || !luck) {
    return <Card title="Regression candidate"><p className="text-sm text-muted">The luck index is unavailable.</p></Card>;
  }
  const row = luck.team;
  const waiting = luck.unavailable?.[0];
  return (
    <div className="space-y-5">
      <Card
        title="Regression candidate"
        action={<span className="text-xs text-muted">{luck.season}</span>}
      >
        <p className="text-sm font-medium">This is not a pick.</p>
        <p className="mt-1 text-sm text-muted">{luck.disclaimer}</p>
        {waiting ? (
          <p className="mt-3 text-sm text-amber-200/90">{waiting.reason}</p>
        ) : row ? (
          <div className="mt-4 space-y-3">
            <div className="flex items-baseline justify-between gap-3">
              <p className="text-2xl font-semibold tabular-nums">{formatSigned(row.score, 2)}</p>
              <p className="text-sm text-muted">
                {row.side} · {row.rank} of {row.league_size}
              </p>
            </div>
            <LuckScale score={row.score} negative={luck.scale.negative} positive={luck.scale.positive} />
            <p className="text-xs text-muted">{luck.method}</p>
          </div>
        ) : (
          <p className="mt-3 text-sm text-muted">{luck.error || `No luck row for ${teamId}.`}</p>
        )}
      </Card>

      {row && row.components.length > 0 && (
        <Card title="What went into it">
          <ul className="space-y-3">
            {row.components.map((component) => (
              <li key={component.id} className="border-b border-white/5 pb-3 last:border-0 last:pb-0">
                <div className="flex items-baseline justify-between gap-3">
                  <p className="text-sm font-medium">{component.label}</p>
                  <p className="text-sm tabular-nums">
                    {formatSigned(component.residual, 3)}
                    <span className="ml-2 text-xs text-muted">z {formatSigned(component.z, 2)}</span>
                  </p>
                </div>
                <p className="mt-1 text-xs text-muted">{component.note}</p>
                <p className="mt-1 text-xs text-muted">
                  {component.samples} samples, already shrunk toward the baseline
                  {component.shrink_weight != null ? ` (weight ${component.shrink_weight.toFixed(2)})` : ""}.
                </p>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {luck.omitted.length > 0 && (
        <Card title="Left out">
          <ul className="space-y-2">
            {luck.omitted.map((item) => (
              <li key={item.id} className="text-sm text-muted">{item.reason}</li>
            ))}
          </ul>
        </Card>
      )}

      <Card title="League table">
        <p className="mb-3 text-xs text-muted">Luckiest first. The same regression candidate, not a pick.</p>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <caption className="sr-only">Teams sorted from luckiest to unluckiest</caption>
            <thead className="text-xs text-muted">
              <tr>
                <th className="py-2 pr-3 font-medium">Rank</th>
                <th className="py-2 pr-3 font-medium">Team</th>
                <th className="py-2 pr-3 font-medium">Index</th>
                <th className="py-2 pr-3 font-medium">Side</th>
              </tr>
            </thead>
            <tbody>
              {luck.table.map((item) => (
                <tr
                  key={item.team}
                  className={item.team === teamId ? "bg-white/10" : undefined}
                >
                  <td className="py-1.5 pr-3 tabular-nums">{item.rank ?? "—"}</td>
                  <td className="py-1.5 pr-3 font-medium">{item.team}</td>
                  <td className="py-1.5 pr-3 tabular-nums">{formatSigned(item.score, 2)}</td>
                  <td className="py-1.5 pr-3">{item.side ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
