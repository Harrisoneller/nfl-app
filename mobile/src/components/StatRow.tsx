import { StyleSheet, View } from "react-native";
import { colors, spacing } from "@/theme/theme";
import { Txt } from "./ui/Text";

/** Label/value row with an optional percentile bar. Used on profile screens. */
export function StatRow({
  label,
  value,
  percentile,
  higherIsBetter = true,
}: {
  label: string;
  value: string;
  percentile?: number | null;
  higherIsBetter?: boolean;
}) {
  return (
    <View style={styles.row}>
      <View style={styles.head}>
        <Txt variant="muted" style={{ flex: 1 }}>
          {label}
        </Txt>
        <Txt variant="mono">{value}</Txt>
      </View>
      {percentile != null ? (
        <View style={styles.track}>
          <View
            style={[
              styles.fill,
              {
                width: `${Math.max(2, Math.min(100, percentile))}%`,
                backgroundColor: percentileColor(percentile, higherIsBetter),
              },
            ]}
          />
        </View>
      ) : null}
    </View>
  );
}

function percentileColor(p: number, higherIsBetter: boolean): string {
  const eff = higherIsBetter ? p : 100 - p;
  if (eff >= 70) return colors.positive;
  if (eff >= 40) return colors.warning;
  return colors.negative;
}

const styles = StyleSheet.create({
  row: { gap: 6, paddingVertical: 6 },
  head: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  track: {
    height: 6,
    borderRadius: 3,
    backgroundColor: colors.panelAlt,
    overflow: "hidden",
  },
  fill: { height: "100%", borderRadius: 3 },
});
