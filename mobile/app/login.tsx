import { useState } from "react";
import { View } from "react-native";
import { router, Link } from "expo-router";
import { useAuth } from "@/context/AuthProvider";
import { Screen } from "@/components/ui/Screen";
import { Txt } from "@/components/ui/Text";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";
import { colors, spacing } from "@/theme/theme";

export default function LoginScreen() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (!email || !password) return;
    setBusy(true);
    setError(null);
    try {
      await login(email.trim(), password);
      router.back();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Sign in failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen>
      <Txt variant="h1">Welcome back</Txt>
      <Txt variant="muted">Sign in to track bets, save preferences, and sync across devices.</Txt>

      <View style={{ gap: spacing.md, marginTop: spacing.lg }}>
        <Input
          label="Email"
          value={email}
          onChangeText={setEmail}
          placeholder="you@example.com"
          autoCapitalize="none"
          keyboardType="email-address"
          autoCorrect={false}
        />
        <Input
          label="Password"
          value={password}
          onChangeText={setPassword}
          placeholder="••••••••"
          secureTextEntry
        />
        {error ? <Txt style={{ color: colors.negative }}>{error}</Txt> : null}
        <Button label="Sign in" onPress={submit} loading={busy} />
        <Link href="/register" style={{ alignSelf: "center" }}>
          <Txt style={{ color: colors.accent }}>Create an account</Txt>
        </Link>
      </View>
    </Screen>
  );
}
