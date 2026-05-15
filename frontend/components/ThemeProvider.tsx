"use client";
import { ReactNode, useEffect } from "react";

/**
 * Sets root CSS variables for team theming. Children get team-tinted colors via
 * Tailwind's bg-team-primary etc. or custom CSS using `--team-primary`.
 */
export function TeamTheme({
  primary,
  secondary,
  children,
}: {
  primary?: string;
  secondary?: string;
  children: ReactNode;
}) {
  useEffect(() => {
    const root = document.documentElement;
    if (primary) root.style.setProperty("--team-primary", primary);
    if (secondary) root.style.setProperty("--team-secondary", secondary);
    return () => {
      root.style.removeProperty("--team-primary");
      root.style.removeProperty("--team-secondary");
    };
  }, [primary, secondary]);

  return <>{children}</>;
}

export function ThemeToggle() {
  const toggle = () => {
    const root = document.documentElement;
    const cur = root.getAttribute("data-theme");
    root.setAttribute("data-theme", cur === "light" ? "dark" : "light");
  };
  return (
    <button
      onClick={toggle}
      className="text-sm text-muted hover:text-text transition-colors"
      aria-label="Toggle theme"
    >
      ◐
    </button>
  );
}
