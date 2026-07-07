"use client";
import { useState } from "react";
import Link from "next/link";
import { api, ComparePlayerEntry, PlayerComparison } from "@/lib/api";
import { Card } from "@/components/Card";

/**
 * Player compare — 2–4 players side by side: season projection distributions
 * (overlaid bands), next-game projections, usage shares, and consistency
 * profiles from last season's weekly data.
 */

const PLAYER_COLORS = ["#3b82f6", "#22c55e", "#f59e0b", "#ec4899"];

export function CompareTab() {
  const [namesText, setNamesText] = useState("Ja'Marr Chase, Justin Jefferson");
  const [scoring, setScoring] = useState("ppr");
  const [data, setData] = useState<PlayerComparison | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setErr(null);
    try {
      const names = namesText.split(/\n|,/).map((s) => s.trim()).filter(Boolean).slice(0, 4);
      if (names.length < 2) {
        setErr("Enter at least two players (comma-separated).");
        return;
      }
      const enriched = await api.enrichRoster(names);
      const ids = enriched.rows.filter((r: any) => r.player_id).map((r: any) => r.player_id);
      const missing = enriched.rows.filter((r: any) => !r.player_id).map((r: any) => r.query);
      if (ids.length < 2) {
        setErr(`Couldn't resolve enough players${missing.length ? ` (not found: ${missing.join(", ")})` : ""}.`);
        return;
      }
      if (missing.length) setErr(`Not found: ${missing.join(", ")}`);
      setData(await api.comparePlayerProjections(ids));
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  const players = (data?.players || []).filter((p) => !p.error);

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap gap-2 items-center">
          <input
            value={namesText}
            onChange={(e) => setNamesText(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && run()}
            placeholder="2–4 players, comma-separated…"
            className="bg-bg border divider rounded px-3 py-2 text-sm flex-1 min-w-[260px]"
          />
          <select
            value={scoring}
            onChange={(e) => setScoring(e.target.value)}
            className="bg-bg border divider rounded px-2 py-2 text-xs"
          >
            <option value="ppr">PPR</option>
            <option value="half_ppr">Half-PPR</option>
            <option value="standard">Standard</option>
          </select>
          <button
            onClick={run}
            disabled={busy}
            className="bg-team-primary text-white text-sm rounded px-4 py-2 disabled:opacity-50"
          >
            {busy ? "Comparing…" : "Compare"}
          </button>
        </div>
        {err && <p className="text-xs text-amber-500 mt-2">{err}</p>}
        <p className="text-[11px] text-muted mt-2">
          Projections are full distributions — the overlap of two players&apos; bands is
          the honest answer to &quot;who should I take&quot;. Usage and consistency come from
          last season&apos;s weekly data ({data?.usage_season ?? "…"}).
        </p>
      </Card>

      {players.length >= 2 && (
        <>
          <SeasonOverlay players={players} scoring={scoring} />
          <div className={`grid grid-cols-1 ${players.length > 2 ? "lg:grid-cols-3 md:grid-cols-2" : "md:grid-cols-2"} gap-4`}>
            {players.map((p, i) => (
              <PlayerCard key={p.player_id} p={p} color={PLAYER_COLORS[i]} scoring={scoring} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function SeasonOverlay({ players, scoring }: { players: ComparePlayerEntry[]; scoring: string }) {
  const bands = players.map((p) => {
    const f = p.season_projection?.fantasy?.[scoring];
    const q = f?.quantiles as Record<string, number> | undefined;
    return q ? { name: p.name ?? "?", p10: q.p10, p50: q.p50, p90: q.p90 } : null;
  });
  const max = Math.max(1, ...bands.filter(Boolean).map((b) => b!.p90));

  return (
    <Card title={`Rest-of-season fantasy (${scoring.replace("_", "-").toUpperCase()}) — p10 / median / p90`}>
      <div className="space-y-2">
        {bands.map((b, i) =>
          b ? (
            <div key={i} className="flex items-center gap-3">
              <span className="text-xs w-40 truncate" style={{ color: PLAYER_COLORS[i] }}>
                {b.name}
              </span>
              <div className="relative h-3 flex-1 rounded bg-bg border divider">
                <div
                  className="absolute h-full rounded"
                  style={{
                    left: `${(b.p10 / max) * 100}%`,
                    width: `${Math.max(1, ((b.p90 - b.p10) / max) * 100)}%`,
                    backgroundColor: `${PLAYER_COLORS[i]}44`,
                  }}
                />
                <div
                  className="absolute w-1 h-full rounded"
                  style={{ left: `calc(${(b.p50 / max) * 100}% - 2px)`, backgroundColor: PLAYER_COLORS[i] }}
                />
              </div>
              <span className="text-[10px] text-muted tabular-nums w-28 text-right">
                {b.p10.toFixed(0)} / {b.p50.toFixed(0)} / {b.p90.toFixed(0)}
              </span>
            </div>
          ) : (
            <p key={i} className="text-xs text-muted">No projection for {players[i]?.name}.</p>
          ),
        )}
      </div>
    </Card>
  );
}

function PlayerCard({ p, color, scoring }: { p: ComparePlayerEntry; color: string; scoring: string }) {
  const next = p.next_game;
  const nf = next?.fantasy?.[scoring];
  const cons = p.usage?.consistency;
  const shares = p.usage?.shares;
  const weekly = p.usage?.weekly || [];

  return (
    <Card
      title={
        <span>
          <span style={{ color }}>{p.name}</span>
          <span className="text-muted text-xs ml-2">
            {p.position} · {p.team ?? "FA"}
            {p.injury_status ? ` · ${p.injury_status}` : ""}
          </span>
        </span>
      }
    >
      <div className="space-y-3 text-xs">
        <div>
          <div className="text-muted uppercase tracking-wide text-[10px] mb-1">Next game</div>
          {next ? (
            <p>
              {next.is_home ? "vs" : "@"} {next.opponent} · grade{" "}
              <span className="font-bold">{next.matchup_grade}</span>
              {nf && (
                <>
                  {" "}· proj <span className="font-semibold tabular-nums">{nf.mean.toFixed(1)}</span>
                  <span className="text-muted"> ±{nf.sd.toFixed(1)}</span>
                </>
              )}
            </p>
          ) : (
            <p className="text-muted">No upcoming game projection.</p>
          )}
        </div>

        <div>
          <div className="text-muted uppercase tracking-wide text-[10px] mb-1">
            Usage ({p.usage?.season}, {p.usage?.games ?? 0} gms)
          </div>
          <p>
            {shares?.target_share != null && <>Target share <b>{(shares.target_share * 100).toFixed(0)}%</b> · </>}
            {shares?.carry_share != null && <>Carry share <b>{(shares.carry_share * 100).toFixed(0)}%</b> · </>}
            {cons?.ppg_ppr != null ? <>PPG <b>{cons.ppg_ppr.toFixed(1)}</b></> : "no weekly data"}
          </p>
          {weekly.length > 1 && <Sparkline weekly={weekly} color={color} />}
        </div>

        {cons?.ppg_ppr != null && (
          <div>
            <div className="text-muted uppercase tracking-wide text-[10px] mb-1">Consistency</div>
            <p>
              Floor (p25) <b className="tabular-nums">{cons.floor_p25}</b> · Ceiling (p75){" "}
              <b className="tabular-nums">{cons.ceiling_p75}</b> · Volatility (CV){" "}
              <b className="tabular-nums">{cons.cv ?? "—"}</b>
            </p>
            <p className="text-muted">
              Best {cons.best} / worst {cons.worst} in a game.
            </p>
          </div>
        )}

        {p.player_id && (
          <Link href={`/players/${p.player_id}`} className="text-team-primary hover:underline text-[11px]">
            Full profile →
          </Link>
        )}
      </div>
    </Card>
  );
}

function Sparkline({ weekly, color }: { weekly: Record<string, unknown>[]; color: string }) {
  const vals = weekly.map((w) =>
    typeof w.fantasy_points_ppr === "number" ? w.fantasy_points_ppr : 0,
  );
  const max = Math.max(1, ...vals);
  const W = 220;
  const H = 36;
  const step = W / Math.max(1, vals.length - 1);
  const points = vals.map((v, i) => `${(i * step).toFixed(1)},${(H - (v / max) * H).toFixed(1)}`).join(" ");
  return (
    <svg width={W} height={H} className="mt-1 block" aria-label="Weekly PPR points">
      <polyline points={points} fill="none" stroke={color} strokeWidth="1.5" />
    </svg>
  );
}
