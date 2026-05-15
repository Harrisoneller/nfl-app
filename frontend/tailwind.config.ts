import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Pulled from CSS variables — set per page (team theming) or globally (dark mode)
        "team-primary": "var(--team-primary, #111827)",
        "team-secondary": "var(--team-secondary, #9ca3af)",
        bg: "var(--bg, #0b0d10)",
        panel: "var(--panel, #11151a)",
        border: "var(--border, #1f242b)",
        muted: "var(--muted, #9ba3af)",
        text: "var(--text, #e5e7eb)",
      },
      fontFamily: {
        sans: ["ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto"],
      },
    },
  },
  plugins: [],
};

export default config;
