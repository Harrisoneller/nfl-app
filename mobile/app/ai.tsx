import { useRef, useState } from "react";
import {
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  View,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/lib/api";
import { Txt } from "@/components/ui/Text";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";
import { colors, radius, spacing } from "@/theme/theme";

type Msg = { role: "user" | "assistant"; content: string };

const SUGGESTIONS = [
  "Who should I start at RB this week?",
  "Best bets for the upcoming slate?",
  "Compare the Eagles and 49ers defenses",
];

export default function AIScreen() {
  const insets = useSafeAreaInsets();
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const sessionId = useRef<string | undefined>(undefined);
  const scrollRef = useRef<ScrollView>(null);

  async function send(text?: string) {
    const message = (text ?? input).trim();
    if (!message || busy) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: message }]);
    setBusy(true);
    try {
      const res = await api.chat({ message, session_id: sessionId.current, enable_tools: true });
      sessionId.current = res.session_id;
      setMessages((m) => [...m, { role: "assistant", content: res.content }]);
    } catch (e) {
      setMessages((m) => [
        ...m,
        { role: "assistant", content: `⚠️ ${e instanceof Error ? e.message : "Something went wrong."}` },
      ]);
    } finally {
      setBusy(false);
      requestAnimationFrame(() => scrollRef.current?.scrollToEnd({ animated: true }));
    }
  }

  return (
    <KeyboardAvoidingView
      style={styles.flex}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
      keyboardVerticalOffset={90}
    >
      <ScrollView
        ref={scrollRef}
        style={styles.flex}
        contentContainerStyle={styles.list}
        keyboardShouldPersistTaps="handled"
      >
        {messages.length === 0 ? (
          <View style={{ gap: spacing.md }}>
            <Txt variant="h2">Ask the NFL AI</Txt>
            <Txt variant="muted">
              Ask about matchups, start/sit calls, betting edges, or any stat. The assistant can pull
              live data from the app.
            </Txt>
            <View style={{ gap: spacing.sm, marginTop: spacing.sm }}>
              {SUGGESTIONS.map((s) => (
                <Button key={s} label={s} variant="secondary" onPress={() => send(s)} />
              ))}
            </View>
          </View>
        ) : (
          messages.map((m, i) => (
            <View
              key={i}
              style={[styles.bubble, m.role === "user" ? styles.user : styles.assistant]}
            >
              <Txt style={m.role === "user" ? styles.userText : styles.assistantText}>
                {m.content}
              </Txt>
            </View>
          ))
        )}
        {busy ? (
          <View style={[styles.bubble, styles.assistant]}>
            <Txt variant="muted">Thinking…</Txt>
          </View>
        ) : null}
      </ScrollView>

      <View style={[styles.inputBar, { paddingBottom: insets.bottom + spacing.sm }]}>
        <View style={{ flex: 1 }}>
          <Input
            placeholder="Ask anything NFL…"
            value={input}
            onChangeText={setInput}
            onSubmitEditing={() => send()}
            returnKeyType="send"
          />
        </View>
        <Button label="Send" onPress={() => send()} loading={busy} style={{ paddingHorizontal: 18 }} />
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.bg },
  list: { padding: spacing.lg, gap: spacing.md },
  bubble: {
    maxWidth: "88%",
    borderRadius: radius.lg,
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.md,
  },
  user: { alignSelf: "flex-end", backgroundColor: colors.accent },
  assistant: {
    alignSelf: "flex-start",
    backgroundColor: colors.panel,
    borderColor: colors.border,
    borderWidth: StyleSheet.hairlineWidth,
  },
  userText: { color: "#04222a", fontWeight: "600" },
  assistantText: { color: colors.text },
  inputBar: {
    flexDirection: "row",
    alignItems: "flex-end",
    gap: spacing.sm,
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm,
    borderTopColor: colors.border,
    borderTopWidth: StyleSheet.hairlineWidth,
    backgroundColor: colors.bgElevated,
  },
});
