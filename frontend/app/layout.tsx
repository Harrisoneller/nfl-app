import "./globals.css";
import type { Metadata, Viewport } from "next";
import { Analytics } from "@vercel/analytics/next";
import { Nav } from "@/components/Nav";
import { Footer } from "@/components/Footer";
import { CommandPalette } from "@/components/CommandPalette";
import { ToastProvider } from "@/components/Toast";
import { AuthProvider } from "@/context/AuthProvider";
import { ExperimentProvider } from "@/context/ExperimentProvider";
import { PersonaProvider } from "@/context/PersonaProvider";

export const metadata: Metadata = {
  title: {
    default: "Statletics NFL",
    template: "%s · Statletics NFL",
  },
  description:
    "News, scores, stats, fantasy, odds, and AI — all things NFL in one place.",
  openGraph: {
    title: "Statletics NFL",
    description: "News, scores, stats, fantasy, odds, and AI — all things NFL.",
    type: "website",
  },
  twitter: {
    card: "summary",
    title: "Statletics NFL",
    description: "News, scores, stats, fantasy, odds, and AI — all things NFL.",
  },
  icons: {
    icon: [{ url: "/favicon.svg", type: "image/svg+xml" }],
  },
};

export const viewport: Viewport = {
  themeColor: "#0b0d10",
  viewportFit: "cover",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <ToastProvider>
          <ExperimentProvider>
            <PersonaProvider>
              <AuthProvider>
                <Nav />
                <main className="max-w-7xl mx-auto px-4 pt-6 pb-28 md:pb-6">{children}</main>
              </AuthProvider>
            </PersonaProvider>
          </ExperimentProvider>
          <Footer />
          <CommandPalette />
        </ToastProvider>
        <Analytics />
      </body>
    </html>
  );
}
