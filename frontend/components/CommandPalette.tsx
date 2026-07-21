"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api, Player, Team } from "@/lib/api";

type Hit =
  | { kind: "team"; id: string; label: string; sub: string; color: string }
  | { kind: "player"; id: string; label: string; sub: string; color: string }
  | { kind: "nav"; id: string; label: string; sub: string; color: string };

// /players, /compare, and /performance are temporarily hidden — see Nav.tsx.
const NAV_ITEMS: Hit[] = [
  { kind: "nav", id: "/", label: "Home", sub: "Scores + news + widgets", color: "#94a3b8" },
  { kind: "nav", id: "/teams", label: "Teams", sub: "All 32, grouped by division", color: "#94a3b8" },
  { kind: "nav", id: "/odds", label: "Odds", sub: "Sportsbook markets", color: "#94a3b8" },
  { kind: "nav", id: "/bets", label: "My Bets", sub: "Your bet log + closing-line value", color: "#94a3b8" },
  // /fantasy and /ai hidden until ready — see Nav.tsx
];

/**
 * Cmd+K (or Ctrl+K) command palette. Global search across teams, players,
 * and quick-nav. Renders as a centered modal.
 */
export function CommandPalette() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [results, setResults] = useState<Hit[]>(NAV_ITEMS);
  const [activeIdx, setActiveIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  // Toggle on Cmd/Ctrl+K
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const k = e.key.toLowerCase();
      if ((e.metaKey || e.ctrlKey) && k === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Focus input when opened
  useEffect(() => {
    if (open) {
      setQ("");
      setResults(NAV_ITEMS);
      setActiveIdx(0);
      setTimeout(() => inputRef.current?.focus(), 10);
    }
  }, [open]);

  // Run search as user types
  useEffect(() => {
    if (!open) return;
    if (!q || q.length < 2) {
      setResults(NAV_ITEMS);
      return;
    }
    let cancelled = false;
    const t = setTimeout(async () => {
      try {
        // Player search hidden alongside /players nav. Re-add to results when
        // the players section is re-enabled.
        const teams = await api.listTeams().catch(() => [] as Team[]);
        const ql = q.toLowerCase();
        const teamHits: Hit[] = teams
          .filter((t) => t.full_name.toLowerCase().includes(ql) || t.id.toLowerCase() === ql)
          .slice(0, 8)
          .map((t) => ({
            kind: "team", id: `/teams/${t.id}`, label: t.full_name,
            sub: `Team · ${t.conference} ${t.division}`, color: t.primary_color,
          }));
        const navHits: Hit[] = NAV_ITEMS.filter((n) => n.label.toLowerCase().includes(ql)).slice(0, 3);
        if (!cancelled) {
          setResults([...navHits, ...teamHits]);
          setActiveIdx(0);
        }
      } catch {
        if (!cancelled) setResults([]);
      }
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [q, open]);

  const go = (hit: Hit) => {
    router.push(hit.id);
    setOpen(false);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIdx((i) => Math.min(i + 1, results.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIdx((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      const hit = results[activeIdx];
      if (hit) go(hit);
    }
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh] bg-black/60 backdrop-blur-sm"
      onClick={() => setOpen(false)}
    >
      <div
        className="panel w-full max-w-xl mx-4 overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="border-b divider px-3 py-2">
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Search teams, players, or jump to a page…"
            className="w-full bg-transparent outline-none text-sm py-2"
          />
        </div>
        <ul className="max-h-96 overflow-y-auto">
          {results.length === 0 && (
            <li className="px-4 py-6 text-sm text-muted text-center">No results</li>
          )}
          {results.map((r, i) => (
            <li key={`${r.kind}-${r.id}`}>
              <button
                onClick={() => go(r)}
                className={`w-full text-left px-4 py-2 flex items-center gap-3 text-sm transition-colors
                  ${i === activeIdx ? "bg-bg" : "hover:bg-bg/50"}`}
              >
                <span
                  className="inline-block w-2 h-2 rounded-sm"
                  style={{ background: r.color }}
                />
                <span className="flex-1">{r.label}</span>
                <span className="text-xs text-muted">{r.sub}</span>
              </button>
            </li>
          ))}
        </ul>
        <div className="border-t divider px-3 py-1.5 flex items-center gap-3 text-[11px] text-muted">
          <span>↑↓ navigate</span>
          <span>↵ select</span>
          <span>esc close</span>
          <span className="ml-auto">⌘K / ctrl+K to toggle</span>
        </div>
      </div>
    </div>
  );
}
