import { Pressable, ScrollView, StyleSheet, View } from "react-native";
import { colors, radius, spacing } from "@/theme/theme";
import { Txt } from "./Text";

export type SegmentOption<T extends string> = { id: T; label: string };

/**
 * Horizontal segmented control / tab strip. Scrolls when options overflow.
 */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
}: {
  options: ReadonlyArray<SegmentOption<T>>;
  value: T;
  onChange: (id: T) => void;
}) {
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      contentContainerStyle={styles.row}
    >
      {options.map((o) => {
        const active = o.id === value;
        return (
          <Pressable
            key={o.id}
            onPress={() => onChange(o.id)}
            style={[styles.seg, active && styles.segActive]}
          >
            <Txt
              style={[styles.segText, active && styles.segTextActive]}
            >
              {o.label}
            </Txt>
          </Pressable>
        );
      })}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  row: { gap: spacing.sm, paddingVertical: 2 },
  seg: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderRadius: radius.pill,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    backgroundColor: colors.panelAlt,
  },
  segActive: {
    backgroundColor: colors.accent,
    borderColor: colors.accent,
  },
  segText: { color: colors.muted, fontSize: 13, fontWeight: "600" },
  segTextActive: { color: "#04222a" },
});
