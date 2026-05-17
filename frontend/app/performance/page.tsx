"use client";
import useSWR from "swr";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, EloBacktest, MLBacktest } from "@/lib/api";
import { Card } from "@/components/Card";

export default function ModelPerformancePage() {
  const { data, isLoading } = useSWR(["backtest"], () => api.backtest(), {
    revalidateOnFocus: false,
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Model performance</h1>
        <p className="text-sm text-muted mt-1">
          Honest evaluation of how well our predictions match real outcomes.
          Numbers are computed against completed games from the most recent
          NFL seasons. Lower MAE = closer predictions; higher accuracy = more
          correct picks; Brier score near 0 = better-calibrated probabilities.
        </p>
      </div>

      {isLoading || !data ? (
        <Card>
          <p className="text-sm text-muted">Computing backtest across 5 seasons…</p>
        </Card>
      ) : (
        <>
          <EloPerformance backtest={data.elo} />
          <CalibrationCard backtest={data.elo} />
          <MLPerformance backtest={data.ml} />
          <GlossaryCard />
        </>
      )}
    </div>
  );
}

function EloPerformance({ backtest }: { backtest: EloBacktest }) {
  return (
    <Card
      title="Elo predictions — lifetime"
      action={<span className="text-[11px] text-muted">{backtest.n_games} games · {backtest.seasons.length} seasons</span>}
    >
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
        <BigStat label="Spread MAE" value={`${backtest.overall.spread_mae} pts`} hint="lower is better" />
        <BigStat label="Pick accuracy" value={`${backtest.overall.classifier_accuracy_pct}%`} hint="did the model pick the winner" />
        <BigStat label="High-conf accuracy" value={backtest.overall.high_confidence_accuracy_pct != null ? `${backtest.overall.high_confidence_accuracy_pct}%` : "—"} hint={`${backtest.overall.high_confidence_n} games where model was ≥60% confident`} />
        <BigStat label="Brier score" value={backtest.overall.brier_score.toFixed(3)} hint="0 is perfect; lower = better-calibrated" />
      </div>

      {backtest.overall.ats_picks_n > 0 && (
        <div className="panel p-3 mb-5">
          <div className="text-xs uppercase tracking-wide text-muted mb-1">Against the closing spread</div>
          <div className="text-sm">
            Model agreed with the close on{" "}
            <span className="font-semibold tabular-nums">{backtest.overall.ats_correct_pct}%</span>{" "}
            of cover outcomes across {backtest.overall.ats_picks_n} games (where line data was available).{" "}
            <span className="text-muted text-xs">
              {backtest.overall.ats_correct_pct != null && backtest.overall.ats_correct_pct >= 52.4 ? "Beating the breakeven juice ✓" : "Below breakeven juice (52.4%)"}
            </span>
          </div>
        </div>
      )}

      <h3 className="text-xs uppercase tracking-wide text-muted mb-2">Per-season breakdown</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-muted">
            <tr>
              <th className="py-1 pr-3">Season</th>
              <th className="pr-3 text-right">Games</th>
              <th className="pr-3 text-right">Spread MAE</th>
              <th className="pr-3 text-right">Pick %</th>
              <th className="pr-3 text-right">High-conf %</th>
              <th className="pr-3 text-right">ATS %</th>
            </tr>
          </thead>
          <tbody>
            {backtest.per_season.map((s) => (
              <tr key={s.season} className="border-t divider">
                <td className="py-1 pr-3 font-medium">{s.season}</td>
                <td className="pr-3 text-right tabular-nums">{s.n_games}</td>
                <td className="pr-3 text-right tabular-nums">{s.spread_mae}</td>
                <td className="pr-3 text-right tabular-nums">{s.classifier_accuracy_pct}%</td>
                <td className="pr-3 text-right tabular-nums">
                  {s.high_confidence_accuracy_pct != null ? `${s.high_confidence_accuracy_pct}%` : "—"}
                </td>
                <td className="pr-3 text-right tabular-nums">
                  {s.ats_correct_pct != null ? `${s.ats_correct_pct}%` : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function CalibrationCard({ backtest }: { backtest: EloBacktest }) {
  const data = backtest.calibration
    .filter((c) => c.predicted_avg != null && c.actual_win_rate != null)
    .map((c) => ({
      predicted: (c.predicted_avg ?? 0) * 100,
      actual: (c.actual_win_rate ?? 0) * 100,
      n: c.n,
    }));

  return (
    <Card title="Calibration plot" action={<span className="text-[11px] text-muted">Perfect = diagonal</span>}>
      <p className="text-xs text-muted mb-3">
        For each predicted-probability bucket, the dot shows what fraction
        of those games the home team actually won. A well-calibrated model
        sits on the diagonal — when we say "70% chance," it happens ~70% of
        the time.
      </p>
      <ResponsiveContainer width="100%" height={300}>
        <ScatterChart margin={{ top: 12, right: 12, bottom: 8, left: 0 }}>
          <CartesianGrid stroke="rgba(255,255,255,0.06)" />
          <XAxis
            type="number" dataKey="predicted" name="predicted" domain={[0, 100]}
            tick={{ fill: "#94a3b8", fontSize: 11 }}
            label={{ value: "Predicted home win %", position: "insideBottom", offset: -4, fill: "#64748b", fontSize: 11 }}
          />
          <YAxis
            type="number" dataKey="actual" name="actual" domain={[0, 100]}
            tick={{ fill: "#94a3b8", fontSize: 11 }}
            label={{ value: "Actual win %", angle: -90, position: "insideLeft", fill: "#64748b", fontSize: 11 }}
          />
          <ReferenceLine
            segment={[{ x: 0, y: 0 }, { x: 100, y: 100 }]}
            stroke="rgba(255,255,255,0.25)" strokeDasharray="4 4"
          />
          <Tooltip
            cursor={{ strokeDasharray: "3 3" }}
            contentStyle={{ background: "var(--panel)", border: "1px solid var(--border)", color: "var(--text)", fontSize: 12 }}
            formatter={(v: number, name: string) => [`${v.toFixed(1)}%`, name]}
          />
          <Scatter data={data} fill="var(--team-primary)" />
        </ScatterChart>
      </ResponsiveContainer>
    </Card>
  );
}

function MLPerformance({ backtest }: { backtest: MLBacktest }) {
  if (!backtest.available) {
    return (
      <Card title="XGBoost model — out-of-sample">
        <p className="text-sm text-muted">
          ML evaluation unavailable: <span className="text-text">{backtest.reason}</span>
        </p>
      </Card>
    );
  }
  return (
    <Card
      title="XGBoost model — out-of-sample"
      action={
        <span className="text-[11px] text-muted">
          Trained on {backtest.train_seasons?.join(", ")} → tested on {backtest.test_season}
        </span>
      }
    >
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
        <BigStat label="Test set" value={`${backtest.n_test} games`} hint={`Trained on ${backtest.n_train}`} />
        <BigStat label="Spread MAE" value={`${backtest.spread_mae} pts`} hint="held-out season" />
        <BigStat label="RMSE" value={`${backtest.spread_rmse} pts`} />
        <BigStat label="Pick accuracy" value={`${backtest.classifier_accuracy_pct}%`} />
      </div>

      <h3 className="text-xs uppercase tracking-wide text-muted mb-2">Feature importance</h3>
      <ul className="space-y-1.5">
        {backtest.feature_importance?.map((f) => (
          <li key={f.feature} className="text-sm">
            <div className="flex justify-between text-xs mb-0.5">
              <span>{f.feature.replace(/_/g, " ")}</span>
              <span className="tabular-nums text-muted">{(f.importance * 100).toFixed(1)}%</span>
            </div>
            <div className="h-1.5 rounded bg-bg overflow-hidden border divider">
              <div
                className="h-full"
                style={{ width: `${Math.min(100, f.importance * 100)}%`, background: "var(--team-primary)" }}
              />
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function GlossaryCard() {
  return (
    <Card title="Glossary">
      <dl className="text-sm space-y-2">
        <Item term="Spread MAE">
          Mean absolute error of predicted point spread vs. actual margin.
          NFL sharp models typically land in the 10-13 point range.
        </Item>
        <Item term="Pick accuracy">
          Fraction of games where the model picked the actual winner. A coin flip is 50%; ESPN BPI / 538 typically score 60-66%.
        </Item>
        <Item term="High-conf accuracy">
          Pick accuracy on games where the model was at least 60% confident.
          A model that's "well-calibrated when confident" lands above 65%.
        </Item>
        <Item term="Brier score">
          Mean squared error of probabilistic predictions. Perfect = 0. The
          all-coin-flip baseline is 0.25. NFL Elo ≈ 0.20.
        </Item>
        <Item term="ATS %">
          Percentage of games where the model's pick agreed with the actual
          cover outcome against the closing line. Breakeven against -110 juice
          is 52.4%; sharp bettors aim for 55%+.
        </Item>
        <Item term="Out-of-sample">
          Tested on a season the model was never trained on. The only honest
          way to evaluate predictive accuracy.
        </Item>
      </dl>
    </Card>
  );
}

function BigStat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="panel p-3">
      <div className="text-xs text-muted">{label}</div>
      <div className="text-2xl font-bold tabular-nums">{value}</div>
      {hint && <div className="text-[10px] text-muted">{hint}</div>}
    </div>
  );
}

function Item({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-3">
      <dt className="font-medium w-32 flex-shrink-0">{term}</dt>
      <dd className="text-muted">{children}</dd>
    </div>
  );
}
