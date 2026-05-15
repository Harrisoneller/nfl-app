"use client";
import useSWR from "swr";
import { api } from "@/lib/api";

const fetcher = () => api.seasons();

export function SeasonSelect({
  value,
  onChange,
  className = "",
}: {
  value: number | undefined;
  onChange: (s: number) => void;
  className?: string;
}) {
  const { data } = useSWR("/meta/seasons", fetcher);
  const opts = data?.available ?? [];
  const current = value ?? data?.default;

  return (
    <label className={`flex items-center gap-2 text-sm ${className}`}>
      <span className="text-muted">Season</span>
      <select
        value={current ?? ""}
        onChange={(e) => onChange(Number(e.target.value))}
        className="bg-bg border divider rounded px-2 py-1.5 text-sm"
      >
        {!current && <option value="">…</option>}
        {opts.map((s) => (
          <option key={s} value={s}>{s}</option>
        ))}
      </select>
    </label>
  );
}
