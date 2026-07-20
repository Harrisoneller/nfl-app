"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { api, RankingEntryRow, RankingSetMeta } from "@/lib/api";
import { SeasonSelect } from "@/components/SeasonSelect";
import { FantasyRanksTab } from "./FantasyRanksTab";

/**
 * Custom ranking boards — admin-authored rankings fully independent of the
 * projection engine. Create named, format-tagged sets (PPR, Superflex,
 * Dynasty…), seed them from projections as a starting point, then reshape
 * holistically: drag rows, insert tier breaks, jot notes. Edits stay in a
 * private draft until Publish snapshots the board to the fantasy page.
 *
 * Tier semantics: tiers belong to board POSITIONS, not players — a dragged
 * row adopts the tier where it lands, and a tier break shifts everything
 * below it into a new tier. This keeps tiers non-decreasing (a server-side
 * invariant) without any bookkeeping while you shuffle.
 */

const FORMAT_LABELS: Record<string, string> = {
  ppr: "PPR",
  half_ppr: "Half PPR",
  standard: "Standard",
  superflex: "Superflex",
  two_qb: "2QB",
  dynasty: "Dynasty",
  best_ball: "Best Ball",
  custom: "Custom",
};

const POSITIONS = ["ALL", "QB", "RB", "WR", "TE"] as const;

type Row = RankingEntryRow;

export function RankingBoardsTab() {
  const [view, setView] = useState<"boards" | "legacy">("boards");
  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        {(
          [
            ["boards", "Custom boards"],
            ["legacy", "Projection pins (legacy)"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            onClick={() => setView(id)}
            className={`text-xs rounded px-3 py-1.5 border ${
              view === id
                ? "bg-white/10 border-white/20 font-semibold"
                : "divider text-muted hover:text-white"
            }`}
          >
            {label}
          </button>
        ))}
      </div>
      {view === "boards" ? <BoardsManager /> : <FantasyRanksTab />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Set management
// ---------------------------------------------------------------------------

function BoardsManager() {
  const [season, setSeason] = useState<number | undefined>(undefined);
  const [activeId, setActiveId] = useState<number | null>(null);

  const sets = useSWR(["admin-ranking-sets", season], () =>
    api.adminListRankingSets(season),
  );
  const list = sets.data?.sets ?? [];
  const formats = sets.data?.formats ?? Object.keys(FORMAT_LABELS);

  useEffect(() => {
    if (activeId != null && !list.some((s) => s.id === activeId)) {
      setActiveId(null);
    }
    if (activeId == null && list.length > 0) setActiveId(list[0].id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sets.data]);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        <SeasonSelect value={season} onChange={setSeason} />
        <div className="flex gap-1.5 flex-wrap">
          {list.map((s) => (
            <button
              key={s.id}
              onClick={() => setActiveId(s.id)}
              className={`text-xs rounded px-3 py-1.5 border flex items-center gap-1.5 ${
                activeId === s.id
                  ? "bg-team-primary text-white border-transparent"
                  : "bg-bg divider text-muted hover:text-white"
              }`}
            >
              {s.name}
              <SetBadge set={s} />
            </button>
          ))}
        </div>
        <NewSetButton
          formats={formats}
          season={season}
          onCreated={(s) => {
            sets.mutate();
            setActiveId(s.id);
          }}
        />
      </div>

      {sets.isLoading && (
        <div className="panel p-6 text-sm text-muted">Loading ranking sets…</div>
      )}
      {!sets.isLoading && list.length === 0 && (
        <div className="panel p-6 text-sm text-muted">
          No custom boards yet. Create one, seed it from projections, then
          reshape it into your own rankings.
        </div>
      )}

      {activeId != null && (
        <BoardEditor
          key={activeId}
          setId={activeId}
          formats={formats}
          onSetsChanged={() => sets.mutate()}
        />
      )}
    </div>
  );
}

function SetBadge({ set }: { set: RankingSetMeta }) {
  if (set.status === "published" && set.has_unpublished_changes) {
    return (
      <span className="text-[9px] font-bold uppercase text-amber-300" title="Published, but the draft has unpublished edits">
        edited
      </span>
    );
  }
  if (set.status === "published") {
    return (
      <span className="text-[9px] font-bold uppercase text-emerald-400" title={`Live on the fantasy page (v${set.version})`}>
        live
      </span>
    );
  }
  return (
    <span className="text-[9px] font-bold uppercase text-muted" title="Draft — not visible publicly">
      draft
    </span>
  );
}

function NewSetButton({
  formats,
  season,
  onCreated,
}: {
  formats: string[];
  season?: number;
  onCreated: (s: RankingSetMeta) => void;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [format, setFormat] = useState("ppr");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function create() {
    if (!name.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      const s = await api.adminCreateRankingSet({
        name: name.trim(),
        season,
        format,
      });
      setOpen(false);
      setName("");
      onCreated(s);
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="text-xs rounded px-3 py-1.5 border divider text-muted hover:text-white ml-auto"
      >
        + New board
      </button>
    );
  }
  return (
    <div className="flex items-center gap-2 ml-auto flex-wrap">
      <input
        autoFocus
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && create()}
        placeholder="Board name (e.g. Superflex Big Board)"
        className="bg-bg border divider rounded px-2 py-1.5 text-xs w-56"
      />
      <select
        value={format}
        onChange={(e) => setFormat(e.target.value)}
        className="bg-bg border divider rounded px-2 py-1.5 text-xs"
      >
        {formats.map((f) => (
          <option key={f} value={f}>
            {FORMAT_LABELS[f] ?? f}
          </option>
        ))}
      </select>
      <button
        onClick={create}
        disabled={busy || !name.trim()}
        className="text-xs rounded px-3 py-1.5 bg-team-primary text-white disabled:opacity-50"
      >
        {busy ? "…" : "Create"}
      </button>
      <button
        onClick={() => setOpen(false)}
        className="text-xs text-muted hover:text-white"
      >
        Cancel
      </button>
      {err && <span className="text-xs text-red-400">{err}</span>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Board editor
// ---------------------------------------------------------------------------

/** Move list[from] to position `to`, adopting the tier at the landing spot. */
function reinsert(rows: Row[], from: number, to: number): Row[] {
  if (from === to || from < 0 || from >= rows.length) return rows;
  const next = rows.slice();
  const [moved] = next.splice(from, 1);
  const clamped = Math.max(0, Math.min(to, next.length));
  next.splice(clamped, 0, moved);
  const adopted =
    clamped > 0 ? next[clamped - 1].tier : next[1]?.tier ?? moved.tier;
  next[clamped] = { ...moved, tier: adopted };
  return next.map((r, i) => ({ ...r, rank: i + 1 }));
}

function BoardEditor({
  setId,
  formats,
  onSetsChanged,
}: {
  setId: number;
  formats: string[];
  onSetsChanged: () => void;
}) {
  const detail = useSWR(["admin-ranking-set", setId], () =>
    api.adminGetRankingSet(setId),
  );

  const [rows, setRows] = useState<Row[] | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [posFilter, setPosFilter] = useState<(typeof POSITIONS)[number]>("ALL");
  const [search, setSearch] = useState("");
  const dragFrom = useRef<number | null>(null);
  const [dragOver, setDragOver] = useState<number | null>(null);

  useEffect(() => {
    if (detail.data && rows === null) {
      setRows(detail.data.entries);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail.data]);

  const set = detail.data;
  const board = rows ?? [];

  // Live positional rank (RB5, WR12…) derived from board order, keyed by
  // player_id. Recomputed on every reorder, so dragging a player up updates
  // their pos rank immediately — it's never stored, always a function of the
  // current ordering. Mirrors the public fantasy page's computation.
  const posRankById = useMemo(() => {
    const counts: Record<string, number> = {};
    const out: Record<string, number> = {};
    for (const r of board) {
      const pos = (r.position ?? "?").toUpperCase();
      counts[pos] = (counts[pos] ?? 0) + 1;
      out[r.player_id] = counts[pos];
    }
    return out;
  }, [board]);

  const filtered = posFilter !== "ALL" || search.trim() !== "";
  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    return board
      .map((r, i) => ({ r, i }))
      .filter(
        ({ r }) =>
          (posFilter === "ALL" || r.position === posFilter) &&
          (!q || (r.name ?? "").toLowerCase().includes(q)),
      );
  }, [board, posFilter, search]);

  const mutateAll = useCallback(() => {
    detail.mutate();
    onSetsChanged();
  }, [detail, onSetsChanged]);

  const apply = (next: Row[]) => {
    setRows(next.map((r, i) => ({ ...r, rank: i + 1 })));
    setDirty(true);
  };

  // --- board operations ----------------------------------------------------

  const moveTo = (from: number, to: number) => apply(reinsert(board, from, to));

  const addTierBreak = (index: number) =>
    apply(
      board.map((r, i) => (i >= index ? { ...r, tier: r.tier + 1 } : r)),
    );

  const removeTierBreak = (index: number) => {
    if (index <= 0) return;
    const delta = board[index].tier - board[index - 1].tier;
    if (delta <= 0) return;
    apply(
      board.map((r, i) => (i >= index ? { ...r, tier: r.tier - delta } : r)),
    );
  };

  const setNote = (index: number, note: string) =>
    apply(board.map((r, i) => (i === index ? { ...r, note } : r)));

  const removeRow = (index: number) =>
    apply(board.filter((_, i) => i !== index));

  const addPlayer = (p: { id: string; full_name: string; position: string | null; team_id: string | null }) => {
    if (board.some((r) => r.player_id === p.id)) return;
    apply([
      ...board,
      {
        player_id: p.id,
        rank: board.length + 1,
        tier: board.length ? board[board.length - 1].tier : 1,
        note: "",
        name: p.full_name,
        position: p.position,
        team: p.team_id,
      },
    ]);
  };

  // --- persistence ---------------------------------------------------------

  async function run(label: string, fn: () => Promise<unknown>) {
    setBusy(label);
    setErr(null);
    try {
      await fn();
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setBusy(null);
    }
  }

  const save = () =>
    run("save", async () => {
      const d = await api.adminReplaceRankingEntries(
        setId,
        board.map((r) => ({ player_id: r.player_id, tier: r.tier, note: r.note })),
      );
      setRows(d.entries);
      setDirty(false);
      mutateAll();
    });

  const seed = (source: string) =>
    run("seed", async () => {
      if (
        board.length > 0 &&
        !confirm("Seeding replaces the whole draft board. Continue?")
      ) {
        return;
      }
      const d = await api.adminSeedRankingSet(setId, { source, limit: 200 });
      setRows(d.entries);
      setDirty(false);
      mutateAll();
    });

  const publish = () =>
    run("publish", async () => {
      if (dirty) await save();
      await api.adminPublishRankingSet(setId);
      mutateAll();
    });

  const unpublish = () =>
    run("unpublish", async () => {
      await api.adminUnpublishRankingSet(setId);
      mutateAll();
    });

  const destroy = () =>
    run("delete", async () => {
      if (!confirm(`Delete "${set?.name}" permanently?`)) return;
      await api.adminDeleteRankingSet(setId);
      onSetsChanged();
    });

  const exportCsv = () => {
    const head = [
      "rank", "tier", "position", "pos_rank", "name", "team",
      "player_id", "model_rank", "note",
    ];
    const esc = (v: unknown) => JSON.stringify(v ?? "");
    const lines = board.map((r) =>
      [
        r.rank,
        r.tier,
        r.position ?? "",
        posRankById[r.player_id] ?? "",
        esc(r.name ?? ""),
        r.team ?? "",
        r.player_id,
        r.model_rank ?? "",
        esc(r.note ?? ""),
      ].join(","),
    );
    const blob = new Blob([[head.join(","), ...lines].join("\n")], {
      type: "text/csv;charset=utf-8",
    });
    const slug = (set?.name ?? "board").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
    const season = String(set?.season ?? "");
    const fname = season && !slug.includes(season) ? `${slug}_${season}` : slug;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${fname}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  if (detail.isLoading || !set) {
    return <div className="panel p-6 text-sm text-muted">Loading board…</div>;
  }

  const serverDirty = set.has_unpublished_changes || dirty;

  return (
    <div className="space-y-3">
      {/* Toolbar */}
      <div className="panel px-4 py-3 flex items-center gap-3 flex-wrap">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-semibold text-sm">{set.name}</span>
            <FormatChip
              format={set.format}
              formats={formats}
              onChange={(f) =>
                run("format", async () => {
                  await api.adminUpdateRankingSet(setId, { format: f });
                  mutateAll();
                })
              }
            />
          </div>
          <div className="text-[11px] text-muted mt-0.5">
            {set.season} · {board.length} players ·{" "}
            {set.status === "published"
              ? `live v${set.version}${serverDirty ? " · unpublished edits" : ""}`
              : "draft (not public)"}
          </div>
        </div>

        <div className="ml-auto flex items-center gap-2 flex-wrap">
          <SeedMenu onSeed={seed} busy={busy === "seed"} />
          <button
            onClick={exportCsv}
            disabled={board.length === 0}
            className="text-xs rounded px-3 py-1.5 border divider text-muted hover:text-white disabled:opacity-50"
            title="Download the current board (including unsaved edits) as CSV"
          >
            Export CSV
          </button>
          <button
            onClick={save}
            disabled={!dirty || busy !== null}
            className={`text-xs rounded px-3 py-1.5 border ${
              dirty
                ? "bg-amber-500/15 border-amber-500/40 text-amber-200"
                : "divider text-muted"
            } disabled:opacity-50`}
          >
            {busy === "save" ? "Saving…" : dirty ? "Save draft *" : "Saved"}
          </button>
          <button
            onClick={publish}
            disabled={busy !== null || board.length === 0}
            className="text-xs rounded px-3 py-1.5 bg-emerald-600/20 border border-emerald-500/40 text-emerald-300 hover:bg-emerald-600/30 disabled:opacity-50"
            title="Snapshot the draft to the public fantasy page"
          >
            {busy === "publish" ? "Publishing…" : "Publish"}
          </button>
          {set.status === "published" && (
            <button
              onClick={unpublish}
              disabled={busy !== null}
              className="text-xs rounded px-2.5 py-1.5 border divider text-muted hover:text-white disabled:opacity-50"
              title="Hide from the fantasy page (draft is kept)"
            >
              Unpublish
            </button>
          )}
          <button
            onClick={destroy}
            disabled={busy !== null}
            className="text-xs rounded px-2.5 py-1.5 border border-red-500/30 text-red-300 hover:bg-red-600/10 disabled:opacity-50"
          >
            Delete
          </button>
        </div>
      </div>

      {err && <p className="text-sm text-red-400">{err}</p>}

      {/* Filters + add */}
      <div className="flex items-center gap-2 flex-wrap">
        <div className="flex gap-1">
          {POSITIONS.map((p) => (
            <button
              key={p}
              onClick={() => setPosFilter(p)}
              className={`text-xs rounded px-2.5 py-1 border divider ${
                posFilter === p ? "bg-team-primary text-white" : "bg-bg text-muted"
              }`}
            >
              {p}
            </button>
          ))}
        </div>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Filter board…"
          className="bg-bg border divider rounded px-2 py-1 text-xs w-44"
        />
        {filtered && (
          <span className="text-[10px] text-muted">
            Filtered view — drag disabled, use the rank box to move players.
          </span>
        )}
        <AddPlayerSearch onAdd={addPlayer} exclude={board.map((r) => r.player_id)} />
      </div>

      {/* Board */}
      <div className="panel overflow-hidden">
        {board.length === 0 && (
          <div className="p-6 text-sm text-muted">
            Empty board — seed from projections or add players by search.
          </div>
        )}
        <div>
          {visible.map(({ r, i }, vIdx) => {
            const prev = vIdx > 0 ? visible[vIdx - 1].r : null;
            const newTier = !filtered && (i === 0 || board[i - 1].tier !== r.tier);
            return (
              <div key={r.player_id}>
                {newTier && (
                  <TierDivider
                    tier={r.tier}
                    removable={i > 0}
                    onRemove={() => removeTierBreak(i)}
                  />
                )}
                {filtered && prev !== null && prev.tier !== r.tier && (
                  <div className="px-4 py-0.5 text-[9px] uppercase tracking-wider text-muted bg-white/[0.02]">
                    Tier {r.tier}
                  </div>
                )}
                <BoardRow
                  row={r}
                  index={i}
                  posRank={posRankById[r.player_id]}
                  draggable={!filtered}
                  isDragOver={dragOver === i}
                  onDragStart={() => (dragFrom.current = i)}
                  onDragOverRow={() => setDragOver(i)}
                  onDrop={() => {
                    if (dragFrom.current !== null) moveTo(dragFrom.current, i);
                    dragFrom.current = null;
                    setDragOver(null);
                  }}
                  onDragEnd={() => {
                    dragFrom.current = null;
                    setDragOver(null);
                  }}
                  onMove={(to) => moveTo(i, to)}
                  onNote={(n) => setNote(i, n)}
                  onRemove={() => removeRow(i)}
                  onTierBreak={() => addTierBreak(i)}
                />
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function FormatChip({
  format,
  formats,
  onChange,
}: {
  format: string;
  formats: string[];
  onChange: (f: string) => void;
}) {
  return (
    <select
      value={format}
      onChange={(e) => onChange(e.target.value)}
      className="bg-bg border divider rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted"
      title="Format tag (drives model-comparison scoring on the fantasy page)"
    >
      {formats.map((f) => (
        <option key={f} value={f}>
          {FORMAT_LABELS[f] ?? f}
        </option>
      ))}
    </select>
  );
}

function SeedMenu({
  onSeed,
  busy,
}: {
  onSeed: (source: string) => void;
  busy: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        disabled={busy}
        className="text-xs rounded px-3 py-1.5 border divider text-muted hover:text-white disabled:opacity-50"
      >
        {busy ? "Seeding…" : "Seed from projections ▾"}
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 z-20 bg-bg border divider rounded shadow-lg min-w-[220px]">
          {(
            [
              ["ros_vorp", "ROS VORP order (with tiers)"],
              ["season_total", "Season total points order"],
            ] as const
          ).map(([src, label]) => (
            <button
              key={src}
              onClick={() => {
                setOpen(false);
                onSeed(src);
              }}
              className="block w-full text-left text-xs px-3 py-2 hover:bg-white/5"
            >
              {label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function TierDivider({
  tier,
  removable,
  onRemove,
}: {
  tier: number;
  removable: boolean;
  onRemove: () => void;
}) {
  return (
    <div className="flex items-center gap-2 px-4 py-1 bg-white/[0.03] border-y divider">
      <span className="text-[10px] font-bold uppercase tracking-wider text-team-primary">
        Tier {tier}
      </span>
      {removable && (
        <button
          onClick={onRemove}
          className="text-[10px] text-muted hover:text-red-300"
          title="Merge this tier into the one above"
        >
          merge ↑
        </button>
      )}
    </div>
  );
}

function BoardRow({
  row,
  index,
  posRank,
  draggable,
  isDragOver,
  onDragStart,
  onDragOverRow,
  onDrop,
  onDragEnd,
  onMove,
  onNote,
  onRemove,
  onTierBreak,
}: {
  row: Row;
  index: number;
  posRank?: number;
  draggable: boolean;
  isDragOver: boolean;
  onDragStart: () => void;
  onDragOverRow: () => void;
  onDrop: () => void;
  onDragEnd: () => void;
  onMove: (to: number) => void;
  onNote: (note: string) => void;
  onRemove: () => void;
  onTierBreak: () => void;
}) {
  const [rankDraft, setRankDraft] = useState<string | null>(null);
  const [noteOpen, setNoteOpen] = useState(false);

  const commitRank = () => {
    if (rankDraft !== null) {
      const n = parseInt(rankDraft, 10);
      if (!Number.isNaN(n) && n >= 1) onMove(n - 1);
    }
    setRankDraft(null);
  };

  return (
    <div
      draggable={draggable}
      onDragStart={(e) => {
        e.dataTransfer.effectAllowed = "move";
        onDragStart();
      }}
      onDragOver={(e) => {
        e.preventDefault();
        onDragOverRow();
      }}
      onDrop={(e) => {
        e.preventDefault();
        onDrop();
      }}
      onDragEnd={onDragEnd}
      className={`group flex items-center gap-3 px-4 py-1.5 border-b divider last:border-0 text-sm ${
        isDragOver ? "bg-team-primary/10 border-t-2 border-t-team-primary" : ""
      } ${draggable ? "cursor-grab active:cursor-grabbing" : ""}`}
    >
      <span
        className={`text-muted select-none ${draggable ? "" : "opacity-30"}`}
        title={draggable ? "Drag to reorder" : "Clear filters to drag"}
      >
        ⠿
      </span>
      <input
        value={rankDraft ?? String(row.rank)}
        onChange={(e) => setRankDraft(e.target.value.replace(/\D/g, ""))}
        onFocus={(e) => {
          setRankDraft(String(row.rank));
          e.target.select();
        }}
        onBlur={commitRank}
        onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
        className="w-11 bg-bg border divider rounded px-1 py-0.5 text-xs text-center tabular-nums"
        title="Type a rank and press Enter to move"
      />
      <div className="w-52 min-w-0">
        <span className="font-medium truncate">{row.name ?? row.player_id}</span>
        {row.injury_status && (
          <span className="ml-1.5 text-[9px] text-amber-500 font-bold uppercase">
            {row.injury_status}
          </span>
        )}
        <div className="text-[10px] text-muted">
          <span className="font-medium text-white/70 tabular-nums">
            {(row.position ?? "?")}{posRank ?? ""}
          </span>{" "}
          · {row.team ?? "FA"}
        </div>
      </div>
      <span className="text-[10px] text-muted w-10">T{row.tier}</span>
      {row.model_rank != null && (
        <span
          className="text-[10px] text-muted w-16 tabular-nums"
          title="Model's season leaderboard rank"
        >
          mdl #{row.model_rank}
        </span>
      )}
      <div className="flex-1 min-w-[120px]">
        {noteOpen || row.note ? (
          <input
            value={row.note}
            autoFocus={noteOpen && !row.note}
            onChange={(e) => onNote(e.target.value)}
            onBlur={() => setNoteOpen(false)}
            placeholder="note…"
            maxLength={200}
            className="w-full bg-transparent border-b divider text-xs text-muted focus:text-white outline-none py-0.5"
          />
        ) : (
          <button
            onClick={() => setNoteOpen(true)}
            className="text-[10px] text-muted/50 hover:text-muted opacity-0 group-hover:opacity-100"
          >
            + note
          </button>
        )}
      </div>
      <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100">
        <button
          onClick={onTierBreak}
          className="text-[10px] text-muted hover:text-team-primary"
          title="Start a new tier at this player"
        >
          + tier
        </button>
        <button
          onClick={onRemove}
          className="text-[10px] text-muted hover:text-red-300"
          title="Remove from board"
        >
          ✕
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Add-player search
// ---------------------------------------------------------------------------

function AddPlayerSearch({
  onAdd,
  exclude,
}: {
  onAdd: (p: { id: string; full_name: string; position: string | null; team_id: string | null }) => void;
  exclude: string[];
}) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<any[]>([]);
  const [open, setOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    if (q.trim().length < 2) {
      setResults([]);
      return;
    }
    timer.current = setTimeout(async () => {
      try {
        const rows = await api.listPlayers({ query: q.trim(), limit: 8 });
        setResults(rows);
        setOpen(true);
      } catch {
        /* search is best-effort */
      }
    }, 250);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [q]);

  return (
    <div className="relative ml-auto">
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onFocus={() => results.length && setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        placeholder="+ Add player to board…"
        className="bg-bg border divider rounded px-2 py-1 text-xs w-52"
      />
      {open && results.length > 0 && (
        <div className="absolute right-0 top-full mt-1 z-20 bg-bg border divider rounded shadow-lg w-64 max-h-64 overflow-y-auto">
          {results.map((p) => {
            const onBoard = exclude.includes(p.id);
            return (
              <button
                key={p.id}
                disabled={onBoard}
                onMouseDown={(e) => {
                  e.preventDefault();
                  onAdd(p);
                  setQ("");
                  setOpen(false);
                }}
                className="block w-full text-left text-xs px-3 py-2 hover:bg-white/5 disabled:opacity-40"
              >
                {p.full_name}
                <span className="text-muted ml-1.5">
                  {p.position ?? "?"} · {p.team_id ?? "FA"}
                </span>
                {onBoard && <span className="text-[9px] ml-1.5">on board</span>}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
