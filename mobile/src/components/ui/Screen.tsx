import { ReactNode } from "react";
import {
  RefreshControl,
  ScrollView,
  StyleSheet,
  View,
  ViewStyle,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { colors, spacing } from "@/theme/theme";

/**
 * Standard screen wrapper: dark background, safe-area aware, scrollable with
 * optional pull-to-refresh. Most screens render their content as children.
 */
export function Screen({
  children,
  onRefresh,
  refreshing = false,
  scroll = true,
  contentStyle,
}: {
  children: ReactNode;
  onRefresh?: () => void;
  refreshing?: boolean;
  scroll?: boolean;
  contentStyle?: ViewStyle;
}) {
  const insets = useSafeAreaInsets();
  const pad: ViewStyle = {
    padding: spacing.lg,
    paddingBottom: spacing.xxl + insets.bottom + 72, // clear the tab bar
    gap: spacing.md,
  };

  if (!scroll) {
    return <View style={[styles.bg, pad, contentStyle]}>{children}</View>;
  }

  return (
    <ScrollView
      style={styles.bg}
      contentContainerStyle={[pad, contentStyle]}
      keyboardShouldPersistTaps="handled"
      indicatorStyle="white"
      refreshControl={
        onRefresh ? (
          <RefreshControl
            refreshing={refreshing}
            onRefresh={onRefresh}
            tintColor={colors.muted}
          />
        ) : undefined
      }
    >
      {children}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  bg: { flex: 1, backgroundColor: colors.bg },
});
