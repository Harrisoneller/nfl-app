"use client";
import { useEffect, useRef, useState } from "react";
import { api, Player, Team } from "@/lib/api";

export type Pickable =
  | { kind: "team"; id: string; label: string; color: string }
  | { kind: "player"; id: string; label: string; color: string; position: string };

const MAX_OVERLAYS = 3;

/**
 * Autocomplete picker for adding teams or players to a comparison overlay.
 * Shows currently-selected items as removable chips.
 */
export function ComparisonPicker({
  kind,
  selected,
  onChange,
  excludeId,
}: {
  kind: "team" | "player";
  selected: Pickable[];
  onChange: (items: Pickable[]) => void;
  excludeId?: string;
}) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<(Team | Player)[]>([]);
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  // Click-outside closes the dropdown
  useEffect(() => {
    const click = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("click", click);
    return () => window.removeEventListener("click", click);
  }, []);

  // Search as user types (300ms debounce-ish via setTimeout)
  useEffect(() => {
    if (!q || q.length < 2) {
      setResults([]);
      return;
    }
    let cancelled = false;
    const t = setTimeout(async () => {
      try {
        if (kind === "team") {
          const teams = await api.listTeams();
          const ql = q.toLowerCase();
          const matched = teams.filter(
            (t) =>
              t.full_name.toLowerCase().includes(ql) ||
              t.id.toLowerCase() === ql,
          );
          if (!cancelled) setResults(matched.slice(0, 8));
        } else {
          const players = await api.listPlayers({ query: q, limit: 8 });
          if (!cancelled) setResults(players);
        }
      } catch {
        if (!cancelled) setResults([]);
      }
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [q, kind]);

  const add = (item: Team | Player) => {
    if (selected.length >= MAX_OVERLAYS) return;
    const id = item.id;
    if (id === excludeId || selected.some((s) => s.id === id)) return;
    const next: Pickable =
      kind === "team"
        ? {
            kind: "team",
            id,
            label: (item as Team).id,
            color: (item as Team).primary_color,
          }
        : {
            kind: "player",
            id,
            label: (item as Player).full_name,
            color: "",  // filled in by parent from palette
            position: (item as Player).position,
          };
    onChange([...selected, next]);
    setQ("");
    setOpen(false);
  };

  const remove = (id: string) => onChange(selected.filter((s) => s.id !== id));

  return (
    <div ref={wrapRef} className="relative">
      <div className="flex flex-wrap gap-2 items-center">
        {selected.map((s) => (
          <span
            key={s.id}
            className="inline-flex items-center gap-1.5 text-xs px-2 py-1 rounded border divider"
            style={{ borderColor: s.color || undefined }}
          >
            <span
              className="inline-block w-2 h-2 rounded-full"
              style={{ background: s.color || "#94a3b8" }}
            />
            {s.label}
            <button onClick={() => remove(s.id)} className="text-muted hover:text-text">×</button>
          </span>
        ))}
        {selected.length < MAX_OVERLAYS && (
          <div className="relative">
            <input
              value={q}
              onChange={(e) => { setQ(e.target.value); setOpen(true); }}
              onFocus={() => setOpen(true)}
              placeholder={kind === "team" ? "+ compare team…" : "+ compare player…"}
              className="bg-bg border divider rounded px-2 py-1 text-xs w-44"
            />
            {open && results.length > 0 && (
              <ul className="absolute z-20 mt-1 w-64 max-h-64 overflow-y-auto panel text-sm">
                {results.map((r) => (
                  <li key={r.id}>
                    <button
                      onClick={() => add(r)}
                      className="w-full text-left px-3 py-1.5 hover:bg-bg flex items-center gap-2"
                    >
                      {kind === "team" ? (
                        <>
                          <span
                            className="inline-block w-2 h-2 rounded-sm"
                            style={{ background: (r as Team).primary_color }}
                          />
                          <span>{(r as Team).full_name}</span>
                          <span className="text-muted text-xs ml-auto">{(r as Team).id}</span>
                        </>
                      ) : (
                        <>
                          <span>{(r as Player).full_name}</span>
                          <span className="text-muted text-xs ml-auto">
                            {(r as Player).position} · {(r as Player).team_id ?? "—"}
                          </span>
                        </>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
      {selected.length >= MAX_OVERLAYS && (
        <p className="text-xs text-muted mt-1">Max {MAX_OVERLAYS} overlays.</p>
      )}
    </div>
  );
}
