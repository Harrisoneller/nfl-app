import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  formatHomeSpread,
  formatMarketLine,
  formatSpreadEdge,
  headlineIsConsistent,
  headlineNumbers,
  headlineSourceLabel,
  isMarketBlended,
} from "./headline.ts";

const model = {
  predicted_spread: -6,
  predicted_total: 44,
  predicted_home_score: 25,
  predicted_away_score: 19,
  home_win_prob: 0.64,
  away_win_prob: 0.36,
  model_version: "qb-ridge-v5",
  prediction_basis: "model_only",
};

test("headline numbers do not move when the market line changes", () => {
  const withShort = headlineNumbers(model);
  const withLong = headlineNumbers({
    ...model,
    market: { spread_home: -14, total: 57, consensus_home_prob: 0.8 },
  } as typeof model);
  assert.deepEqual(withShort, withLong);
  assert.equal(withShort.spread, -6);
  assert.equal(withShort.total, 44);
  assert.equal(withShort.homeWinProb, 0.64);
  assert.equal(headlineIsConsistent(model), true);
});

test("market line and edge stay separate, including pickem and missing", () => {
  assert.equal(formatHomeSpread(-6, "KC", "BUF"), "KC -6.0");
  assert.equal(formatHomeSpread(3.5, "KC", "BUF"), "BUF -3.5");
  assert.equal(formatHomeSpread(0, "KC", "BUF"), "PK");
  assert.equal(
    formatMarketLine({ spread_home: -3, total: 47.5, consensus_home_prob: 0.6 }, "KC", "BUF"),
    "KC -3.0 · O/U 47.5",
  );
  assert.equal(
    formatMarketLine({ spread_home: 0, total: null, consensus_home_prob: 0.5 }, "KC", "BUF"),
    "PK",
  );
  assert.equal(formatMarketLine(null, "KC", "BUF"), "Market line unavailable");
  assert.equal(
    formatMarketLine({ spread_home: null, total: null, consensus_home_prob: null }, "KC", "BUF"),
    "Market line unavailable",
  );
  assert.equal(
    formatMarketLine({ spread_home: null, total: 44, consensus_home_prob: 0.7 }, "KC", "BUF"),
    "KC 70% · O/U 44.0",
  );
  // Model -6 vs market -2.5 → edge -3.5 → model likes home.
  assert.equal(formatSpreadEdge(-3.5, "KC", "BUF"), "KC +3.5");
  assert.equal(formatSpreadEdge(2, "KC", "BUF"), "BUF +2.0");
  assert.equal(formatSpreadEdge(0, "KC", "BUF"), "Pick'em");
  assert.equal(formatSpreadEdge(null, "KC", "BUF"), null);
});

test("a blend or a v4 row is not labeled qb-ridge-v5", () => {
  assert.equal(headlineSourceLabel(model), "qb-ridge-v5");
  assert.equal(
    headlineSourceLabel({ prediction_basis: "market-blend-v1", model_version: "qb-ridge-v5" }),
    "Market blend",
  );
  assert.equal(isMarketBlended({ prediction_basis: "market-blend-v1" }), true);
  assert.equal(
    headlineSourceLabel({ prediction_basis: "model_only", model_version: "epa-elo-ctx-v4" }),
    "v4 fallback",
  );
  assert.equal(
    headlineSourceLabel({ prediction_basis: "admin_override", model_version: "qb-ridge-v5" }),
    "Admin override",
  );
  assert.equal(headlineSourceLabel({}), "Model");
});

test("headline surfaces do not re-blend the market into the pick", () => {
  const files = [
    "../components/predictions/PredictionCard.tsx",
    "../components/predictions/MarketBlendLine.tsx",
    "../components/predictions/WhyPredictionPanel.tsx",
    "../components/WeekSlateView.tsx",
    "../app/page.tsx",
    "../app/h2h/[a]/[b]/page.tsx",
    "../components/WeekSchedule.tsx",
  ];
  for (const rel of files) {
    const text = readFileSync(new URL(rel, import.meta.url), "utf8");
    assert.equal(/blend_prob|blend_value|market\.weight\s*\*/.test(text), false, rel);
    assert.equal(/\(\s*1\s*-\s*w\s*\)/.test(text), false, rel);
  }
  const card = readFileSync(new URL("../components/predictions/PredictionCard.tsx", import.meta.url), "utf8");
  assert.match(card, /headlineNumbers/);
  assert.match(card, /headlineSourceLabel/);
  const slate = readFileSync(new URL("../components/WeekSlateView.tsx", import.meta.url), "utf8");
  assert.match(slate, /formatHomeSpread/);
  assert.equal(/market anchor/.test(slate), false);
});
