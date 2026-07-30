# DokTok NG mobile (React Native / Expo)

The mobile client (epic #769). Android first, iOS later. Same spartan dark UI as the web app.

## Prerequisites (one-time)

| Tool | Why | Install |
|---|---|---|
| pnpm | JS deps | already used by the repo |
| Android Studio | bundles the Java runtime (JBR) gradle needs | `brew install --cask android-studio` (retry on flaky downloads) |
| JDK 17 (Temurin) | the build's `compileKotlin` toolchain requires exactly Java 17 (JBR 21 does NOT satisfy it) | `brew install --cask temurin@17` |
| Android SDK (cmdline) | adb, emulator, avdmanager | see below |

The native Android app needs a Java runtime; macOS ships none. Android Studio's bundled JBR runs
gradle itself (`JAVA_HOME`), while the Kotlin compile toolchain wants JDK 17 specifically —
gradle auto-detects it under `/Library/Java/JavaVirtualMachines` once installed, and downloads it
itself only as a fallback (foojay.io, which a locked-down network may block).

### SDK command-line tools (no IDE wizard needed)

```bash
mkdir -p ~/Library/Android/sdk/cmdline-tools
cd /tmp && curl -fL --retry 3 -o cmdtools.zip \
  "https://dl.google.com/android/repository/commandlinetools-mac-13114758_latest.zip"
cd ~/Library/Android/sdk/cmdline-tools && unzip -q /tmp/cmdtools.zip && mv cmdline-tools latest
```

### Environment (put in `~/.zshrc`)

```bash
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
export ANDROID_HOME=~/Library/Android/sdk
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$ANDROID_HOME/cmdline-tools/latest/bin:$PATH"
```

### SDK packages + the virtual device

```bash
yes | sdkmanager --licenses
sdkmanager "platform-tools" "platforms;android-36" "emulator" "system-images;android-36;google_apis;arm64-v8a"
# (Apple Silicon: the arm64-v8a image above; Intel: use x86_64 instead)
echo "no" | avdmanager create avd -n doktok -k "system-images;android-36;google_apis;arm64-v8a" -d "pixel_7"
```

Flaky-network note: if a big package fails with a TLS/decrypt error, just rerun `sdkmanager` for
the missing piece - it resumes.

## App dependencies (standalone - intentionally NOT in the pnpm workspace)

React Native needs React 19 while `apps/ui` is on React 18; one workspace would leak React 19
types into the UI build. So:

```bash
make mobile-install        # = cd apps/mobile && pnpm install --ignore-workspace
```

## Run on the Android emulator

```bash
make mobile-emulator-start     # boot the AVD (detached; status: make mobile-emulator-status)
make mobile-run                # build + install the dev client on the EMULATOR (first time / native changes)
make mobile-start              # Metro in dev-client mode (hot-reload for everything JS)
```

The emulator reaches the backend as `http://10.0.2.2:8000` (default in `app.json`).

## Run on a physical phone (USB cable)

No LAN/firewall needed - everything goes through `adb reverse` tunnels:

```bash
make mobile-deploy             # build + install on the USB PHONE (auto-detects it, ensures adb reverse tcp:8000+8081)
make mobile-start              # Metro; the app on the phone loads the bundle over the cable
```

`mobile-deploy` is the one-command phone loop (also after native dependency changes); JS-only
changes need no redeploy - Metro hot-reloads. The app expects the backend at
`http://127.0.0.1:8000` over the tunnel (swap `extra.backendUrl` in `app.json` back to
`http://10.0.2.2:8000` when switching to the emulator).

Phone prep (once): Settings -> About phone -> tap Build number 7x -> Developer options -> USB
debugging; accept the RSA prompt on first connect.

## What the app does today (M1+)

- **Login** (tenant + user). Credentials are saved in the OS keystore; on emulators without a
  lock screen (where the Android keystore silently fails) it falls back to AsyncStorage - either
  way you log in once.
- **Documents**: full-width accordion tiles (collapsed: bold title + acquire/doc dates + status
  badge; tap to expand for category, tags, counts, features, open) OR a 2-column thumbnail grid
  (toggle next to search). First-page thumbnails everywhere (ingest-generated WebP).
- **Search**: title box + complex filters - token chips with AND completions (from
  `/tokens/suggest`, document counts, floating overlay), category dropdown, status, needs
  attention / unidentifiable switches, tag chips.
- **Document detail**: Content (rendered markdown, selectable for copy) / Entities / Activity
  tabs, per-page thumbnails, "view PDF" / "view searchable" inline viewer (native PDFium,
  zoom buttons +-/%), share.
- **Scan**: ML Kit camera scanning -> page review (reorder/delete) -> one PDF -> upload with
  progress. Fully on-device until the upload.

## Layout

- `App.tsx` — navigation container + bottom tabs (Documents / Scan / Chat / Insights / Settings),
  gated by auth
- `src/theme.ts` — design tokens mirroring `apps/ui/src/styles.css`
- `src/config.ts` — backend URL resolution (`app.json` `extra.backendUrl`)
- `src/api/` — typed API client (`client.ts`), auth, documents, document-detail
- `src/auth/AuthContext.tsx` — token + credentials in SecureStore (AsyncStorage fallback)
- `src/components/` — DocumentTile, DocumentGridCard, SearchFilters, TokenInput, AuthImage
- `src/screens/` — DocumentsStack (list -> detail -> PDF viewer), LoginScreen, ScanScreen,
  PdfViewerScreen, placeholders (Chat/Insights/Settings land per ticket)
- `metro.config.js` — aliases Node's `punycode` to the userland package (markdown-it on RN)

## Troubleshooting

- **"Unable to locate a Java Runtime"** from gradlew: `JAVA_HOME` isn't set in that shell - export
  it (see Environment) and retry.
- **"Cannot find a Java installation matching: {languageVersion=17}"**: the Kotlin toolchain needs
  JDK 17 (JBR 21 is not accepted) - `brew install --cask temurin@17`, then rerun. If it tried to
  auto-download from foojay.io and failed, that is the network blocking the fallback; the brew
  install is the fix.
- **"SDK location not found"** from gradle: `ANDROID_HOME` isn't set in that shell. The repo keeps
  `apps/mobile/android/local.properties` (gitignored, machine-local) with
  `sdk.dir=<your SDK path>` so builds work without the env var - create it with your path if
  missing.
- **Emulator starts but the screen stays black**: it is still booting; wait for
  `adb shell getprop sys.boot_completed` = `1`.
- **App can't reach the backend on a physical device**: host firewall blocks inbound - use the
  emulator instead (everything stays on the Mac).
- **Reset the emulator**: `adb emu kill`, then start it again (or `emulator -avd doktok -wipe-data`).

## Layout

- `App.tsx` — navigation container + bottom tabs (Documents / Scan / Chat / Insights / Settings),
  gated by auth
- `src/theme.ts` — design tokens mirroring `apps/ui/src/styles.css`
- `src/config.ts` — backend URL resolution (`app.json` `extra.backendUrl`)
- `src/api/` — typed API client (`client.ts`), auth API (`auth.ts`)
- `src/auth/AuthContext.tsx` — token in expo-secure-store, restored + validated on start
- `src/screens/` — LoginScreen + placeholders (real screens land per ticket, M1.3+)
