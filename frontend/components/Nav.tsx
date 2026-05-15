"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { ThemeToggle } from "./ThemeProvider";

const links = [
  { href: "/", label: "Home" },
  { href: "/teams", label: "Teams" },
  { href: "/players", label: "Players" },
  { href: "/compare", label: "Compare" },
  { href: "/fantasy", label: "Fantasy" },
  { href: "/odds", label: "Odds" },
  { href: "/ai", label: "AI" },
];

export function Nav() {
  const [isMac, setIsMac] = useState(true);
  useEffect(() => {
    setIsMac(/Mac|iPhone|iPad/.test(navigator.userAgent));
  }, []);
  const cmd = isMac ? "⌘" : "Ctrl";

  const openPalette = () => {
    // Dispatch a keyboard event so CommandPalette opens (it listens for ⌘K)
    window.dispatchEvent(
      new KeyboardEvent("keydown", { key: "k", metaKey: isMac, ctrlKey: !isMac }),
    );
  };

  return (
    <header className="border-b divider sticky top-0 z-30 backdrop-blur bg-bg/70">
      <div className="max-w-7xl mx-auto px-4 h-14 flex items-center justify-between gap-2">
        <Link href="/" className="font-semibold tracking-tight text-lg whitespace-nowrap">
          🏈 NFL One-Stop
        </Link>
        <nav className="hidden md:flex items-center gap-5 text-sm">
          {links.map((l) => (
            <Link key={l.href} href={l.href} className="hover:text-team-primary transition-colors">
              {l.label}
            </Link>
          ))}
        </nav>
        <div className="flex items-center gap-2">
          <button
            onClick={openPalette}
            className="hidden sm:flex items-center gap-2 text-xs text-muted bg-bg border divider rounded px-2 py-1 hover:text-text"
            aria-label="Search (Cmd+K)"
          >
            <span>Search</span>
            <kbd className="text-[10px] bg-panel border divider rounded px-1">{cmd}K</kbd>
          </button>
          <ThemeToggle />
        </div>
      </div>
      {/* Mobile nav row */}
      <nav className="md:hidden flex items-center gap-4 text-xs px-4 py-2 overflow-x-auto border-t divider">
        {links.map((l) => (
          <Link key={l.href} href={l.href} className="whitespace-nowrap hover:text-team-primary">
            {l.label}
          </Link>
        ))}
      </nav>
    </header>
  );
}
