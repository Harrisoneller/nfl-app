import { api } from "@/lib/api";
import { Card } from "@/components/Card";
import Link from "next/link";

export const revalidate = 3600;

export default async function TeamsPage() {
  const teams = await api.listTeams({ revalidate: 3600 }).catch(() => []);
  const byConference: Record<string, Record<string, typeof teams>> = {};
  for (const t of teams) {
    byConference[t.conference] ??= {};
    byConference[t.conference][t.division] ??= [] as any;
    (byConference[t.conference][t.division] as any).push(t);
  }
  const order = ["East", "North", "South", "West"];

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Teams</h1>
      {(["AFC", "NFC"] as const).map((conf) => (
        <Card key={conf} title={conf}>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            {order.map((div) => (
              <div key={div}>
                <h3 className="text-sm text-muted mb-2">{conf} {div}</h3>
                <ul className="space-y-1">
                  {(byConference[conf]?.[div] || []).map((t) => (
                    <li key={t.id}>
                      <Link
                        href={`/teams/${t.id}`}
                        className="flex items-center gap-2 text-sm hover:text-team-primary"
                        style={{
                          ["--team-primary" as any]: t.primary_color,
                        } as React.CSSProperties}
                      >
                        <span
                          className="inline-block w-2.5 h-2.5 rounded-sm"
                          style={{ background: t.primary_color }}
                        />
                        {t.full_name}
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </Card>
      ))}
    </div>
  );
}
