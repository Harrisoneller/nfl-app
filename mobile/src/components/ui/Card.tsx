import { ReactNode } from "react";
import {
  Pressable,
  StyleSheet,
  View,
  ViewStyle,
} from "react-native";
import { colors, radius, shadow, spacing } from "@/theme/theme";

/**
 * Glass-style surface. If `onPress` is provided it becomes a pressable with a
 * subtle press state — the mobile analogue of the web's hover lift.
 */
export function Card({
  children,
  onPress,
  style,
  padded = true,
  accent,
}: {
  children: ReactNode;
  onPress?: () => void;
  style?: ViewStyle;
  padded?: boolean;
  accent?: string; // optional left accent stripe (e.g. team color)
}) {
  const base: ViewStyle = {
    backgroundColor: colors.panel,
    borderColor: colors.border,
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: radius.lg,
    padding: padded ? spacing.lg : 0,
    overflow: "hidden",
    ...shadow.card,
  };

  const content = (
    <>
      {accent ? (
        <View style={[styles.accent, { backgroundColor: accent }]} />
      ) : null}
      {children}
    </>
  );

  if (onPress) {
    return (
      <Pressable
        onPress={onPress}
        style={({ pressed }) => [
          base,
          accent ? { paddingLeft: spacing.lg + 4 } : null,
          pressed ? styles.pressed : null,
          style,
        ]}
      >
        {content}
      </Pressable>
    );
  }

  return (
    <View style={[base, accent ? { paddingLeft: spacing.lg + 4 } : null, style]}>
      {content}
    </View>
  );
}

const styles = StyleSheet.create({
  pressed: { opacity: 0.7, transform: [{ scale: 0.99 }] },
  accent: {
    position: "absolute",
    left: 0,
    top: 0,
    bottom: 0,
    width: 4,
  },
});
