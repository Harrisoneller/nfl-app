"use client";

import { PERSONAS, Persona, usePersona } from "@/context/PersonaProvider";

const LABELS: Record<Persona, string> = {
  bettor: "Bettor",
  fantasy: "Fantasy",
  analyst: "Analyst",
};

export function PersonaToggle() {
  const { persona, setPersona } = usePersona();

  return (
    <div className="hidden lg:flex items-center rounded-full border divider bg-bg/70 p-0.5">
      {PERSONAS.map((mode) => {
        const active = mode === persona;
        return (
          <button
            key={mode}
            type="button"
            onClick={() => setPersona(mode)}
            className={`px-2.5 py-1 text-[11px] rounded-full transition-colors ${
              active ? "bg-team-primary/20 text-text" : "text-muted hover:text-text"
            }`}
            aria-pressed={active}
          >
            {LABELS[mode]}
          </button>
        );
      })}
    </div>
  );
}
