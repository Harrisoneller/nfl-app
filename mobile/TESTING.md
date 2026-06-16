# Testing the Statletics NFL mobile app

Two paths, in order of effort: **iOS Simulator** (fast, daily dev loop) and
**TestFlight** (real device, shareable). Both point at the **deployed Railway
backend**, so you don't need to run the API locally.

---

## 0. One-time setup

```bash
cd nfl-app/mobile

# Clean install (node_modules is gitignored)
rm -rf node_modules
npm install

# Point the app at your deployed backend
cp .env.example .env
# then edit .env and set your real Railway URL:
#   EXPO_PUBLIC_API_BASE=https://<your-app>.up.railway.app
```

Sanity-check the URL resolves and returns JSON before launching the app:

```bash
curl -s https://<your-app>.up.railway.app/health   # or /scores
```

> The native app does **not** trigger browser CORS, so the backend's
> `CORS_ORIGINS` does not need to include the app. If `curl` works, the app can
> reach it.

Confirm the project still typechecks:

```bash
npm run typecheck   # tsc --noEmit, should exit 0
```

---

## 1. iOS Simulator (Xcode)

**Requirements:** Xcode installed from the App Store, then its command-line
tools and a simulator runtime.

```bash
xcode-select --install              # if not already done
# Open Xcode once to accept the license and let it install components.
```

Run it:

```bash
npm run ios
```

This starts the Metro bundler and boots the iOS Simulator with the app loaded
via Expo Go / the dev client. To pick a specific device:

```bash
npx expo start --ios
# press `i` in the terminal, or open Simulator > File > Open Simulator first
```

**What to smoke-test (each hits a real endpoint):**

| Screen | Check |
| --- | --- |
| Home | Scores + news load; pull-to-refresh works |
| Teams | List groups by division; search filters; tap → team detail |
| Odds | Lines render; "See all books" expands; tap a price → sign-in prompt |
| Sparky | Slate + accuracy load; tap a game → detail |
| My Bets | Sign-in gate shows; after login, profile + filters work |
| Ask AI | Send a prompt; response streams back |
| Account | Login → register → change name → sign out |

If a screen shows "Couldn't load…", the backend URL in `.env` is wrong or the
Railway service is asleep/down — re-check step 0.

---

## 2. TestFlight (real device)

**Requirements:** Apple Developer Program membership ($99/yr) and an Expo
account (free).

```bash
npm install -g eas-cli
eas login
```

### a. Link the project

```bash
cd nfl-app/mobile
eas init        # creates/links an EAS project, writes extra.eas.projectId to app.json
```

### b. Fill in submit credentials

`eas.json` already has the build profiles. In the `submit.production.ios`
block, replace the two placeholders:

- `ascAppId` — the App Store Connect app's numeric Apple ID (create the app
  record at https://appstoreconnect.apple.com first; "App Information" → Apple ID).
- `appleTeamId` — your 10-character Team ID from
  https://developer.apple.com/account (Membership details).

`appleId` is already set to `harrisoneller@outlook.com` — change it if you
publish under a different Apple ID.

### c. Build and submit

```bash
# Production build (EAS manages signing certs/provisioning for you)
eas build -p ios --profile production

# After it finishes, upload to TestFlight
eas submit -p ios --latest
```

Then in App Store Connect → TestFlight, add yourself/testers. The build is
usable for internal testing within a few minutes of processing.

> The production build bakes in whatever `EXPO_PUBLIC_API_BASE` is set at build
> time. For TestFlight, set it in your shell before `eas build`, or define it as
> an EAS environment variable, so the binary points at the production Railway
> URL — not localhost.

### Want it on your own phone faster, without TestFlight?

A development/simulator-or-device build is quicker for solo testing:

```bash
eas build -p ios --profile development   # internal distribution, install via QR
```

---

## Troubleshooting

- **Everything says "Couldn't load":** wrong/missing `EXPO_PUBLIC_API_BASE`, or
  the Railway service is cold. `curl` the URL to confirm.
- **`npm run ios` can't find a simulator:** open Xcode → Settings → Components
  and install an iOS runtime; or launch the Simulator app manually first.
- **Logos missing, colored initials instead:** expected fallback when the ESPN
  CDN logo 404s for a team id; not a bug.
- **Stale bundle / weird errors after dependency changes:** `npx expo start -c`
  to clear the Metro cache.
