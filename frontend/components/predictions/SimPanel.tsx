import type { FrozenSim } from "@/lib/api";
import {
  SIM_MARGIN_LABEL,
  SIM_PANEL_LABEL,
  SIM_PANEL_NOTE,
  SIM_TOTAL_LABEL,
  SIM_WP_LABEL,
  formatFrozenAt,
  formatSimHomeWp,
  formatSimMarginHome,
  formatSimTotal,
} from "@/lib/simPanel";

/**
 * Secondary block under the v5 headline. Hidden when this game has no
 * frozen pre-kickoff sim row.
 */
export function SimPanel({ sim }: { sim: FrozenSim | null | undefined }) {
  if (!sim) return null;
  return (
    <section
      className="mt-4 rounded-lg border border-dashed divider bg-bg/40 px-3 py-3"
      aria-label={SIM_PANEL_LABEL}
    >
      <h3 className="text-[11px] uppercase tracking-wider font-semibold text-muted">
        {SIM_PANEL_LABEL}
      </h3>
      <dl className="grid grid-cols-1 sm:grid-cols-3 gap-2 mt-2 text-sm">
        <div>
          <dt className="text-[10px] uppercase tracking-wide text-muted">{SIM_TOTAL_LABEL}</dt>
          <dd className="font-semibold tabular-nums">{formatSimTotal(sim.sim_total)}</dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase tracking-wide text-muted" title="Home score minus away score">
            {SIM_MARGIN_LABEL}
          </dt>
          <dd className="font-semibold tabular-nums">{formatSimMarginHome(sim.sim_margin_home)}</dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase tracking-wide text-muted">{SIM_WP_LABEL}</dt>
          <dd className="font-semibold tabular-nums">{formatSimHomeWp(sim.sim_home_wp)}</dd>
        </div>
      </dl>
      <p className="text-[10px] text-muted mt-2">Frozen {formatFrozenAt(sim.frozen_at)}</p>
      <p className="text-[10px] text-muted">{SIM_PANEL_NOTE}</p>
    </section>
  );
}
