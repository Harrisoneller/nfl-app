"use client";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { Card } from "@/components/Card";
import { MarkdownContent } from "@/components/MarkdownContent";
import { useToast } from "@/components/Toast";

type Msg = { role: "user" | "assistant"; content: string };

const SUGGESTIONS = [
  "Compare PHI and SF passing efficiency in 2024",
  "Which teams have the best red-zone defenses?",
  "Who leads the league in receiving yards this season?",
  "Predict the Eagles' chances to win the NFC East",
  "Show me the top QBs by EPA per play",
];

export default function AiPage() {
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<Msg[]>([]);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [loading, setLoading] = useState(false);
  const toast = useToast();
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to the bottom of the conversation as new messages land.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, loading]);

  async function send(text?: string) {
    const content = (text ?? input).trim();
    if (!content) return;
    setLoading(true);
    const userMsg: Msg = { role: "user", content };
    setMessages((m) => [...m, userMsg]);
    setInput("");
    try {
      const r = await api.chat({ message: content, session_id: sessionId });
      setSessionId(r.session_id);
      setMessages((m) => [...m, { role: "assistant", content: r.content }]);
    } catch (e: any) {
      const msg = String(e?.message ?? e);
      const isLimit = msg.includes("429");
      toast.push(isLimit ? "Rate limit or budget reached. Try again later." : msg, "error");
      setMessages((m) => [...m, { role: "assistant", content: `_Error: ${msg}_` }]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="max-w-3xl mx-auto space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">Ask anything NFL</h1>
        <p className="text-sm text-muted mt-1">
          Backed by live data — stats, scores, schedules, news. Ask about
          teams, players, matchups, or trends.
        </p>
      </div>

      <Card>
        <div ref={scrollRef} className="space-y-4 max-h-[60vh] overflow-y-auto pr-1">
          {messages.length === 0 && (
            <div className="space-y-3">
              <p className="text-sm text-muted">Try one of these to get started:</p>
              <div className="flex flex-wrap gap-2">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => send(s)}
                    className="text-xs px-3 py-1.5 rounded-full border divider hover:border-team-primary hover:bg-bg transition-colors"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={m.role === "user" ? "panel p-3 bg-bg/40" : ""}>
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
              <span className="inline-block w-2 h-2 rounded-full bg-team-primary animate-pulse" />
              Thinking…
            </div>
          )}
        </div>
        <div className="mt-4 flex gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send()}
            placeholder="Ask anything NFL…"
            className="flex-1 bg-bg border divider rounded px-3 py-2 text-sm"
            disabled={loading}
          />
          <button
            onClick={() => send()}
            disabled={loading || !input.trim()}
            className="bg-team-primary text-white text-sm rounded px-4 py-2 disabled:opacity-50"
          >
            Send
          </button>
        </div>
      </Card>
    </div>
  );
}
