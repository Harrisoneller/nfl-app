import "react-native-gesture-handler";
import { useEffect } from "react";
import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { SafeAreaProvider } from "react-native-safe-area-context";
import * as SystemUI from "expo-system-ui";
import { AuthProvider } from "@/context/AuthProvider";
import { colors } from "@/theme/theme";

export default function RootLayout() {
  useEffect(() => {
    SystemUI.setBackgroundColorAsync(colors.bg).catch(() => {});
  }, []);

  return (
    <GestureHandlerRootView style={{ flex: 1, backgroundColor: colors.bg }}>
      <SafeAreaProvider>
        <AuthProvider>
          <StatusBar style="light" />
          <Stack
            screenOptions={{
              headerStyle: { backgroundColor: colors.bg },
              headerTitleStyle: { color: colors.textBright, fontWeight: "700" },
              headerTintColor: colors.accent,
              headerShadowVisible: false,
              contentStyle: { backgroundColor: colors.bg },
            }}
          >
            <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
            <Stack.Screen name="teams/[id]" options={{ title: "Team" }} />
            <Stack.Screen name="players/index" options={{ title: "Players" }} />
            <Stack.Screen name="players/[id]" options={{ title: "Player" }} />
            <Stack.Screen name="h2h/[a]/[b]" options={{ title: "Head to Head" }} />
            <Stack.Screen name="sparky/[eventId]" options={{ title: "Game Detail" }} />
            <Stack.Screen name="compare" options={{ title: "Compare" }} />
            <Stack.Screen name="fantasy" options={{ title: "Fantasy" }} />
            <Stack.Screen name="ai" options={{ title: "Ask AI" }} />
            <Stack.Screen name="account" options={{ title: "Account" }} />
            <Stack.Screen
              name="login"
              options={{ title: "Sign in", presentation: "modal" }}
            />
            <Stack.Screen
              name="register"
              options={{ title: "Create account", presentation: "modal" }}
            />
          </Stack>
        </AuthProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}
