# DokTok NG mobile (React Native / Expo)

The mobile client (epic #769). Android first, iOS later. Same spartan dark UI as the web app.

## Setup (standalone - intentionally NOT in the pnpm workspace)

React Native 0.86 needs React 19 while `apps/ui` is on React 18; keeping them in one pnpm
workspace makes peer resolution collide (React 19 types leak into the UI build). So this package
installs standalone:

```bash
make mobile-install        # = cd apps/mobile && pnpm install --ignore-workspace
```

## Run

```bash
cd apps/mobile
pnpm start                 # Metro; then press 'a' for Android
pnpm android               # start + open on a connected Android device/emulator
```

M1 works in Expo Go. From M2 (camera scanning) a dev-client build is required (custom native
module); use `pnpm android` once a dev client is built.

## Layout

- `App.tsx` — navigation container + bottom tabs (Documents / Scan / Chat / Insights / Settings)
- `src/theme.ts` — design tokens mirroring `apps/ui/src/styles.css`
- `src/screens/` — screens (PlaceholderScreen until M1.3+)
