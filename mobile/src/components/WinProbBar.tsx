import { StyleSheet, View } from "react-native";
import { teamColor, pairColors } from "@/lib/team-colors";
import { colors } from "@/theme/theme";
import { Txt } from "./ui/Text";
import { pct } from "@/lib/format";

/**
 * Two-sided win-probability bar. Uses team colors, with pairColors() to keep
 * the two sides visually distinct when primaries are too close.
 */
export function WinProbBar({
  awayId,
  homeId,
  homeWinProb,
  height = 12,
}: {
  awayId: string | null | undefined;
  homeId: string | null | undefined;
  homeWinProb: number; // 0..1
  height?: number;
}) {
  const [awayCol, homeCol] = safePair(awayId, homeId);
  const homePctW = Math.max(0, Math.min(1, homeWinProb)) * 100;
  const awayPctW = 100 - homePctW;

  return (
    <View style={{ gap: 6 }}>
      <View style={[styles.bar, { height }]}>
        <View style={{ width: `${awayPctW}%`, backgroundColor: awayCol }} />
        <View style={{ width: `${homePctW}%`, backgroundColor: homeCol }} />
      </View>
      <View style={styles.labels}>
        <Txt variant="mono" style={{ color: awayCol }}>
          {awayId ?? "AWAY"} {pct(1 - homeWinProb)}
        </Txt>
        <Txt variant="mono" style={{ color: homeCol }}>
          {pct(homeWinProb)} {homeId ?? "HOME"}
        </Txt>
      </View>
    </View>
  );
}

function safePair(
  a: string | null | undefined,
  b: string | null | undefined,
): [string, string] {
  try {
    const pair = pairColors(a, b);
    if (pair?.away && pair?.home) return [pair.away, pair.home];
  } catch {
    /* fall through */
  }
  return [teamColor(a, colors.muted), teamColor(b, colors.accent)];
}

const styles = StyleSheet.create({
  bar: {
    flexDirection: "row",
    borderRadius: 6,
    overflow: "hidden",
    backgroundColor: colors.panelAlt,
  },
  labels: { flexDirection: "row", justifyContent: "space-between" },
});
