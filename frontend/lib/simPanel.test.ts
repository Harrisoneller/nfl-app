import assert from "node:assert/strict";
import { test } from "node:test";

import {
  SIM_PANEL_LABEL,
  SIM_PANEL_NOTE,
  SIM_PUBLIC_FIELDS,
  formatFrozenAt,
  formatSimHomeWp,
  formatSimMarginHome,
  formatSimTotal,
  simPanelCopy,
} from "./simPanel.ts";

const FORBIDDEN = [
  "odds",
  "edge",
  "pick",
  "moneyline",
  "recommendation",
  "vegas",
  "spread",
  "line",
];

test("sim panel copy has the research label and no market language", () => {
  assert.equal(SIM_PANEL_LABEL, "Sim (research, drive-level simulation)");
  assert.equal(SIM_PANEL_NOTE, "Experimental. Not the official projection.");
  const copy = simPanelCopy().toLowerCase();
  assert.match(copy, /experimental/);
  assert.match(copy, /not the official projection/);
  for (const word of FORBIDDEN) {
    assert.equal(copy.includes(word), false, word);
  }
});

test("sim formatters keep the home perspective and the public fields stay market-blind", () => {
  assert.equal(formatSimTotal(47.25), "47.3");
  assert.equal(formatSimMarginHome(3.1), "+3.1");
  assert.equal(formatSimMarginHome(-2), "-2.0");
  assert.equal(formatSimMarginHome(0), "0.0");
  assert.equal(formatSimHomeWp(0.612), "61.2%");
  assert.equal(formatFrozenAt("2026-11-20T18:05:00Z"), "2026-11-20 18:05 UTC");
  assert.deepEqual(SIM_PUBLIC_FIELDS.includes("sim_margin_home"), true);
  assert.equal(SIM_PUBLIC_FIELDS.includes("sim_total"), true);
  assert.equal(
    ["odds", "edge", "pick", "market"].some((word) =>
      SIM_PUBLIC_FIELDS.join(" ").includes(word),
    ),
    false,
  );
});
