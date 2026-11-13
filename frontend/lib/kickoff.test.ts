import assert from "node:assert/strict";
import { test } from "node:test";

import { easternWallToUtc, formatScheduleKickoff } from "./kickoff.ts";

test("1:00 PM ET in November is 12:00 PM Central", () => {
  const label = formatScheduleKickoff(
    { start_time: "2026-11-15T18:00:00Z", gameday: "2026-11-15", gametime: "13:00" },
    "America/Chicago",
  );
  assert.match(label ?? "", /12:00/);
  assert.match(label ?? "", /PM/);
  assert.doesNotMatch(label ?? "", /1:00/);
  assert.doesNotMatch(label ?? "", /6:00/);
});

test("1:00 PM ET in September (EDT) is also 12:00 PM Central", () => {
  const label = formatScheduleKickoff(
    { start_time: "2026-09-13T17:00:00Z" },
    "America/Chicago",
  );
  assert.match(label ?? "", /12:00/);
  assert.match(label ?? "", /PM/);
});

test("legacy Eastern digits convert once and are not treated as local or UTC", () => {
  const fromWall = formatScheduleKickoff(
    { gameday: "2026-11-15", gametime: "13:00" },
    "America/Chicago",
  );
  const fromInstant = formatScheduleKickoff(
    { start_time: "2026-11-15T18:00:00Z", gametime: "13:00" },
    "America/Chicago",
  );
  assert.equal(fromWall, fromInstant);
  assert.equal(easternWallToUtc("2026-11-15", "13:00"), "2026-11-15T18:00:00.000Z");
  assert.equal(easternWallToUtc("2026-09-13", "13:00"), "2026-09-13T17:00:00.000Z");
});
