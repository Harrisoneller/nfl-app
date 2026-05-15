"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Card } from "../Card";

type Spec = Record<string, any>;

export function WidgetRenderer({ spec }: { spec: Spec }) {
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    api
      .renderInline(spec)
      .then((r) => mounted && setData(r.data))
      .catch((e) => mounted && setErr(String(e)))
      .finally(() => mounted && setLoading(false));
    return () => {
      mounted = false;
    };
  }, [JSON.stringify(spec)]);

  return (
    <Card title={spec.title}>
      {spec.description && (
        <p className="text-sm text-muted mb-3">{String(spec.description)}</p>
      )}
      {loading && <div className="text-sm text-muted">Loading…</div>}
      {err && <div className="text-sm text-red-400">{err}</div>}
      {!loading && !err && <RenderBody kind={spec.kind} data={data} />}
    </Card>
  );
}

function RenderBody({ kind, data }: { kind: string; data: any }) {
  if (!data) return <div className="text-sm text-muted">No data.</div>;
  if (data.error) return <div className="text-sm text-red-400">{String(data.error)}</div>;

  if (kind === "comparison_table" && Array.isArray(data.rows)) {
    const metrics: string[] = data.metrics || Object.keys(data.rows[0] || {});
    return (
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-muted">
              <th className="py-1 pr-3">Team</th>
              {metrics.filter((m) => m !== "team_id").map((m) => (
                <th key={m} className="py-1 pr-3">{m}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((r: any, i: number) => (
              <tr key={i} className="border-t divider">
                <td className="py-1 pr-3 font-medium">{r.team_id}</td>
                {metrics.filter((m) => m !== "team_id").map((m) => (
                  <td key={m} className="py-1 pr-3">{fmt(r[m])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  if (kind === "scoreboard" && Array.isArray(data.games)) {
    return (
      <ul className="space-y-1 text-sm">
        {data.games.map((g: any) => (
          <li key={g.id} className="flex justify-between border-t divider py-1">
            <span>{g.away_team_id} @ {g.home_team_id}</span>
            <span className="text-muted">{g.status_detail || g.status}</span>
            <span>{g.away_score ?? ""} – {g.home_score ?? ""}</span>
          </li>
        ))}
      </ul>
    );
  }

  if (kind === "news_list" && Array.isArray(data.items)) {
    return (
      <ul className="space-y-2 text-sm">
        {data.items.slice(0, 10).map((n: any) => (
          <li key={n.id}>
            <a href={n.link} target="_blank" rel="noreferrer" className="hover:underline">
              <span className="text-muted">[{n.source_label}]</span> {n.title}
            </a>
          </li>
        ))}
      </ul>
    );
  }

  if (kind === "odds_table" && Array.isArray(data.lines)) {
    return (
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-muted">
            <tr><th>Game</th><th>Market</th><th>Book</th><th>Outcome</th><th>Price</th></tr>
          </thead>
          <tbody>
            {data.lines.slice(0, 30).map((l: any) => (
              <tr key={l.id} className="border-t divider">
                <td>{l.away_team} @ {l.home_team}</td>
                <td>{l.market}</td>
                <td>{l.bookmaker}</td>
                <td>{l.label}{l.point != null ? ` ${l.point}` : ""}</td>
                <td>{l.price}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  // Fallback: pretty-printed JSON
  return (
    <pre className="text-xs overflow-x-auto bg-bg p-3 rounded-md border divider">
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}

function fmt(v: any) {
  if (typeof v === "number") return Number.isInteger(v) ? v : v.toFixed(2);
  return String(v ?? "");
}
