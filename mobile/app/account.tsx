import { useEffect, useState } from "react";
import { Alert, View } from "react-native";
import { router } from "expo-router";
import { useAuth } from "@/context/AuthProvider";
import { Screen } from "@/components/ui/Screen";
import { Card } from "@/components/ui/Card";
import { Txt } from "@/components/ui/Text";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { Loading } from "@/components/ui/States";
import { spacing } from "@/theme/theme";

export default function AccountScreen() {
  const { user, loading, isAuthenticated, updateProfile, changePassword, logout } = useAuth();
  const [displayName, setDisplayName] = useState("");
  const [savingName, setSavingName] = useState(false);

  const [currentPw, setCurrentPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [savingPw, setSavingPw] = useState(false);

  useEffect(() => {
    if (user) setDisplayName(user.display_name ?? "");
  }, [user]);

  useEffect(() => {
    if (!loading && !isAuthenticated) router.replace("/login");
  }, [loading, isAuthenticated]);

  if (loading || !user) {
    return (
      <Screen>
        <Loading />
      </Screen>
    );
  }

  async function saveName() {
    setSavingName(true);
    try {
      await updateProfile(displayName.trim());
      Alert.alert("Saved", "Display name updated.");
    } catch (e) {
      Alert.alert("Couldn't save", e instanceof Error ? e.message : "Try again.");
    } finally {
      setSavingName(false);
    }
  }

  async function savePassword() {
    if (!currentPw || !newPw) return;
    setSavingPw(true);
    try {
      await changePassword(currentPw, newPw);
      setCurrentPw("");
      setNewPw("");
      Alert.alert("Done", "Password changed.");
    } catch (e) {
      Alert.alert("Couldn't change password", e instanceof Error ? e.message : "Try again.");
    } finally {
      setSavingPw(false);
    }
  }

  return (
    <Screen>
      <Card>
        <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
          <View>
            <Txt variant="h2">{user.display_name || "NFL fan"}</Txt>
            <Txt variant="muted">{user.email}</Txt>
          </View>
          {user.is_admin ? <Pill label="Admin" tone="brand" /> : null}
        </View>
      </Card>

      <Card>
        <Txt variant="label">Display name</Txt>
        <View style={{ gap: spacing.sm, marginTop: spacing.sm }}>
          <Input value={displayName} onChangeText={setDisplayName} placeholder="Your name" />
          <Button label="Save" onPress={saveName} loading={savingName} />
        </View>
      </Card>

      <Card>
        <Txt variant="label">Change password</Txt>
        <View style={{ gap: spacing.sm, marginTop: spacing.sm }}>
          <Input
            value={currentPw}
            onChangeText={setCurrentPw}
            placeholder="Current password"
            secureTextEntry
          />
          <Input value={newPw} onChangeText={setNewPw} placeholder="New password" secureTextEntry />
          <Button label="Update password" variant="secondary" onPress={savePassword} loading={savingPw} />
        </View>
      </Card>

      <Button
        label="Sign out"
        variant="danger"
        onPress={() => {
          logout();
          router.replace("/(tabs)");
        }}
      />
    </Screen>
  );
}
