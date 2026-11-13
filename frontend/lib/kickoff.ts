/** Format a real UTC kickoff in a viewer's zone. No second offset is applied. */

export type KickoffFields = {
  start_time?: string | null;
  gameday?: string | null;
  gametime?: string | null;
};

const CLOCK: Intl.DateTimeFormatOptions = {
  weekday: "short",
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
};

export function formatKickoffInstant(iso: string, timeZone?: string): string | null {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return new Intl.DateTimeFormat("en-US", {
    ...CLOCK,
    ...(timeZone ? { timeZone } : {}),
  }).format(d);
}

/** Eastern wall clock (legacy gameday + gametime) as a UTC instant. */
export function easternWallToUtc(gameday: string, gametime: string): string | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(gameday);
  const clock = /^(\d{1,2}):(\d{2})/.exec(gametime);
  if (!match || !clock) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(clock[1]);
  const minute = Number(clock[2]);
  // Probe the zone offset for that civil time. DST is the offset America/New_York
  // reports for the UTC guess, then we correct once.
  const utcGuess = Date.UTC(year, month - 1, day, hour, minute);
  // offset is NY wall minus UTC (negative). UTC = civil digits minus that offset.
  const offset = newYorkOffsetMinutes(new Date(utcGuess));
  return new Date(utcGuess - offset * 60_000).toISOString();
}

function newYorkOffsetMinutes(instant: Date): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(instant);
  const pick = (type: string) => Number(parts.find((p) => p.type === type)?.value);
  const asUtc = Date.UTC(pick("year"), pick("month") - 1, pick("day"), pick("hour"), pick("minute"), pick("second"));
  return Math.round((asUtc - instant.getTime()) / 60_000);
}

/**
 * Prefer the UTC instant. A legacy payload without one is Eastern wall clock,
 * converted once. Passing both does not apply gametime on top of start_time.
 */
export function formatScheduleKickoff(game: KickoffFields, timeZone?: string): string | null {
  if (game.start_time) {
    return formatKickoffInstant(game.start_time, timeZone);
  }
  if (game.gameday && game.gametime) {
    const iso = easternWallToUtc(game.gameday, game.gametime);
    return iso ? formatKickoffInstant(iso, timeZone) : null;
  }
  if (!game.gameday) return null;
  const day = new Date(`${game.gameday}T12:00:00Z`);
  if (Number.isNaN(day.getTime())) return game.gameday;
  return new Intl.DateTimeFormat("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(day);
}
