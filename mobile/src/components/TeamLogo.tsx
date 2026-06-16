import { useEffect, useState } from "react";
import { Image, StyleSheet, View } from "react-native";
import { teamColor } from "@/lib/team-colors";
import { Txt } from "./ui/Text";
import { colors } from "@/theme/theme";

const ESPN_LOGO = (id: string) =>
  `https://a.espncdn.com/i/teamlogos/nfl/500/${id.toLowerCase()}.png`;

/**
 * Team logo with graceful fallback to a team-colored badge with the 3-letter
 * abbreviation. Mirrors the web TeamLogo (ESPN CDN URL pattern).
 */
export function TeamLogo({
  teamId,
  size = 28,
}: {
  teamId: string | null | undefined;
  size?: number;
}) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [teamId]);

  if (failed || !teamId) {
    return (
      <View
        style={[
          styles.fallback,
          {
            width: size,
            height: size,
            borderRadius: size / 2,
            backgroundColor: teamColor(teamId, colors.panelAlt),
          },
        ]}
      >
        <Txt style={{ fontSize: size * 0.34, fontWeight: "800", color: "#fff" }}>
          {teamId ? teamId.slice(0, 3) : "—"}
        </Txt>
      </View>
    );
  }

  return (
    <Image
      source={{ uri: ESPN_LOGO(teamId) }}
      style={{ width: size, height: size }}
      onError={() => setFailed(true)}
      resizeMode="contain"
    />
  );
}

const styles = StyleSheet.create({
  fallback: {
    alignItems: "center",
    justifyContent: "center",
  },
});
