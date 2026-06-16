# Statletics NFL — Mobile (Expo / React Native)

The iOS-first mobile companion to the Statletics NFL web app. It talks to the
**same FastAPI backend** as the website and reuses the web app's exact API
contract (`lib/api.ts`), so there is one source of truth for data and types.

Built with **Expo Router** (file-based navigation), TypeScript (strict), and a
hand-rolled dark "glass" design system that mirrors the web app's look.

---

## Quick start

```bash
cd nfl-app/mobile
rm -rf node_modules        # clear the partial install left from scaffolding
npm install

# Point the app at your backend (see "Backend URL" below)
cp .env.example .env        # then edit EXPO_PUBLIC_API_BASE

npm run ios                 # open in the iOS simulator (needs Xcode)
# or
npm start                   # then scan the QR code with Expo Go on your iPhone
```

`npm run typecheck` runs `tsc --noEmit` (currently passes clean).

### Backend URL

The app resolves its API base in this order (`src/lib/api.ts`):

1. `EXPO_PUBLIC_API_BASE` env var (recommended)
2. `expo.extra.apiBase` in `app.json`
3. `http://localhost:8000` fallback

| Where you run | Use this base URL |
| --- | --- |
| iOS **simulator** | `http://localhost:8000` |
| **Physical iPhone** (Expo Go) | `http://<your-mac-LAN-ip>:8000` e.g. `http://192.168.1.42:8000` |
| Production | `https://<your-backend>.up.railway.app` |

> A physical device can't reach `localhost` — that's the phone itself. Use your
> Mac's LAN IP and make sure the backend binds `0.0.0.0` and CORS allows the origin.

---

## What's included (feature parity with the web app)

Bottom tab bar: **Home · Teams · Odds · Sparky · My Bets**, plus stacked detail
and utility screens.

| Screen | Route | Backend endpoints |
| --- | --- | --- |
| Home (scores, news, quick links) | `app/(tabs)/index.tsx` | `/scores`, `/news` |
| Teams list (grouped by division, search) | `app/(tabs)/teams.tsx` | `/teams` |
| Team detail (profile metrics, Elo trend, outlook, roster, schedule) | `app/teams/[id].tsx` | `/teams/*`, `/predictions/teams/*` |
| Odds board (best line per side, all-books, one-tap track) | `app/(tabs)/odds.tsx` | `/odds`, `/odds/status`, `/bets` |
| Sparky (dashboard, parlays, accuracy) | `app/(tabs)/sparky.tsx` | `/sparky/slate`, `/sparky/accuracy` |
| Sparky game detail (signals, line movement, by-book) | `app/sparky/[eventId].tsx` | `/sparky/games/{id}` |
| My Bets (CLV profile, settle, delete, filter) | `app/(tabs)/bets.tsx` | `/bets`, `/bets/profile`, `/bets/settle` |
| Players (search by name/position) | `app/players/index.tsx` | `/players` |
| Player detail (percentiles, projection, matchups) | `app/players/[id].tsx` | `/players/*`, `/predictions/players/*` |
| Head-to-head | `app/h2h/[a]/[b].tsx` | `/h2h/{a}/{b}` |
| Compare teams | `app/compare.tsx` | `/teams/{id}/profile` |
| Fantasy (trending add/drop, news) | `app/fantasy.tsx` | `/fantasy/*` |
| Ask AI (chat with tool use) | `app/ai.tsx` | `/ai/chat` |
| Account / Login / Register | `app/account.tsx`, `login.tsx`, `register.tsx` | `/auth/*` |

---

## Architecture

```
mobile/
├── app/                      # Expo Router screens (file = route)
│   ├── _layout.tsx           # root stack + providers
│   └── (tabs)/_layout.tsx    # bottom tab bar
├── src/
│   ├── lib/
│   │   ├── api.ts            # ported verbatim from the web client (single source of truth)
│   │   ├── auth-storage.ts   # JWT in expo-secure-store (sync in-memory mirror)
│   │   ├── useApi.ts         # tiny SWR-style fetch hook (loading/error/refetch)
│   │   ├── odds.ts           # odds grouping + best-line selection (ported)
│   │   ├── metrics.ts        # metric labels/formatters (shared with web)
│   │   ├── team-colors.ts    # NFL team palette (shared with web)
│   │   └── format.ts         # american odds, %, dates, grade colors
│   ├── context/AuthProvider.tsx
│   ├── components/           # TeamLogo, WinProbBar, MiniLineChart (SVG), StatRow …
│   │   └── ui/               # Screen, Card, Pill, Button, Input, Segmented, Text, States
│   └── theme/theme.ts        # design tokens (colors, spacing, type)
```

### Key porting decisions

- **`api.ts` is shared 1:1 with the web app.** Only two things changed: the base
  URL now reads `EXPO_PUBLIC_API_BASE`/Expo config, and the fetch wrapper dropped
  Next.js's SSR `cache`/`revalidate` options (no SSR on device). Every type and
  every endpoint method is identical, so backend changes stay in sync by copying
  one file.
- **Auth token** moved from `localStorage` to `expo-secure-store`. Since the API
  client reads the token synchronously on every request, we keep an in-memory
  mirror that is hydrated from SecureStore once at app boot.
- **Charts**: Recharts (web/SVG/DOM) was replaced with a small custom
  `MiniLineChart` built on `react-native-svg` for Elo/line-movement trends.
- **Data fetching**: `useApi` replaces SWR — same `{ data, error, isLoading,
  refetch }` ergonomics plus pull-to-refresh, without the web dependency.

---

## Notes & next steps

- **Push notifications** (line moves, bet settles, game start) are a natural
  mobile-only addition — wire `expo-notifications` to the existing Sparky/odds
  refresh jobs.
- **EAS Build** for TestFlight: `eas.json` is included. Run `npx eas build -p ios`
  once you have an Apple Developer account — see `TESTING.md` for the full flow.
- `node_modules`, `ios/`, and `android/` are gitignored — run `npm install`
  after cloning. Native folders are generated by `expo prebuild` / EAS as needed.
- Admin-only Sparky tools (refresh/backfill/settle/backtest) exist in the API
  client but are intentionally not surfaced in the mobile UI; add an admin tab
  gated on `user.is_admin` if you want them on device.
```
