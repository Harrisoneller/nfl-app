import { Text as RNText, TextProps, StyleSheet } from "react-native";
import { colors, font } from "@/theme/theme";

type Variant = "h1" | "h2" | "h3" | "body" | "muted" | "label" | "mono";

const map: Record<Variant, object> = {
  h1: { fontSize: font.size.xxl, fontWeight: font.weight.heavy, color: colors.textBright },
  h2: { fontSize: font.size.lg, fontWeight: font.weight.bold, color: colors.textBright },
  h3: { fontSize: font.size.md, fontWeight: font.weight.semibold, color: colors.text },
  body: { fontSize: font.size.base, fontWeight: font.weight.regular, color: colors.text },
  muted: { fontSize: font.size.sm, fontWeight: font.weight.regular, color: colors.muted },
  label: {
    fontSize: font.size.xs,
    fontWeight: font.weight.semibold,
    color: colors.mutedDim,
    textTransform: "uppercase",
    letterSpacing: 0.6,
  },
  mono: {
    fontSize: font.size.sm,
    fontWeight: font.weight.semibold,
    color: colors.text,
    fontVariant: ["tabular-nums"],
  },
};

export function Txt({
  variant = "body",
  style,
  ...rest
}: TextProps & { variant?: Variant }) {
  return <RNText {...rest} style={[styles.base, map[variant], style]} />;
}

const styles = StyleSheet.create({
  base: {},
});
