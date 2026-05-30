"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/context/AuthProvider";
import { ThemeToggle } from "./ThemeProvider";
import { PersonaToggle } from "./persona/PersonaToggle";

// NOTE: /players, /compare, and /performance are intentionally hidden from the
// nav while they're being stabilized. The pages still exist if you navigate
// directly to the URL — re-add the entries here to restore discovery.
const links = [
  { href: "/", label: "Home" },
  { href: "/teams", label: "Teams" },
  { href: "/h2h/PHI/SF", label: "H2H" },
  { href: "/odds", label: "Odds" },
  { href: "/sparky", label: "Sparky" },
  // /fantasy and /ai hidden until ready — routes still work via direct URL
];

export function Nav() {
  const { user, loading } = useAuth();
  const [isMac, setIsMac] = useState(true);
  useEffect(() => {
    setIsMac(/Mac|iPhone|iPad/.test(navigator.userAgent));
  }, []);
  const cmd = isMac ? "⌘" : "Ctrl";

  const openPalette = () => {
    window.dispatchEvent(
      new KeyboardEvent("keydown", { key: "k", metaKey: isMac, ctrlKey: !isMac }),
    );
  };

  return (
    <header className="border-b divider sticky top-0 z-30 backdrop-blur bg-bg/70">
      <div className="max-w-7xl mx-auto px-4 h-14 flex items-center justify-between gap-2">
        <Link href="/" className="nav-brand group shrink-0" aria-label="Statletics NFL home">
          <span className="nav-brand__primary">Statletics</span>
          <span className="nav-brand__accent">NFL</span>
        </Link>
        <nav className="hidden md:flex items-center gap-5 text-sm">
          {links.map((l) => (
            <Link key={l.href} href={l.href} className="hover:text-team-primary transition-colors">
              {l.label}
            </Link>
          ))}
        </nav>
        <div className="flex items-center gap-2">
          <PersonaToggle />
          {!loading &&
            (user ? (
              <Link
                href="/account"
                className="hidden sm:inline-flex items-center gap-1.5 text-xs text-muted hover:text-team-primary border divider rounded px-2 py-1"
              >
                <span
                  className="w-5 h-5 rounded-full bg-team-primary/25 text-[10px] font-bold flex items-center justify-center text-team-primary"
                  aria-hidden
                >
                  {(user.display_name || user.email)[0]?.toUpperCase()}
                </span>
                <span className="max-w-[100px] truncate">
                  {user.display_name || user.email.split("@")[0]}
                </span>
              </Link>
            ) : (
              <Link
                href="/login"
                className="hidden sm:inline text-xs text-muted hover:text-team-primary border divider rounded px-2 py-1"
              >
                Sign in
              </Link>
            ))}
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
