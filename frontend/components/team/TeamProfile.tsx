"use client";

import { useMemo, useState } from "react";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card } from "@/components/Card";
import { SkeletonBlock, SkeletonRow } from "@/components/Skeleton";
import { HelpTip } from "@/components/sparky/HelpTip";
import { pctColor } from "@/lib/metrics";
import {
  EfficiencyProfile,
  ProfileMetric,
  RollingUnit,
  SplitProfile,
  TeamProfileConfig,
  formatEpa,
  formatSuccess,
  formatSigned,
  rankCaption,
} from "@/lib/team-profile";

type UnitKey = "offense" | "defense";

const COLUMNS: { unit: UnitKey; key: "epa" | "success_rate" | "schedule_strength"; tip: keyof TeamProfileConfig["metrics"]; format: (v: number | null) => string }[] = [
  { unit: "offense", key: "epa", tip: "offenseEpa", format: formatEpa },
  { unit: "offense", key: "success_rate", tip: "offenseSuccess", format: formatSuccess },
  { unit: "offense", key: "schedule_strength", tip: "offenseSchedule", format: formatSigned },
  { unit: "defense", key: "epa", tip: "defenseEpa", format: formatEpa },
  { unit: "defense", key: "success_rate", tip: "defenseSuccess", format: formatSuccess },
  { unit: "defense", key: "schedule_strength", tip: "defenseSchedule", format: formatSigned },
];

/**
 * League-agnostic efficiency profile. All copy comes from `config`; the
 * numbers come from the backend bundle. A college page can reuse this with
 * its own tooltips and the same JSON shape.
 */
export function TeamProfile({
  profile,
  config,
  loading = false,
  error,
  accent,
}: {
  profile: EfficiencyProfile | undefined;
  config: TeamProfileConfig;
  loading?: boolean;
  error?: string;
  accent?: string;
}) {
  const [unit, setUnit] = useState<UnitKey>("offense");

  if (loading) {
    return (
      <Card title={config.title}>
        <SkeletonRow />
        <SkeletonRow />
        <SkeletonBlock height={220} className="mt-3" />
      </Card>
    );
  }

  if (error) {
    return (
      <Card title={config.title}>
        <p className="text-sm text-red-400">{error}</p>
      </Card>
    );
  }

  if (!profile) return null;

  const filtered = profile.filters?.garbage_time_excluded === true;
  const rolling = profile.rolling?.[unit];
  const waitingCopy = profile.unavailable?.find((item) => item.id === "feature_defs")?.reason
    || (profile.splits?.length ? null : profile.unavailable?.[0]?.reason)
    || profile.filters?.description
    || "Garbage time is not marked as excluded, so that note is withheld.";

  return (
    <Card title={`${config.title}${profile.season ? ` — ${profile.season}` : ""}`}>
      <div className="space-y-4" data-testid="team-profile">
        {filtered ? (
          <FilterNote profile={profile} config={config} />
        ) : (
          <p
            className="text-sm rounded-lg border divider bg-bg/60 px-3 py-2 text-amber-200/90"
            data-testid="feature-waiting"
          >
            {waitingCopy}
          </p>
        )}

        {profile.splits?.length > 0 && (
          <p className="text-xs text-muted leading-relaxed">{config.methodNote}</p>
        )}
        {profile.notes?.map((note) => (
          <p key={note} className="text-xs text-muted leading-relaxed">{note}</p>
        ))}

        {profile.error && (
          <p className="text-sm text-muted">{profile.error}</p>
        )}

        {profile.splits?.length > 0 && (
          <>
            <MetricLegend config={config} />
            <div className="hidden md:block overflow-x-auto">
              <SplitTable splits={profile.splits} config={config} />
            </div>
            <div className="md:hidden space-y-3">
              {profile.splits.map((split) => (
                <SplitCard key={split.id} split={split} config={config} />
              ))}
            </div>
            <SplitGlossary splits={profile.splits} config={config} />
          </>
        )}

        <RollingSection
          unit={unit}
          setUnit={setUnit}
          rolling={rolling}
          config={config}
          accent={accent}
        />

        <UnavailableList profile={profile} />
      </div>
    </Card>
  );
}

/** One-line pointer used on the overview tab. The full table lives on Efficiency. */
export function TeamProfileTeaser({
  profile,
  config,
  onOpen,
}: {
  profile: EfficiencyProfile | undefined;
  config: TeamProfileConfig;
  onOpen: () => void;
}) {
  const filtered = profile?.filters?.garbage_time_excluded === true;
  const offense = profile?.rolling?.offense;
  const last4 = offense?.last4_average;
  const seasonAvg = offense?.season_average;
  const waiting = profile?.unavailable?.find((item) => item.id === "feature_defs")?.reason
    || profile?.unavailable?.[0]?.reason;
  return (
    <Card
      title={config.title}
      action={
        <button type="button" onClick={onOpen} className="text-[11px] text-muted hover:underline">
          Full profile →
        </button>
      }
    >
      {!profile && (
        <p className="text-sm text-muted">Loading this season’s efficiency…</p>
      )}
      {profile && filtered && (
        <p className="text-sm text-muted">{config.garbageTimeNote}</p>
      )}
      {profile && !filtered && (
        <p className="text-sm text-muted">
          {waiting || "Efficiency metrics are not available for this season yet."}
        </p>
      )}
      {offense && (last4 != null || seasonAvg != null) && (
        <p className="mt-2 text-sm tabular-nums">
          <span className="text-muted">Offense, last 4 games </span>
          <span className="font-medium">{formatEpa(last4 ?? null)}</span>
          <span className="text-muted"> · season </span>
          <span className="font-medium">{formatEpa(seasonAvg ?? null)}</span>
        </p>
      )}
    </Card>
  );
}

function FilterNote({
  profile,
  config,
}: {
  profile: EfficiencyProfile;
  config: TeamProfileConfig;
}) {
  const kept = profile.filters.plays_kept;
  const dropped = profile.filters.plays_excluded;
  const counts = kept != null && dropped != null
    ? ` ${kept.toLocaleString()} plays counted, ${dropped.toLocaleString()} left out as garbage time.`
    : "";
  return (
    <p
      className="text-sm rounded-lg border divider px-3 py-2"
      style={{ background: "color-mix(in oklab, var(--team-primary) 12%, transparent)" }}
      data-testid="garbage-time-note"
    >
      {profile.filters.description || config.garbageTimeNote}
      {counts}
    </p>
  );
}

function MetricLegend({ config }: { config: TeamProfileConfig }) {
  const groups: { title: string; tips: (keyof TeamProfileConfig["metrics"])[] }[] = [
    { title: "Offense", tips: ["offenseEpa", "offenseSuccess", "offenseSchedule"] },
    { title: "Defense", tips: ["defenseEpa", "defenseSuccess", "defenseSchedule"] },
  ];
  return (
    <div className="grid gap-2 sm:grid-cols-2 text-xs text-muted">
      {groups.map((group) => (
        <div key={group.title} className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="uppercase tracking-wide text-[10px]">{group.title}</span>
          {group.tips.map((key) => {
            const tip = config.metrics[key];
            return (
              <span key={key} className="inline-flex items-center">
                {tip.label}
                <HelpTip label={tip.label} body={tip.body} />
              </span>
            );
          })}
        </div>
      ))}
    </div>
  );
}

function SplitTable({
  splits,
  config,
}: {
  splits: SplitProfile[];
  config: TeamProfileConfig;
}) {
  return (
    <table className="w-full text-sm min-w-[760px]">
      <thead>
        <tr className="text-[11px] uppercase tracking-wide text-muted">
          <th className="text-left font-medium py-1 pr-3" scope="col">Split</th>
          <th className="text-left font-medium py-1 px-2" scope="col" colSpan={3}>Offense</th>
          <th className="text-left font-medium py-1 px-2 border-l divider" scope="col" colSpan={3}>Defense</th>
        </tr>
        <tr className="text-[11px] text-muted border-b divider">
          <th scope="col" />
          {COLUMNS.map((col) => (
            <th
              key={`${col.unit}-${col.key}`}
              scope="col"
              className={`text-left font-normal py-1 px-2 ${col.unit === "defense" && col.key === "epa" ? "border-l divider" : ""}`}
            >
              {config.metrics[col.tip].label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {splits.map((split) => (
          <tr key={split.id} className="border-b divider align-top">
            <th scope="row" className="text-left font-medium py-2 pr-3 whitespace-nowrap">
              {splitLabel(split, config)}
              {split.adjusted === false ? <span className="text-muted font-normal"> · rate</span> : null}
              {split.approximated ? <span className="text-amber-300/90"> *</span> : null}
            </th>
            {COLUMNS.map((col) => (
              <td
                key={`${col.unit}-${col.key}`}
                className={`py-2 px-2 ${col.unit === "defense" && col.key === "epa" ? "border-l divider" : ""}`}
              >
                <MetricReadout metric={split[col.unit][col.key]} format={col.format} />
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function SplitCard({ split, config }: { split: SplitProfile; config: TeamProfileConfig }) {
  return (
    <div className="rounded-lg border divider p-3">
      <div className="font-medium text-sm mb-2">
        {splitLabel(split, config)}
        {split.adjusted === false ? <span className="text-muted font-normal"> · rate</span> : null}
        {split.approximated ? <span className="text-amber-300/90"> *</span> : null}
      </div>
      {(["offense", "defense"] as const).map((unit) => (
        <div key={unit} className="mb-2 last:mb-0">
          <div className="text-[10px] uppercase tracking-wide text-muted mb-1">
            {unit === "offense" ? "Offense" : "Defense"}
          </div>
          <div className="grid grid-cols-3 gap-2">
            {COLUMNS.filter((col) => col.unit === unit).map((col) => (
              <div key={col.key}>
                <div className="text-[10px] text-muted">{config.metrics[col.tip].label}</div>
                <MetricReadout metric={split[unit][col.key]} format={col.format} />
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function formatMetric(metric: ProfileMetric, fallback: (value: number | null) => string): string {
  if (metric.display === "share") return formatSuccess(metric.value);
  if (metric.display === "signed") return formatSigned(metric.value);
  if (metric.display === "epa") return formatEpa(metric.value);
  return fallback(metric.value);
}

function MetricReadout({
  metric,
  format,
}: {
  metric: ProfileMetric;
  format: (value: number | null) => string;
}) {
  const neutral = metric.rank_sense === "tougher";
  const percentile = metric.percentile;
  const color = percentile == null || neutral ? "#94a3b8" : pctColor(percentile);
  return (
    <div className="min-w-[4.75rem]">
      <div className="font-medium tabular-nums">{formatMetric(metric, format)}</div>
      <div className="mt-1 h-1 rounded-full bg-bg overflow-hidden">
        {percentile != null && (
          <div className="h-full" style={{ width: `${Math.max(0, Math.min(100, percentile))}%`, background: color }} />
        )}
      </div>
      <div className="text-[10px] mt-0.5" style={{ color }}>{rankCaption(metric)}</div>
    </div>
  );
}

function SplitGlossary({
  splits,
  config,
}: {
  splits: SplitProfile[];
  config: TeamProfileConfig;
}) {
  const approximated = splits.find((split) => split.approximated && split.approximation_note);
  return (
    <div className="space-y-2">
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
        {splits.map((split) => {
          const body = config.splitTooltips[split.id]?.body;
          if (!body) return null;
          return (
            <p key={split.id} className="text-[11px] text-muted leading-snug">
              <span className="text-text">{splitLabel(split, config)}. </span>
              {body}
            </p>
          );
        })}
      </div>
      {approximated?.approximation_note && (
        <p className="text-[11px] text-amber-200/80">* {approximated.approximation_note}</p>
      )}
      {splits.some((split) => split.note) && (
        <p className="text-[11px] text-muted">{splits.find((split) => split.note)?.note}</p>
      )}
    </div>
  );
}

function RollingSection({
  unit,
  setUnit,
  rolling,
  config,
  accent,
}: {
  unit: UnitKey;
  setUnit: (unit: UnitKey) => void;
  rolling: RollingUnit | undefined;
  config: TeamProfileConfig;
  accent?: string;
}) {
  const tip = config.rolling[unit];
  const asOf = rolling?.as_of_week === true;
  const color = accent || "var(--team-primary)";
  const last4 = rolling?.last4_average ?? null;
  const season = rolling?.season_average ?? null;
  const delta = last4 != null && season != null ? last4 - season : null;
  const improved = delta == null ? null : unit === "offense" ? delta > 0 : delta < 0;

  const data = useMemo(
    () => (rolling?.games || []).map((game) => ({
      label: game.label,
      value: game.value,
      rolling4: game.rolling4,
      season: game.season_average,
    })),
    [rolling],
  );

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
        <h3 className="text-sm font-medium inline-flex items-center">
          Last 4 games vs season
          <HelpTip label={tip.label} body={`${tip.body} ${config.rollingNote}`} />
        </h3>
        <div className="flex rounded-lg border divider overflow-hidden text-xs">
          {(["offense", "defense"] as const).map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => setUnit(key)}
              className={`px-3 py-1 ${unit === key ? "bg-bg text-text" : "text-muted"}`}
              aria-pressed={unit === key}
            >
              {key === "offense" ? "Offense" : "Defense"}
            </button>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-3 gap-2 mb-3">
        <Summary label="Last 4 games" value={formatEpa(last4)} />
        <Summary label="Season average" value={formatEpa(season)} />
        <Summary
          label="Difference"
          value={delta == null ? "—" : formatEpa(delta)}
          tone={improved == null ? undefined : improved ? "good" : "bad"}
        />
      </div>
      {data.length === 0 ? (
        <p className="text-sm text-muted" data-testid="rolling-empty">
          {rolling?.empty_reason || "No games to chart for this season."}
        </p>
      ) : (
        <div style={{ width: "100%", height: 240 }} data-testid="rolling-chart">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={data} margin={{ top: 8, right: 8, bottom: 4, left: 0 }}>
              <CartesianGrid stroke="rgba(255,255,255,0.06)" />
              <XAxis dataKey="label" tick={{ fill: "#94a3b8", fontSize: 10 }} minTickGap={24} interval="preserveStartEnd" />
              <YAxis tick={{ fill: "#94a3b8", fontSize: 11 }} width={40} />
              <ReferenceLine y={0} stroke="rgba(148,163,184,0.35)" />
              <Tooltip
                contentStyle={{
                  background: "var(--panel)",
                  border: "1px solid var(--border)",
                  color: "var(--text)",
                  fontSize: 12,
                }}
                formatter={(value: number, name: string) => [formatEpa(value), name]}
              />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              {!asOf && (
                <Bar dataKey="value" name="This game" fill={color} fillOpacity={0.35} radius={[3, 3, 0, 0]} isAnimationActive={false} />
              )}
              <Line type="monotone" dataKey="rolling4" name={asOf ? "Last 4 entering the week" : "Last 4 games"} stroke={color} strokeWidth={2.5} dot={{ r: 3 }} connectNulls={false} isAnimationActive={false} />
              <Line type="monotone" dataKey="season" name={asOf ? "Season entering the week" : "Season average"} stroke="#94a3b8" strokeDasharray="5 4" strokeWidth={1.5} dot={false} isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
      {data.length > 0 && data.every((row) => row.rolling4 == null) && (
        <p className="text-[11px] text-muted mt-1">
          The four-game line shows up once this team has four games. The season average is the flat line.
        </p>
      )}
    </div>
  );
}

function Summary({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "good" | "bad";
}) {
  const color = tone === "good" ? "#34d399" : tone === "bad" ? "#f87171" : undefined;
  return (
    <div className="rounded-lg bg-bg px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-wide text-muted">{label}</div>
      <div className="font-medium tabular-nums" style={{ color }}>{value}</div>
    </div>
  );
}

function UnavailableList({ profile }: { profile: EfficiencyProfile }) {
  // The banner already explains a missing win-probability filter. Anything
  // else (no success flag, no down, and so on) still needs a line of its own.
  const hidden = new Set(
    profile.filters?.garbage_time_excluded
      ? []
      : ["win_probability", "play_by_play", "competitive_plays", "epa", "play_type", "feature_defs"],
  );
  const visible = (profile.unavailable || []).filter((item) => item.reason && !hidden.has(item.id));
  if (!visible.length) return null;
  return (
    <ul className="text-[11px] text-muted space-y-1">
      {visible.map((item) => (
        <li key={item.id}>{item.reason}</li>
      ))}
    </ul>
  );
}

function splitLabel(split: SplitProfile, config: TeamProfileConfig): string {
  return config.splitTooltips[split.id]?.label || split.label;
}
