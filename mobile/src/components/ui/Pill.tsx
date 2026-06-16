import { StyleSheet, View, ViewStyle } from "react-native";
import { colors, radius, spacing } from "@/theme/theme";
import { Txt } from "./Text";

/**
 * Small status/label chip. `tone` picks a semantic color; `color` overrides it
 * directly (e.g. a team or grade color). Rendered as a translucent fill.
 */
export function Pill({
  label,
  tone = "neutral",
  color,
  style,
}: {
  label: string;
  tone?: "neutral" | "positive" | "negative" | "warning" | "info" | "brand";
  color?: string;
  style?: ViewStyle;
}) {
  const base = color ?? toneColor[tone];
  return (
    <View
      style={[
        styles.pill,
        { backgroundColor: withAlpha(base, 0.16), borderColor: withAlpha(base, 0.4) },
        style,
      ]}
    >
      <Txt style={[styles.text, { color: base }]}>{label}</Txt>
    </View>
  );
}

const toneColor: Record<string, string> = {
  neutral: colors.muted,
  positive: colors.positive,
  negative: colors.negative,
  warning: colors.warning,
  info: colors.info,
  brand: colors.accent,
};

/** Append alpha to a #rrggbb hex. */
export function withAlpha(hex: string, alpha: number): string {
  const h = hex.replace("#", "");
  if (h.length !== 6) return hex;
  const a = Math.round(alpha * 255)
    .toString(16)
    .padStart(2, "0");
  return `#${h}${a}`;
}

const styles = StyleSheet.create({
  pill: {
    alignSelf: "flex-start",
    paddingHorizontal: spacing.sm,
    paddingVertical: 3,
    borderRadius: radius.pill,
    borderWidth: StyleSheet.hairlineWidth,
  },
  text: {
    fontSize: 11,
    fontWeight: "700",
  },
});
