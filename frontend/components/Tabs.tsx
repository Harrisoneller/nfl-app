"use client";
import { ReactNode } from "react";

export type Tab = { id: string; label: string; count?: number };

export function TabBar({
  tabs,
  active,
  onChange,
}: {
  tabs: Tab[];
  active: string;
  onChange: (id: string) => void;
}) {
  return (
    <div className="border-b divider sticky top-14 z-20 bg-bg/85 backdrop-blur -mx-4 px-4 overflow-x-auto">
      <div className="flex gap-1 min-w-max">
        {tabs.map((t) => {
          const isActive = active === t.id;
          return (
            <button
              key={t.id}
              onClick={() => onChange(t.id)}
              className={`px-3 py-2 text-sm border-b-2 transition-colors whitespace-nowrap
                ${isActive
                  ? "border-team-primary text-text font-medium"
                  : "border-transparent text-muted hover:text-text"}`}
            >
              {t.label}
              {t.count !== undefined && (
                <span className="ml-1.5 text-xs text-muted">({t.count})</span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function TabPanel({ active, value, children }: { active: string; value: string; children: ReactNode }) {
  if (active !== value) return null;
  return <div className="space-y-6">{children}</div>;
}
