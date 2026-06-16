// Central design tokens — mirrors the web app's CSS variables (globals.css) so
// the mobile app reads as the same product. Dark-first, glassmorphic.

export const colors = {
  bg: "#0b0d10",
  bgElevated: "#11151a",
  panel: "#11151a",
  panelAlt: "#0e1216",
  border: "#1f242b",
  borderStrong: "#2a313a",
  muted: "#9ba3af",
  mutedDim: "#6b7280",
  text: "#e5e7eb",
  textBright: "#f8fafc",

  // Brand / accents
  brand: "#1f6feb",
  accent: "#22d3ee",

  // Semantic
  positive: "#34d399",
  negative: "#f43f5e",
  warning: "#f59e0b",
  bullish: "#34d399",
  info: "#60a5fa",

  // Glass surfaces (approximated as solid-with-alpha — RN has no backdrop blur
  // without a native blur view; these read close on a dark background).
  glass: "rgba(18, 23, 30, 0.72)",
  glassStrong: "rgba(14, 18, 24, 0.88)",
  glassBorder: "rgba(255, 255, 255, 0.10)",
  glassHighlight: "rgba(255, 255, 255, 0.06)",

  // Overlay palette for comparison charts (matches lib/colors.ts).
  overlay: ["#22d3ee", "#f97316", "#a78bfa", "#34d399", "#f43f5e"],
} as const;

export const radius = {
  sm: 8,
  md: 12,
  lg: 16,
  xl: 22,
  pill: 999,
} as const;

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
} as const;

export const font = {
  // System fonts — crisp on iOS without bundling a typeface.
  size: {
    xs: 11,
    sm: 13,
    base: 15,
    md: 17,
    lg: 20,
    xl: 24,
    xxl: 30,
  },
  weight: {
    regular: "400" as const,
    medium: "500" as const,
    semibold: "600" as const,
    bold: "700" as const,
    heavy: "800" as const,
  },
} as const;

export const shadow = {
  card: {
    shadowColor: "#000",
    shadowOpacity: 0.35,
    shadowRadius: 16,
    shadowOffset: { width: 0, height: 8 },
    elevation: 6,
  },
} as const;

export const theme = { colors, radius, spacing, font, shadow };
export type Theme = typeof theme;
