import { ActivityIndicator, Pressable, StyleSheet, ViewStyle } from "react-native";
import { colors, radius, spacing } from "@/theme/theme";
import { Txt } from "./Text";

export function Button({
  label,
  onPress,
  variant = "primary",
  loading = false,
  disabled = false,
  style,
}: {
  label: string;
  onPress?: () => void;
  variant?: "primary" | "secondary" | "ghost" | "danger";
  loading?: boolean;
  disabled?: boolean;
  style?: ViewStyle;
}) {
  const isDisabled = disabled || loading;
  return (
    <Pressable
      onPress={onPress}
      disabled={isDisabled}
      style={({ pressed }) => [
        styles.base,
        variantStyle[variant],
        isDisabled && styles.disabled,
        pressed && !isDisabled && styles.pressed,
        style,
      ]}
    >
      {loading ? (
        <ActivityIndicator color={variant === "primary" ? "#04222a" : colors.text} />
      ) : (
        <Txt style={[styles.label, labelStyle[variant]]}>{label}</Txt>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  base: {
    height: 48,
    borderRadius: radius.md,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: spacing.lg,
    borderWidth: StyleSheet.hairlineWidth,
  },
  label: { fontSize: 15, fontWeight: "700" },
  disabled: { opacity: 0.5 },
  pressed: { opacity: 0.85 },
});

const variantStyle: Record<string, ViewStyle> = {
  primary: { backgroundColor: colors.accent, borderColor: colors.accent },
  secondary: { backgroundColor: colors.panel, borderColor: colors.border },
  ghost: { backgroundColor: "transparent", borderColor: "transparent" },
  danger: { backgroundColor: "transparent", borderColor: colors.negative },
};

const labelStyle: Record<string, object> = {
  primary: { color: "#04222a" },
  secondary: { color: colors.text },
  ghost: { color: colors.accent },
  danger: { color: colors.negative },
};
