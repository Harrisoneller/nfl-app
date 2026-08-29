"use client";
import { SparkyWeeks } from "@/lib/api";
import { HelpTip } from "./HelpTip";

/**
 * Week selector for the Sparky board.
 *
 * Before this, "the slate" was a rolling time window: anchor on the earliest
 * still-upcoming kickoff, take everything within six and a half days. It
 * produced two visible failures, and this component is the answer to both:
 *
 *  - the board **shrank through the week**, because games more than four hours
 *    old fell out of the window. Week membership is now fixed, and started
 *    games are *counted and toggleable* rather than silently gone.
 *  - **weeks bled together**, because once Saturday's games finished the anchor
 *    moved to the last game of the week and the window reached into the next
 *    one. Picking a week now means exactly that week.
 *
 * The `games` / `priced` split on each chip is deliberate. A week the schedule
 * says has 61 games but Sparky has priced 0 of is an *unbuilt slate*, which is a
 * completely different problem from a quiet week and must not look the same.
 */
export function WeekSelector({
  weeks,
  selected,
  onSelect,
  startedCount = 0,
  includeStarted = false,
  onToggleStarted,
}: {
  weeks?: SparkyWeeks;
  selected: number | null;
  onSelect: (week: number) => void;
  startedCount?: number;
  includeStarted?: boolean;
  onToggleStarted?: (next: boolean) => void;
}) {
  if (!weeks) return null;

  // Offseason or a fresh database: there is no schedule to bucket by, and the
  // backend has fallen back to a time window. Say so rather than rendering an
  // empty strip that looks broken.
  if (!weeks.schedule_available || weeks.weeks.length === 0) {
    return (
      <div className="sparky-card p-3 text-[11px] text-muted">
        No schedule loaded for {weeks.season}, so games can&apos;t be grouped into weeks
        yet. Showing whatever upcoming games have odds. Sync the season schedule to get
        the week selector back.
      </div>
    );
  }

  const current = weeks.current_week;
  const active = selected ?? current;

  return (
    <div className="sparky-card p-3 space-y-2">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center text-[11px] text-muted">
          <span className="uppercase tracking-wide">{weeks.season} season</span>
          <HelpTip
            label="Week"
            body="Official NFL weeks from the schedule, not a rolling date range. A week is a fixed set of games, so the board no longer changes shape depending on when you look at it or shrinks as games are played."
          />
        </div>

        {startedCount > 0 && onToggleStarted && (
          <label className="flex items-center gap-1.5 text-[11px] text-muted cursor-pointer">
            <input
              type="checkbox"
              checked={includeStarted}
              onChange={(e) => onToggleStarted(e.target.checked)}
              className="accent-emerald-400"
            />
            Show {startedCount} game{startedCount === 1 ? "" : "s"} already started
          </label>
        )}
      </div>

      <div className="flex gap-1.5 overflow-x-auto pb-1">
        {weeks.weeks.map((w) => {
          const isActive = w.week === active;
          const isCurrent = w.week === current;
          const unbuilt = w.games > 0 && w.priced === 0;
          return (
            <button
              key={w.week}
              onClick={() => onSelect(w.week)}
              title={
                unbuilt
                  ? `${w.games} games scheduled, none priced yet — this slate hasn't been built`
                  : `${w.priced} of ${w.games} games priced`
              }
              className={`shrink-0 px-3 py-1.5 rounded-lg text-xs font-semibold border transition-colors ${
                isActive
                  ? "bg-emerald-500/20 border-emerald-400/60 text-emerald-200"
                  : "border-slate-700/60 text-muted hover:text-white hover:border-slate-500"
              }`}
            >
              <span>{w.label}</span>
              {isCurrent && !isActive && (
                <span className="ml-1.5 text-[9px] text-cyan-300">now</span>
              )}
              <span
                className={`ml-1.5 text-[10px] tabular-nums ${
                  unbuilt ? "text-amber-400" : "text-muted/60"
                }`}
              >
                {w.priced}/{w.games}
              </span>
            </button>
          );
        })}
      </div>

      {(() => {
        const w = weeks.weeks.find((x) => x.week === active);
        if (!w) return null;
        if (w.games > 0 && w.priced === 0) {
          return (
            <p className="text-[11px] text-amber-300/90">
              {w.label} has {w.games} games on the schedule but none priced yet — this
              slate hasn&apos;t been built. That is not the same as a quiet week.
            </p>
          );
        }
        return (
          <p className="text-[11px] text-muted/70">
            {w.priced} of {w.games} games priced
            {startedCount > 0 && !includeStarted && (
              <> · {startedCount} already started and hidden</>
            )}
          </p>
        );
      })()}
    </div>
  );
}
