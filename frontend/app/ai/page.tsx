"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { Card } from "@/components/Card";
import { MarkdownContent } from "@/components/MarkdownContent";
import { WidgetRenderer } from "@/components/widgets/WidgetRenderer";
import { useToast } from "@/components/Toast";

type Msg = { role: "user" | "assistant"; content: string };

export default function AiPage() {
  const [mode, setMode] = useState<"chat" | "widget">("chat");
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<Msg[]>([]);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [widget, setWidget] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const toast = useToast();

  async function send() {
    if (!input.trim()) return;
    setLoading(true);
    const userMsg: Msg = { role: "user", content: input };
    setMessages((m) => [...m, userMsg]);
    setInput("");
    try {
      if (mode === "chat") {
        const r = await api.chat({ message: userMsg.content, session_id: sessionId });
        setSessionId(r.session_id);
        if (r.widget) setWidget(r.widget);
        setMessages((m) => [...m, { role: "assistant", content: r.content }]);
      } else {
        const spec = await api.buildWidget(userMsg.content, true);
        setWidget(spec);
        setMessages((m) => [
          ...m,
          { role: "assistant", content: `Built widget: **${(spec as any).title}**` },
        ]);
      }
    } catch (e: any) {
      const msg = String(e?.message ?? e);
      // 429 = budget or rate limit; surface that distinctly
      const isLimit = msg.includes("429");
      toast.push(isLimit ? "Rate limit or budget reached. Try again later." : msg, "error");
      setMessages((m) => [...m, { role: "assistant", content: `_Error: ${msg}_` }]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
      <div className="lg:col-span-2 space-y-3">
        <Card>
          <div className="flex gap-2 mb-3">
            <button
              onClick={() => setMode("chat")}
              className={`px-3 py-1.5 text-sm rounded ${mode === "chat" ? "bg-team-primary text-white" : "bg-bg border divider"}`}
            >
              Ask anything
            </button>
            <button
              onClick={() => setMode("widget")}
              className={`px-3 py-1.5 text-sm rounded ${mode === "widget" ? "bg-team-primary text-white" : "bg-bg border divider"}`}
            >
              Build a widget
            </button>
          </div>
          <div className="space-y-4 max-h-[60vh] overflow-y-auto">
            {messages.length === 0 && (
              <p className="text-sm text-muted">
                {mode === "chat"
                  ? "Try: \"Compare Eagles and 49ers passing efficiency in 2024\" or \"Who leads the league in receiving yards?\""
                  : "Try: \"Show me a comparison table of QB rushing yards for the top 5 NFC quarterbacks in 2024\""}
              </p>
            )}
            {messages.map((m, i) => (
              <div
                key={i}
                className={`${m.role === "user" ? "bg-bg/50 panel p-3" : ""}`}
              >
                <div className="text-xs uppercase tracking-wide text-muted mb-1">{m.role}</div>
                {m.role === "assistant" ? (
                  <MarkdownContent>{m.content}</MarkdownContent>
                ) : (
                  <div className="text-sm whitespace-pre-wrap">{m.content}</div>
                )}
              </div>
            ))}
            {loading && (
              <div className="flex items-center gap-2 text-sm text-muted">
                <span className="inline-block w-2 h-2 rounded-full bg-team-primary animate-pulse"></span>
                Thinking…
              </div>
            )}
          </div>
          <div className="mt-3 flex gap-2">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && send()}
              placeholder={mode === "chat" ? "Ask anything NFL…" : "Describe the widget you want…"}
              className="flex-1 bg-bg border divider rounded px-3 py-2 text-sm"
            />
            <button onClick={send} disabled={loading} className="bg-team-primary text-white text-sm rounded px-4 py-2 disabled:opacity-50">
              Send
            </button>
          </div>
        </Card>
      </div>

      <div className="space-y-3">
        {widget ? (
          <WidgetRenderer spec={widget} />
        ) : (
          <Card title="Generated widget">
            <p className="text-sm text-muted">
              Widgets created by the AI appear here, and are also saved to your dashboard.
            </p>
          </Card>
        )}
      </div>
    </div>
  );
}
