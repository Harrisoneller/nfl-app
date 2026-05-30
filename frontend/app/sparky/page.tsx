"use client";
import { useState, useEffect } from "react";
import useSWR from "swr";
import { api, SparkyParlay } from "@/lib/api";
import { TeamLogo } from "@/components/TeamLogo";
import { PredictionCard } from "@/components/sparky/PredictionCard";
import { ParlayBuilder } from "@/components/sparky/ParlayBuilder";
import { AccuracyPanel } from "@/components/sparky/AccuracyPanel";
import { AdminPanel } from "@/components/sparky/AdminPanel";
import { americanOdds, pct } from "@/components/sparky/format";

type TabId = "dashboard" | "parlay" | "accuracy" | "admin";

const TABS: { id: TabId; label: string }[] = [
  { id: "dashboard", label: "Dashboard" },
  { id: "parlay", label: "Parlay Builder" },
  { id: "accuracy", label: "Historical Accuracy" },
  { id: "admin", label: "Admin / Debug" },
];

export default function SparkyPage() {
  const [tab, setTab] = useState<TabId>("dashboard");
  const [forceReal, setForceReal] = useState(true); // Prefer real Week 1 data by default
  const slate = useSWR(
    ["sparky-slate", forceReal],
    () => api.sparkySlate(undefined, forceReal)
  );

  // On first load, strongly prefer real data
  useEffect(() => {
    if (forceReal === false && (slate.data?.games?.length === 0 || isSynthetic)) {
      setForceReal(true);
    }
  }, []);
  // Lazy: only fetch accuracy / admin status when those tabs are active.
  const accuracy = useSWR(tab === "accuracy" ? ["sparky-accuracy"] : null, () => api.sparkyAccuracy());
  const admin = useSWR(tab === "admin" ? ["sparky-admin"] : null, () => api.sparkyAdminStatus());

  const games = slate.data?.games ?? [];
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

      <Hero count={games.length} slateDate={slate.data?.slate_date ?? null} />

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

      {tab === "dashboard" && !slate.isLoading && (
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
          <div className="space-y-6">
            {recommended.length > 0 && <RecommendedParlay parlay={recommended[0]} />}
            <div>
              <h2 className="home-section-title mb-3">Today&apos;s predictions</h2>
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                {games.map((g) => (
                  <PredictionCard key={g.event_id} game={g} />
                ))}
              </div>
            </div>
          </div>
        )
      )}

      {tab === "parlay" && !slate.isLoading && <ParlayBuilder games={games} />}

      {tab === "accuracy" && (
        accuracy.isLoading ? (
          <div className="sparky-card p-6 text-sm text-muted">Loading accuracy…</div>
        ) : accuracy.data ? (
          <AccuracyPanel data={accuracy.data} />
        ) : (
          <div className="sparky-card p-6 text-sm text-muted">No accuracy data yet.</div>
        )
      )}

      {tab === "admin" && (
        <AdminPanel
          status={admin.data}
          onChanged={() => {
            admin.mutate();
            slate.mutate();
          }}
        />
      )}

      <p className="text-[11px] text-muted/70">
        Sparky is an analytics tool — predictions and signals are informational, not betting advice.
      </p>
    </div>
  );
}

function Hero({ count, slateDate }: { count: number; slateDate: string | null }) {
  return (
    <div className="sparky-hero">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <div className="sparky-tagline">Sharp NFL Predictions · Intelligent Parlays · Real Edge</div>
          <h1 className="sparky-hero__title mt-1">Sparky</h1>
        </div>
        <div className="text-right">
          <div className="text-2xl font-bold text-white tabular-nums">{count}</div>
          <div className="text-[11px] text-muted">
            games on slate{slateDate ? ` · ${slateDate}` : ""}
          </div>
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
          <div className="sparky-tagline text-emerald-300">Recommended parlay · rank #1</div>
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
          <div className="text-[11px] text-muted">
            {pct(parlay.combined_win_prob, 1)} model hit · composite{" "}
            <span className="text-emerald-300 font-semibold">{parlay.composite_score.toFixed(0)}</span>
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
