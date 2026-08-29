"use client";

import useSWR from "swr";
import Link from "next/link";
import { useCallback } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { api, type WeekSlateGame, type WeekSlateResponse } from "@/lib/api";
import { spreadEdgeSide } from "@/lib/edge";
import { Card } from "@/components/Card";
import { TeamLogo } from "@/components/TeamLogo";
import { WinProbBar } from "@/components/predictions/WinProbBar";

/**
 * Weekly slate — model projections vs market for every REG game this week.
 *
 * SSR passes `fallbackData` so the board paints even if the browser cannot
 * reach Railway directly. Client SWR still refreshes. Week defaults to the
 * next regular-season week with unplayed games.
 */
export function WeekSlateView({
  fallbackData,
}: {
  fallbackData?: WeekSlateResponse | null;
}) {
  const search = useSearchParams();
  const router = useRouter();
  const pathname = usePathname() || "/week";
  const weekParam = search.get("week");
  const selectedWeek = weekParam ? Number(weekParam) : undefined;
  const weekKey = selectedWeek && Number.isFinite(selectedWeek) ? selectedWeek : undefined;

  const { data, isLoading, error } = useSWR(
    ["week-slate", weekKey ?? "next"],
    () => api.weekSlate(undefined, weekKey),
    {
      fallbackData: weekKey == null ? fallbackData ?? undefined : undefined,
      revalidateOnFocus: false,
      refreshInterval: 5 * 60_000,
    },
  );

  const setWeek = useCallback(
    (w: number) => {
      const params = new URLSearchParams(search.toString());
      params.set("week", String(w));
      router.replace(`${pathname}?${params.toString()}`, { scroll: false });
    },
    [pathname, router, search],
  );

  const activeWeek = weekKey ?? data?.week ?? null;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Week slate</h1>
        <p className="mt-1 text-sm text-muted max-w-2xl">
          Production forecast uses the market as a prior and our ratings as a{" "}
          <span className="text-text font-medium">bounded residual</span>.{" "}
          <span className="text-team-primary font-medium">Model</span> = that
          forecast;{" "}
          <span className="text-amber-700 dark:text-amber-300 font-medium">Market</span>{" "}
          = sportsbooks;{" "}
          <span className="text-emerald-700 dark:text-emerald-300 font-medium">Edge</span>{" "}
          = model − market. Defaults to the next regular-season week.
        </p>
      </div>

      {data?.weeks && data.weeks.length > 0 && (
        <div className="flex gap-1.5 overflow-x-auto pb-1">
          {data.weeks.map((w) => {
            const on = activeWeek === w.week;
            return (
              <button
                key={w.week}
                type="button"
                onClick={() => setWeek(w.week)}
                className={`shrink-0 rounded-full border px-3 py-1 text-xs tabular-nums transition-colors ${
                  on
                    ? "border-team-primary bg-team-primary/15 text-team-primary font-semibold"
                    : "divider text-muted hover:text-text"
                }`}
              >
                Wk {w.week}
                <span className="ml-1 opacity-70">{w.games}</span>
              </button>
            );
          })}
        </div>
      )}

      {isLoading && !data && (
        <Card>
          <p className="text-sm text-muted">Loading slate…</p>
        </Card>
      )}
      {error && (
        <Card>
          <p className="text-sm text-red-500">
            Could not load the week slate. Predictions may still be warming.
          </p>
        </Card>
      )}
      {data && (
        <>
          <div className="flex flex-wrap items-center gap-3 text-sm text-muted">
            <span className="font-medium text-text">
              Season {data.season}
              {data.week != null ? ` · Week ${data.week}` : ""}
            </span>
            <span>{data.n_games} games</span>
            <Legend />
            <span className="text-[11px] opacity-70 ml-auto">{data.model_version}</span>
          </div>

          {data.partial && data.games.length === 0 ? (
            <Card>
              <p className="text-sm text-muted">
                The slate is still computing. Refresh in a moment.
              </p>
            </Card>
          ) : data.games.length === 0 ? (
            <Card>
              <p className="text-sm text-muted">
                No games on this week yet. Check back when the schedule posts.
              </p>
            </Card>
          ) : (
            <div className="space-y-3">
              {data.games.map((g, i) => (
                <GameRow
                  key={g.id || `${g.home_team_id}-${g.away_team_id}-${i}`}
                  g={g}
                />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function Legend() {
  return (
    <div className="flex flex-wrap items-center gap-2 text-[10px]">
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-team-primary/15 text-team-primary border border-team-primary/20">
        Model
      </span>
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-700 dark:text-amber-300 border border-amber-500/20">
        Market
      </span>
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border border-emerald-500/20">
        Edge
      </span>
    </div>
  );
}

function formatSpread(spread: number | null | undefined, homeId: string, awayId: string): string {
  if (spread == null || Number.isNaN(spread)) return "—";
  if (spread === 0) return "PK";
  if (spread < 0) return `${homeId} ${spread.toFixed(1)}`;
  return `${awayId} ${(-spread).toFixed(1)}`;
}

function GameRow({ g }: { g: WeekSlateGame }) {
  const homeId = g.home_team_id ?? "HOME";
  const awayId = g.away_team_id ?? "AWAY";
  const played = g.home_score != null && g.away_score != null;

  const modelSpread = g.model_spread ?? g.predicted_spread ?? null;
  const modelTotal = g.model_total ?? g.predicted_total ?? null;
  const modelHomePts = g.model_home_score ?? g.predicted_home_score ?? null;
  const modelAwayPts = g.model_away_score ?? g.predicted_away_score ?? null;
  const modelHomeWp = g.model_home_win_prob ?? g.home_win_prob ?? 0.5;
  const rawSpread = g.model_raw_spread ?? g.model_only?.predicted_spread ?? null;

  const marketSpread =
    g.market_spread ??
    (typeof g.market?.spread_home === "number" ? g.market.spread_home : null);
  const marketTotal =
    g.market_total ?? (typeof g.market?.total === "number" ? g.market.total : null);

  const dist = g.distribution;
  const canH2h = Boolean(g.home_team_id && g.away_team_id);

  return (
    <Card className="!p-4">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-2 flex-wrap">
            {g.confidence_tier && (
              <span className="text-[10px] text-muted capitalize">
                {g.confidence_tier} confidence
              </span>
            )}
            <span
              className="text-[10px] px-1.5 py-0.5 rounded bg-team-primary/15 text-team-primary border border-team-primary/20"
              title="Scores and win % are the production (market-anchored) model."
            >
              model scores
            </span>
          </div>

          <div className="flex items-center justify-between gap-2 mb-1">
            <div className="flex items-center gap-2 min-w-0">
              <TeamLogo teamId={awayId} size={28} />
              <TeamLink id={g.away_team_id} />
            </div>
            <span className="text-xs tabular-nums shrink-0 font-medium text-team-primary">
              {played
                ? g.away_score
                : modelAwayPts != null
                  ? modelAwayPts.toFixed(1)
                  : "—"}
            </span>
          </div>
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2 min-w-0">
              <TeamLogo teamId={homeId} size={28} />
              <TeamLink id={g.home_team_id} />
            </div>
            <span className="text-xs tabular-nums shrink-0 font-medium text-team-primary">
              {played
                ? g.home_score
                : modelHomePts != null
                  ? modelHomePts.toFixed(1)
                  : "—"}
            </span>
          </div>
          <div className="mt-2">
            <WinProbBar
              homeTeam={homeId}
              awayTeam={awayId}
              homeProb={modelHomeWp ?? 0.5}
              awayProb={1 - (modelHomeWp ?? 0.5)}
            />
            <div className="text-[9px] text-muted mt-0.5">
              Win % & scores = production model (market-anchored residual)
              {modelSpread != null &&
                modelHomePts != null &&
                modelAwayPts != null && (
                  <span className="tabular-nums">
                    {" "}
                    · margin {(modelHomePts - modelAwayPts).toFixed(1)}
                  </span>
                )}
              {rawSpread != null &&
                modelSpread != null &&
                Math.abs(rawSpread - modelSpread) >= 3 && (
                  <span className="tabular-nums opacity-80">
                    {" "}
                    · raw power {formatSpread(rawSpread, homeId, awayId)} before
                    market anchor
                  </span>
                )}
            </div>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-2 md:w-80 shrink-0 text-center">
          <Mini
            tone="model"
            label="Model spread"
            value={formatSpread(modelSpread, homeId, awayId)}
            hint="our projection"
          />
          <Mini
            tone="market"
            label="Market spread"
            value={formatSpread(marketSpread ?? undefined, homeId, awayId)}
            hint="Vegas / books"
          />
          <Mini
            tone="edge"
            label="Spread edge"
            value={
              g.spread_edge != null
                ? (() => {
                    const side = spreadEdgeSide(g.spread_edge, homeId, awayId);
                    return side
                      ? `${side} +${Math.abs(Number(g.spread_edge)).toFixed(1)}`
                      : "PK";
                  })()
                : "—"
            }
            hint={g.spread_edge != null ? "pts of value vs market" : "no market line"}
          />
          <Mini
            tone="model"
            label="Model total"
            value={modelTotal != null ? modelTotal.toFixed(1) : "—"}
            hint="our O/U"
          />
          <Mini
            tone="market"
            label="Market total"
            value={marketTotal != null ? Number(marketTotal).toFixed(1) : "—"}
            hint="Vegas O/U"
          />
          <Mini
            tone="edge"
            label="Total edge"
            value={
              g.total_edge != null
                ? `${g.total_edge > 0 ? "+" : ""}${Number(g.total_edge).toFixed(1)}`
                : "—"
            }
            hint={g.total_edge != null ? "model − market" : "no market line"}
          />
        </div>

        <div className="grid grid-cols-2 gap-2 md:w-36 shrink-0 text-center">
          <Mini
            label="Elo Δ"
            value={
              g.elo_gap != null
                ? `${g.elo_gap > 0 ? "+" : ""}${g.elo_gap.toFixed(0)}`
                : "—"
            }
            hint="home − away"
          />
          <Mini label="Script" value={g.game_script || "—"} />
          <Mini
            label="80% margin"
            value={
              dist?.margin_interval_80
                ? `${dist.margin_interval_80[0].toFixed(0)}–${dist.margin_interval_80[1].toFixed(0)}`
                : "—"
            }
            hint="model range"
          />
          <div className="flex flex-col gap-1 justify-center">
            {canH2h ? (
              <Link
                href={`/h2h/${g.away_team_id}/${g.home_team_id}`}
                className="text-xs px-2 py-1.5 rounded border divider hover:bg-bg text-center"
              >
                H2H
              </Link>
            ) : (
              <span className="text-xs px-2 py-1.5 rounded border divider text-muted text-center opacity-50">
                H2H
              </span>
            )}
            <Link
              href="/sparky"
              className="text-xs px-2 py-1.5 rounded border divider hover:bg-bg text-center"
            >
              Sparky
            </Link>
          </div>
        </div>
      </div>
      {(g.gameday || g.gametime) && (
        <div className="mt-2 text-[11px] text-muted">
          {[g.gameday, g.gametime].filter(Boolean).join(" · ")}
        </div>
      )}
    </Card>
  );
}

function TeamLink({ id }: { id: string | null | undefined }) {
  const label = (id && String(id).trim()) || "TBD";
  if (!id) return <span className="font-semibold truncate text-muted">{label}</span>;
  return (
    <Link href={`/teams/${id}`} className="font-semibold truncate hover:underline">
      {label}
    </Link>
  );
}

function Mini({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "model" | "market" | "edge";
}) {
  const toneCls =
    tone === "model"
      ? "border-team-primary/25 bg-team-primary/5"
      : tone === "market"
        ? "border-amber-500/25 bg-amber-500/5"
        : tone === "edge"
          ? "border-emerald-500/25 bg-emerald-500/5"
          : "panel";
  const labelCls =
    tone === "model"
      ? "text-team-primary"
      : tone === "market"
        ? "text-amber-700 dark:text-amber-300"
        : tone === "edge"
          ? "text-emerald-700 dark:text-emerald-300"
          : "text-muted";

  return (
    <div className={`px-2 py-1.5 rounded border divider ${toneCls}`}>
      <div className={`text-[10px] uppercase tracking-wide ${labelCls}`}>{label}</div>
      <div className="text-sm font-semibold tabular-nums truncate" title={value}>
        {value}
      </div>
      {hint && <div className="text-[9px] text-muted">{hint}</div>}
    </div>
  );
}
