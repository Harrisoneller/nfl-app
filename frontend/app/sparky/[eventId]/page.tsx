"use client";
import Link from "next/link";
import useSWR from "swr";
import { api, SparkyGameDetail } from "@/lib/api";
import { TeamLogo } from "@/components/TeamLogo";
import { ConfidenceRing } from "@/components/sparky/ConfidenceRing";
import { SignalPill } from "@/components/sparky/SignalPill";
import { MovementChart } from "@/components/sparky/MovementChart";
import { americanOdds, classificationLabel, kickoff, pct, pctPoints } from "@/components/sparky/format";

export default function SparkyGameDetailPage({
  params,
}: {
  params: { eventId: string };
}) {
  const eventId = params.eventId;
  const { data, isLoading, error } = useSWR(["sparky-game", eventId], () => api.sparkyGame(eventId));

  return (
    <div className="sparky-scope space-y-5">
      <Link href="/sparky" className="text-xs text-cyan-300 hover:underline">
        ← Back to Sparky
      </Link>

      {isLoading && <div className="sparky-card p-6 text-sm text-muted">Loading game…</div>}
      {error && <div className="sparky-card p-6 text-sm text-red-400">Couldn&apos;t load this game.</div>}

      {data && (
        <>
          <Header detail={data} />

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="sparky-card p-4">
              <h3 className="text-sm font-semibold text-white mb-2">Line movement</h3>
              <MovementChart
                movement={data.movement}
                homeId={data.prediction?.home_team_id}
                awayId={data.prediction?.away_team_id}
              />
            </div>

            <div className="sparky-card p-4">
              <h3 className="text-sm font-semibold text-white mb-3">Signal breakdown</h3>
              {data.prediction?.signals?.length ? (
                <div className="space-y-2.5">
                  {data.prediction.signals.map((s) => (
                    <div key={s.key} className="flex items-start gap-2">
                      <SignalPill signal={s} />
                      <span className="text-xs text-slate-300/80 leading-relaxed">{s.explanation}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-muted">No standout market signals on this game.</p>
              )}
            </div>
          </div>

          <BooksTable detail={data} />
        </>
      )}
    </div>
  );
}

function Header({ detail }: { detail: SparkyGameDetail }) {
  const p = detail.prediction;
  if (!p) {
    return (
      <div className="sparky-card p-5 text-sm text-muted">
        Odds captured for this game, but no prediction has been built yet.
      </div>
    );
  }
  const homePicked = p.predicted_winner === p.home_team_id;
  return (
    <div className="sparky-hero">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <div className="flex items-center gap-2 text-[11px] text-muted">
            <span className={`sparky-chip sparky-chip--${p.classification ?? "lean"}`}>
              {classificationLabel(p.classification)}
            </span>
            <span>{kickoff(p.commence_time)}</span>
          </div>
          <div className="mt-2 flex items-center gap-3">
            <TeamSide id={p.away_team_id} ml={p.market?.away_ml ?? null} picked={!homePicked} />
            <span className="text-muted text-sm">@</span>
            <TeamSide id={p.home_team_id} ml={p.market?.home_ml ?? null} picked={homePicked} />
          </div>
        </div>
        <div className="flex flex-col items-center">
          <ConfidenceRing score={p.confidence_score} size={76} />
          <div className="text-[10px] text-muted mt-1">confidence</div>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Mini label="Pick" value={p.predicted_winner ?? "—"} accent />
        <Mini label="Win prob" value={pct(p.win_prob)} />
        <Mini label="Model" value={p.model_prob != null ? pct(p.model_prob) : "—"} />
        <Mini label="Market" value={p.market_prob != null ? pct(p.market_prob) : "—"} />
      </div>

      {p.explanation && <p className="mt-3 text-sm text-slate-200/85 leading-relaxed">{p.explanation}</p>}
    </div>
  );
}

function TeamSide({ id, ml, picked }: { id: string | null; ml: number | null; picked: boolean }) {
  return (
    <div className={`flex items-center gap-2 ${picked ? "" : "opacity-75"}`}>
      {id ? <TeamLogo teamId={id} size={36} /> : null}
      <div>
        <div className={`text-lg font-bold ${picked ? "text-white" : "text-slate-300"}`}>{id ?? "—"}</div>
        <div className="text-[11px] text-muted tabular-nums">{americanOdds(ml)}</div>
      </div>
    </div>
  );
}

function Mini({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="sparky-stat">
      <div className={`text-lg font-bold tabular-nums ${accent ? "text-emerald-300" : "text-white"}`}>{value}</div>
      <div className="sparky-stat__label">{label}</div>
    </div>
  );
}

function BooksTable({ detail }: { detail: SparkyGameDetail }) {
  if (!detail.books?.length) return null;
  return (
    <div className="sparky-card p-4">
      <h3 className="text-sm font-semibold text-white mb-3">By sportsbook ({detail.book_count})</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-left text-muted">
            <tr>
              <th className="py-1 pr-3">Book</th>
              <th className="py-1 pr-3 text-right">Home ML</th>
              <th className="py-1 pr-3 text-right">Away ML</th>
              <th className="py-1 pr-3 text-right">Spread</th>
              <th className="py-1 pr-3 text-right">Total</th>
              <th className="py-1 pr-3 text-right">Home win%</th>
            </tr>
          </thead>
          <tbody>
            {detail.books.map((b) => (
              <tr key={b.book} className="border-t divider">
                <td className="py-1.5 pr-3">{b.book}</td>
                <td className="py-1.5 pr-3 text-right tabular-nums">{americanOdds(b.home_ml)}</td>
                <td className="py-1.5 pr-3 text-right tabular-nums">{americanOdds(b.away_ml)}</td>
                <td className="py-1.5 pr-3 text-right tabular-nums">{b.home_spread ?? "—"}</td>
                <td className="py-1.5 pr-3 text-right tabular-nums">{b.total ?? "—"}</td>
                <td className="py-1.5 pr-3 text-right tabular-nums">
                  {b.home_implied != null ? pctPoints(b.home_implied * 100) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
