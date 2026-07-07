"use client";
import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { useAuth } from "@/context/AuthProvider";
import { ThemeToggle } from "./ThemeProvider";

// NOTE: /compare and /performance are intentionally hidden from the nav while
// they're being stabilized. The pages still exist if you navigate directly to
// the URL — re-add the entries here to restore discovery. /players is live
// again (projection engine v2).
type NavLink = {
  href: string;
  label: string;
  // route prefix used to mark the entry active (Home matches "/" exactly)
  seg: string;
  icon: ReactNode;
};

const links: NavLink[] = [
  { href: "/", label: "Home", seg: "/", icon: <HomeIcon /> },
  { href: "/teams", label: "Teams", seg: "/teams", icon: <TeamsIcon /> },
  { href: "/players", label: "Players", seg: "/players", icon: <PlayersIcon /> },
  { href: "/h2h/PHI/SF", label: "H2H", seg: "/h2h", icon: <H2HIcon /> },
  { href: "/odds", label: "Odds", seg: "/odds", icon: <OddsIcon /> },
  { href: "/sparky", label: "Sparky", seg: "/sparky", icon: <SparkyIcon /> },
  { href: "/bets", label: "My Bets", seg: "/bets", icon: <BetsIcon /> },
  // /fantasy now redirects into the Players hub (/players?tab=fantasy).
  // /ai hidden until ready — route still works via direct URL.
];

function isActive(pathname: string, seg: string) {
  return seg === "/" ? pathname === "/" : pathname.startsWith(seg);
}

export function Nav() {
  const { user, loading } = useAuth();
  const pathname = usePathname() || "/";
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
    <>
      {/* ===== Top bar (glass) ===== */}
      <header className="glass-nav">
        <div className="max-w-7xl mx-auto px-4 h-14 flex items-center justify-between gap-2">
          <Link href="/" className="nav-logo group shrink-0" aria-label="Statletics Sports home">
            <Image
              src="/brand/statletics-neon.png"
              alt="Statletics Sports"
              width={1014}
              height={403}
              priority
              className="nav-logo-img"
            />
          </Link>

          {/* Desktop primary nav — morphing pill highlights */}
          <nav className="hidden md:flex items-center gap-1">
            {links.map((l) => (
              <Link
                key={l.href}
                href={l.href}
                className="nav-link"
                data-active={isActive(pathname, l.seg)}
              >
                {l.label}
              </Link>
            ))}
          </nav>

          <div className="flex items-center gap-2">
            {!loading &&
              (user ? (
                <Link
                  href="/account"
                  className="glass-pill hidden sm:inline-flex text-xs text-muted hover:text-text px-2 py-1"
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
                  className="glass-pill hidden sm:inline-flex text-xs text-muted hover:text-text px-3 py-1"
                >
                  Sign in
                </Link>
              ))}
            <button
              onClick={openPalette}
              className="glass-pill hidden sm:flex text-xs text-muted hover:text-text px-2.5 py-1"
              aria-label="Search (Cmd+K)"
            >
              <span>Search</span>
              <kbd className="text-[10px] bg-panel/60 border divider rounded px-1">{cmd}K</kbd>
            </button>
            <button
              onClick={openPalette}
              className="glass-pill sm:hidden w-8 h-8 justify-center text-muted"
              aria-label="Search"
            >
              <SearchIcon />
            </button>
            <ThemeToggle />
          </div>
        </div>
      </header>

      {/* ===== Mobile bottom tab bar (iOS-style floating glass) ===== */}
      <nav className="glass-tabbar md:hidden" aria-label="Primary">
        {links.map((l) => (
          <Link
            key={l.href}
            href={l.href}
            className="tab-item"
            data-active={isActive(pathname, l.seg)}
            aria-current={isActive(pathname, l.seg) ? "page" : undefined}
          >
            {l.icon}
            <span>{l.label}</span>
          </Link>
        ))}
      </nav>
    </>
  );
}

/* ---------- Inline icons (dependency-free, currentColor) ---------- */
function HomeIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M3 10.5 12 3l9 7.5" />
      <path d="M5 9.5V21h14V9.5" />
      <path d="M9.5 21v-6h5v6" />
    </svg>
  );
}
function TeamsIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M12 3l7 3v5c0 4.4-3 7.7-7 9-4-1.3-7-4.6-7-9V6l7-3Z" />
      <path d="M9 11l2 2 4-4" />
    </svg>
  );
}
function PlayersIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <circle cx="9" cy="8" r="3.5" />
      <path d="M3.5 20c.6-3.2 2.9-5 5.5-5s4.9 1.8 5.5 5" />
      <path d="M16 4.5a3.5 3.5 0 0 1 0 7" />
      <path d="M17.5 15c1.9.5 3.2 2 3.5 5" />
    </svg>
  );
}
function H2HIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M3 8h13l-3-3" />
      <path d="M21 16H8l3 3" />
    </svg>
  );
}
function OddsIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M4 19V5" />
      <path d="M4 19h16" />
      <path d="M8 16v-4" />
      <path d="M13 16V8" />
      <path d="M18 16v-6" />
    </svg>
  );
}
function SparkyIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3Z" />
      <path d="M19 15.5l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7.7-2Z" />
    </svg>
  );
}
function BetsIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="M3 10h18" />
      <path d="M7 15h4" />
    </svg>
  );
}
function SearchIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.2-3.2" />
    </svg>
  );
}
