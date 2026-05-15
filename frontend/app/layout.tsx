import "./globals.css";
import type { Metadata } from "next";
import { Nav } from "@/components/Nav";
import { CommandPalette } from "@/components/CommandPalette";
import { ToastProvider } from "@/components/Toast";

export const metadata: Metadata = {
  title: {
    default: "NFL One-Stop",
    template: "%s · NFL One-Stop",
  },
  description:
    "News, scores, stats, fantasy, odds, and AI — all things NFL in one place.",
  openGraph: {
    title: "NFL One-Stop",
    description: "News, scores, stats, fantasy, odds, and AI — all things NFL.",
    type: "website",
  },
  twitter: {
    card: "summary",
    title: "NFL One-Stop",
    description: "News, scores, stats, fantasy, odds, and AI — all things NFL.",
  },
  icons: {
    icon: [{ url: "/favicon.svg", type: "image/svg+xml" }],
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <ToastProvider>
          <Nav />
          <main className="max-w-7xl mx-auto px-4 py-6">{children}</main>
          <footer className="max-w-7xl mx-auto px-4 py-8 text-xs text-muted">
            NFL One-Stop · data via ESPN, Sleeper, nfl-data-py, The Odds API, Open-Meteo
          </footer>
          <CommandPalette />
        </ToastProvider>
      </body>
    </html>
  );
}
