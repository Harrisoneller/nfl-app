"use client";

import { Persona, usePersona } from "@/context/PersonaProvider";

export function PersonaGate({
  allowed,
  children,
  fallback = null,
}: {
  allowed: Persona[];
  children: React.ReactNode;
  fallback?: React.ReactNode;
}) {
  const { persona } = usePersona();
  if (!allowed.includes(persona)) return <>{fallback}</>;
  return <>{children}</>;
}
