"use client";

import { createContext, useContext, useEffect, useMemo, useState } from "react";

export const PERSONAS = ["bettor", "fantasy", "analyst"] as const;
export type Persona = (typeof PERSONAS)[number];

const STORAGE_KEY = "nfl-app.persona";

type PersonaContextValue = {
  persona: Persona;
  setPersona: (next: Persona) => void;
};

const PersonaContext = createContext<PersonaContextValue | null>(null);

export function PersonaProvider({ children }: { children: React.ReactNode }) {
  const [persona, setPersonaState] = useState<Persona>("bettor");

  useEffect(() => {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw && (PERSONAS as readonly string[]).includes(raw)) {
      setPersonaState(raw as Persona);
    }
  }, []);

  const setPersona = (next: Persona) => {
    setPersonaState(next);
    window.localStorage.setItem(STORAGE_KEY, next);
  };

  const value = useMemo(() => ({ persona, setPersona }), [persona]);
  return <PersonaContext.Provider value={value}>{children}</PersonaContext.Provider>;
}

export function usePersona() {
  const ctx = useContext(PersonaContext);
  if (!ctx) {
    throw new Error("usePersona must be used within PersonaProvider");
  }
  return ctx;
}
