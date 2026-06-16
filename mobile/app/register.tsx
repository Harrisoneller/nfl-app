import { useState } from "react";
import { View } from "react-native";
import { router, Link } from "expo-router";
import { useAuth } from "@/context/AuthProvider";
import { Screen } from "@/components/ui/Screen";
import { Txt } from "@/components/ui/Text";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";
import { colors, spacing } from "@/theme/theme";

export default function RegisterScreen() {
  const { register } = useAuth();
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (!email || !password) return;
    setBusy(true);
    setError(null);
    try {
      await register(email.trim(), password, displayName.trim() || undefined);
      router.replace("/(tabs)");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Sign up failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen>
      <Txt variant="h1">Create account</Txt>
      <Txt variant="muted">Join Statletics to track your bets and closing-line value.</Txt>

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
        <Input label="Display name (optional)" value={displayName} onChangeText={setDisplayName} placeholder="Your name" />
        <Input
          label="Password"
          value={password}
          onChangeText={setPassword}
          placeholder="At least 8 characters"
          secureTextEntry
        />
        {error ? <Txt style={{ color: colors.negative }}>{error}</Txt> : null}
        <Button label="Create account" onPress={submit} loading={busy} />
        <Link href="/login" style={{ alignSelf: "center" }}>
          <Txt style={{ color: colors.accent }}>I already have an account</Txt>
        </Link>
      </View>
    </Screen>
  );
}
