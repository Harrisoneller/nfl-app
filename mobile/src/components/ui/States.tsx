import { ActivityIndicator, StyleSheet, View } from "react-native";
import { colors, spacing } from "@/theme/theme";
import { Txt } from "./Text";
import { Card } from "./Card";

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <View style={styles.center}>
      <ActivityIndicator color={colors.accent} />
      <Txt variant="muted" style={{ marginTop: spacing.sm }}>
        {label}
      </Txt>
    </View>
  );
}

export function ErrorState({
  message = "Something went wrong.",
}: {
  message?: string;
}) {
  return (
    <Card>
      <Txt style={{ color: colors.negative }}>{message}</Txt>
    </Card>
  );
}

export function EmptyState({
  title,
  subtitle,
}: {
  title: string;
  subtitle?: string;
}) {
  return (
    <Card>
      <Txt variant="h3">{title}</Txt>
      {subtitle ? (
        <Txt variant="muted" style={{ marginTop: spacing.xs }}>
          {subtitle}
        </Txt>
      ) : null}
    </Card>
  );
}

const styles = StyleSheet.create({
  center: {
    paddingVertical: spacing.xxl,
    alignItems: "center",
    justifyContent: "center",
  },
});
