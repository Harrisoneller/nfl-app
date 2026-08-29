"use client";
import { useState, useEffect, useMemo } from "react";
import useSWR from "swr";
import { api, SparkyGame, SparkyParlay } from "@/lib/api";
import { useAuth } from "@/context/AuthProvider";
import { TeamLogo } from "@/components/TeamLogo";
import { PredictionCard } from "@/components/sparky/PredictionCard";
import { ParlayBuilder } from "@/components/sparky/ParlayBuilder";
import { AccuracyPanel } from "@/components/sparky/AccuracyPanel";
import { AdminPanel } from "@/components/sparky/AdminPanel";
import { HowSparkyWorks } from "@/components/sparky/HowSparkyWorks";
import { SparkyGlossary } from "@/components/sparky/SparkyGlossary";
import { ValueBoard } from "@/components/sparky/ValueBoard";
import { WeekSelector } from "@/components/sparky/WeekSelector";
import { HelpTip, TERMS } from "@/components/sparky/HelpTip";
import { americanOdds, isNflMatchup, pct } from "@/components/sparky/format";

type TabId = "value" | "parlay" | "predictions" | "accuracy" | "admin";

// Order is the argument. The board that says what is *worth betting* leads;
// the grid that says who *wins* is a second-class tab, because that is what it
// is. The old order — a confidence-sorted prediction grid on the landing view —
// is what made 25-point favorites read as the product's top recommendation.
const ALL_TABS: { id: TabId; label: string; adminOnly?: boolean }[] = [
  { id: "value", label: "Value Board" },
  { id: "parlay", label: "Parlay Builder" },
  { id: "predictions", label: "Game Predictions" },
  { id: "accuracy", label: "Historical Accuracy" },
  { id: "admin", label: "Admin / Debug", adminOnly: true },
];

export default function SparkyPage() {
  const { user } = useAuth();
  // Effective admin flag is computed server-side in /auth/me — it reflects the
  // ADMIN_EMAILS allowlist when set, falling back to the DB is_admin column.
  // Same logic as require_admin on the backend, so the tab and the API agree.
  const isAdmin = !!user?.is_admin;
  const TABS = useMemo(
    () => ALL_TABS.filter((t) => !t.adminOnly || isAdmin),
    [isAdmin],
  );

  const [tab, setTab] = useState<TabId>("value");
  // If a user lands on /sparky already on the admin tab (URL persistence,
  // back-button, etc.) and turns out not to be an admin, bounce them home.
  useEffect(() => {
    if (tab === "admin" && !isAdmin) setTab("value");
  }, [tab, isAdmin]);

  const [forceReal, setForceReal] = useState(true); // Prefer real data by default

  // Week is the board's primary structure. `null` means "whatever the backend
  // calls the current week" — resolved server-side from the schedule, not from
  // a rolling date window.
  const [week, setWeek] = useState<number | null>(null);
  const [includeStarted, setIncludeStarted] = useState(false);

  const weeks = useSWR(["sparky-weeks"], () => api.sparkyWeeks());
  const slate = useSWR(
    ["sparky-slate", forceReal, week, includeStarted],
    () => api.sparkySlate(undefined, forceReal, undefined, week, includeStarted)
  );

  // On first load, strongly prefer real data
  useEffect(() => {
    if (forceReal === false && (slate.data?.games?.length === 0 || isSynthetic)) {
      setForceReal(true);
    }
  }, []);
  // The value board is its own request: it prices every side of every market,
  // which the /slate payload does not carry. Fetched alongside the slate rather
  // than lazily because it is the landing tab.
  const [strictness, setStrictness] = useState<"strict" | "balanced" | "loose">(
    "balanced",
  );
  const valueBoard = useSWR(
    ["sparky-value-board", strictness, week, includeStarted],
    () => api.sparkyValueBoard({ strictness, week, includeStarted }),
  );

  // Lazy: only fetch accuracy / admin status when those tabs are active.
  const accuracy = useSWR(tab === "accuracy" ? ["sparky-accuracy"] : null, () => api.sparkyAccuracy());
  const admin = useSWR(tab === "admin" && isAdmin ? ["sparky-admin"] : null, () => api.sparkyAdminStatus());

  const games = (slate.data?.games ?? []).filter((g: SparkyGame) =>
    isNflMatchup(g.home_team_id, g.away_team_id),
  );
  const recommended = slate.data?.recommended_parlays ?? [];
  const isEmpty = !slate.isLoading && games.length === 0;
  const realDataAvailable = !!slate.data?.real_data_available;

  // Detect if we're currently showing synthetic demo data
  const isSynthetic = games.some((g: any) => g.event_id?.startsWith("demo-"));

  return (
    <div className="sparky-scope space-y-6">
      {isSynthetic && (
        <div className="sparky-card p-4 bg-amber-900/20 border border-amber-500/40">
          <div className="flex items-center justify-between gap-4 flex-wrap">
            <div>
              <div className="text-sm font-medium text-amber-300">Viewing synthetic demo data</div>
              <div className="text-xs text-muted mt-0.5">
                Week 1 real schedule + odds + model predictions are available. Switch to see the actual games.
              </div>
            </div>
            <button
              onClick={() => {
                setForceReal(true);
                slate.mutate();
              }}
              className="sparky-btn sparky-btn--solid !py-1.5 !px-4 text-sm"
            >
              Switch to real Week 1 schedule
            </button>
          </div>
        </div>
      )}

      <Hero
        count={games.length}
        slateDate={slate.data?.slate_date ?? null}
        weekLabel={
          slate.data?.week != null
            ? slate.data.week === 0
              ? "Week 0"
              : `Week ${slate.data.week}`
            : null
        }
      />

      {/* Week is the board's structure, so it sits above the tabs and applies
          to every one of them. */}
      <WeekSelector
        weeks={weeks.data}
        selected={week ?? slate.data?.week ?? null}
        onSelect={setWeek}
        startedCount={slate.data?.started_count ?? 0}
        includeStarted={includeStarted}
        onToggleStarted={setIncludeStarted}
      />

      {/* First-visit orientation — dismissible, persisted in localStorage */}
      {tab === "value" && <HowSparkyWorks />}

      {/* Tabs */}
      <div className="flex gap-2 overflow-x-auto pb-1">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`sparky-tab ${tab === t.id ? "sparky-tab--active" : ""}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {slate.isLoading && tab !== "accuracy" && tab !== "admin" && (
        <div className="sparky-card p-6 text-sm text-muted">Loading today's slate…</div>
      )}

      {tab === "value" && (
        isEmpty && !slate.isLoading ? (
          <EmptyState
            onSeeded={() => {
              setForceReal(true);
              slate.mutate();
              valueBoard.mutate();
            }}
            realDataAvailable={realDataAvailable}
            onBuildReal={() => {
              setForceReal(true);
              slate.mutate();
              valueBoard.mutate();
            }}
          />
        ) : (
          <div className="space-y-6">
            {recommended.length > 0 ? (
              <RecommendedParlay parlay={recommended[0]} />
            ) : (
              /* No parlay cleared the +EV gate, which is the usual outcome. That
                 is a reason to point at the builder, not to remove the entry
                 point — the builder prices any combination you ask for. */
              <div className="sparky-card p-4 flex items-center justify-between gap-4 flex-wrap">
                <div className="text-xs text-muted leading-relaxed max-w-2xl">
                  <span className="text-slate-300 font-medium">
                    No parlay on this slate is +EV.
                  </span>{" "}
                  Sparky won&apos;t manufacture one — but you can still build any
                  combination you like and see exactly what it is worth, positive or
                  negative.
                </div>
                <button
                  onClick={() => setTab("parlay")}
                  className="sparky-btn !py-1.5 !px-4 !text-xs shrink-0"
                >
                  Open Parlay Builder →
                </button>
              </div>
            )}
            <ValueBoard
              board={valueBoard.data}
              isLoading={valueBoard.isLoading}
              strictness={strictness}
              onStrictnessChange={setStrictness}
              onRebuild={async () => {
                try {
                  await api.sparkyAdminRefresh();
                } catch {
                  /* non-admins can still re-fetch what is there */
                }
                slate.mutate();
                valueBoard.mutate();
              }}
            />
          </div>
        )
      )}

      {tab === "predictions" && !slate.isLoading && (
        isEmpty ? (
          <EmptyState
            onSeeded={() => {
              setForceReal(true);
              slate.mutate();
            }}
            realDataAvailable={realDataAvailable}
            onBuildReal={() => {
              setForceReal(true);
              slate.mutate();
            }}
          />
        ) : (
          <div className="space-y-4">
            <div className="sparky-card p-4 text-xs text-muted leading-relaxed">
              <span className="text-slate-300 font-medium">These are forecasts, not bets.</span>{" "}
              The tier on each card says how confident Sparky is about who wins — and confidence
              peaks exactly where the model and the market agree, which is where there is no
              money left. A 95% pick and a good bet are close to opposites. For what is worth
              staking, use the{" "}
              <button
                onClick={() => setTab("value")}
                className="text-cyan-300 hover:underline"
              >
                Value Board
              </button>
              .
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
              {games.map((g) => (
                <PredictionCard key={g.event_id} game={g} />
              ))}
            </div>
          </div>
        )
      )}

      {tab === "parlay" && !slate.isLoading && (
        <ParlayBuilder
          games={games}
          valueBoard={valueBoard.data}
          valueBoardLoading={valueBoard.isLoading}
        />
      )}

      {tab === "accuracy" && (
        accuracy.isLoading ? (
          <div className="sparky-card p-6 text-sm text-muted">Loading accuracy…</div>
        ) : accuracy.data ? (
          <AccuracyPanel data={accuracy.data} />
        ) : (
          <div className="sparky-card p-6 text-sm text-muted">No accuracy data yet.</div>
        )
      )}

      {tab === "admin" && isAdmin && (
        <AdminPanel
          status={admin.data}
          onChanged={() => {
            admin.mutate();
            slate.mutate();
          }}
        />
      )}

      {/* Persistent glossary — single source of truth for every Sparky term */}
      <SparkyGlossary />

      <p className="text-[11px] text-muted/70">
        Sparky is an analytics tool — predictions and signals are informational, not betting advice.
      </p>
    </div>
  );
}

function Hero({
  count,
  slateDate,
  weekLabel,
}: {
  count: number;
  slateDate: string | null;
  weekLabel?: string | null;
}) {
  return (
    <div className="sparky-hero">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <div className="sparky-tagline">Sharp NFL Predictions · Intelligent Parlays · Real Edge</div>
          <h1 className="sparky-hero__title mt-1">Sparky</h1>
          <p className="text-xs text-slate-300/80 mt-1 max-w-2xl">
            Sparky prices every moneyline, spread and total on the board against its own NFL
            model, then shows only the ones still worth betting after its edge is shrunk to what
            settled results support — with the stake. Hover any{" "}
            <span
              className="sparky-help__btn"
              aria-hidden
              style={{ cursor: "default", verticalAlign: "middle" }}
            >
              ?
            </span>{" "}
            icon for a plain-English explanation.
          </p>
        </div>
        <div className="text-right">
          <div className="text-2xl font-bold text-white tabular-nums">{count}</div>
          <div className="text-[11px] text-muted">
            {weekLabel ? `games · ${weekLabel}` : "games on slate"}
          </div>
          {slateDate && (
            <div className="text-[10px] text-muted/60">built {slateDate}</div>
          )}
        </div>
      </div>
    </div>
  );
}

function RecommendedParlay({ parlay }: { parlay: SparkyParlay }) {
  return (
    <div className="sparky-card sparky-card--rank1 p-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <div className="sparky-tagline text-emerald-300">
            Sparky's top-ranked parlay
            <HelpTip
              label="Sparky's #1 parlay"
              body="The single best-blended pick from today's slate — ranked by confidence, signal support, mix of favorites and underdogs, and value (+EV), not just the longest payout."
            />
          </div>
          <div className="mt-2 flex items-center gap-3 flex-wrap">
            {parlay.legs.map((leg) => (
              <span key={leg.event_id} className="flex items-center gap-1.5">
                {leg.team_id ? <TeamLogo teamId={leg.team_id} size={26} /> : null}
                <span className={`text-sm ${leg.is_underdog ? "text-amber-300" : "text-white"}`}>
                  {leg.team_id}
                </span>
                <span className="text-[10px] text-muted tabular-nums">{americanOdds(leg.price_american)}</span>
              </span>
            ))}
          </div>
        </div>
        <div className="text-right">
          <div className="text-2xl font-bold text-white tabular-nums">
            {americanOdds(parlay.parlay_odds_american)}
          </div>
          <div className="text-[11px] text-muted flex items-center justify-end">
            {pct(parlay.combined_win_prob, 1)} Sparky-hit
            <HelpTip label={TERMS.combined_win_prob.label} body={TERMS.combined_win_prob.body} />
            · composite{" "}
            <span className="text-emerald-300 font-semibold ml-1">{parlay.composite_score.toFixed(0)}</span>
            <HelpTip label={TERMS.composite.label} body={TERMS.composite.body} />
          </div>
        </div>
      </div>
      <p className="mt-3 text-xs text-slate-300/80 leading-relaxed">{parlay.explanation}</p>
    </div>
  );
}

function EmptyState({ 
  onSeeded, 
  realDataAvailable,
  onBuildReal 
}: { 
  onSeeded: () => void; 
  realDataAvailable?: boolean;
  onBuildReal?: () => void;
}) {
  const [busy, setBusy] = useState<"demo" | "real" | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const seedDemo = async () => {
    setBusy("demo");
    setErr(null);
    try {
      await api.sparkyAdminBackfill(30);
      onSeeded();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to seed demo data");
    } finally {
      setBusy(null);
    }
  };

  const buildReal = async () => {
    if (!onBuildReal) return;
    setBusy("real");
    setErr(null);
    try {
      await api.sparkyAdminRefresh();
      onBuildReal();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to build from real data");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="sparky-card p-6">
      <h2 className="text-lg font-semibold text-white">No Sparky slate built yet</h2>
      
      {realDataAvailable ? (
        <>
          <p className="text-sm text-muted mt-2 max-w-2xl">
            Real Week 1 odds and model predictions are available in the system. 
            You can build proper Sparky predictions (with signals, confidence, and parlay rankings) 
            from that live data right now.
          </p>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <button 
              onClick={buildReal} 
              disabled={!!busy} 
              className="sparky-btn sparky-btn--solid"
            >
              {busy === "real" ? "Building real predictions…" : "Build from real Week 1 data"}
            </button>
            <button 
              onClick={seedDemo} 
              disabled={!!busy} 
              className="sparky-btn"
            >
              {busy === "demo" ? "Seeding demo…" : "Or generate demo data instead"}
            </button>
            {err && <span className="text-xs text-red-400">{err}</span>}
          </div>
        </>
      ) : (
        <>
          <p className="text-sm text-muted mt-2 max-w-2xl">
            Sparky builds its slate from captured sportsbook line history. In-season, that happens
            automatically with the twice-daily odds pull. Right now there are no upcoming games captured
            (likely the offseason), so there&apos;s nothing to predict yet.
          </p>
          <p className="text-sm text-muted mt-2 max-w-2xl">
            You can seed a realistic 30-day demo — synthetic line movement, predictions, ranked parlays,
            and settled accuracy history — to explore every view immediately.
          </p>
          <div className="mt-4 flex items-center gap-3">
            <button onClick={seedDemo} disabled={!!busy} className="sparky-btn sparky-btn--solid">
              {busy === "demo" ? "Seeding demo…" : "Generate demo data"}
            </button>
            {err && <span className="text-xs text-red-400">{err}</span>}
          </div>
        </>
      )}
    </div>
  );
}
