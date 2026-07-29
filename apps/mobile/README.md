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

## Run the emulator

```bash
~/Library/Android/sdk/emulator/emulator -avd doktok -gpu auto -no-snapshot-save -no-metrics
```

Leave it running in its own terminal. Booted when this prints `1`:

```bash
adb devices
adb shell getprop sys.boot_completed
```

## Build + install the app on the emulator

```bash
cd apps/mobile
pnpm exec expo run:android
```

First build downloads gradle + Android build deps (~1-2GB once) and takes 5-15 min; later builds
~1 min. It installs the dev client on the running emulator and starts it. After that, code
changes hot-reload via `pnpm start` (Metro) - no rebuild needed except for native dependency
changes (e.g. the camera scanner in M2).

## Backend URL + login

- The emulator reaches the Mac's backend as `http://10.0.2.2:8000` (Android's loopback alias for
  the host). That is the default in `app.json -> expo.extra.backendUrl`, so nothing to set.
- A PHYSICAL device instead needs the Mac's LAN IP there (e.g. `http://192.168.1.20:8000`) AND a
  firewall that allows inbound to 8000 + 8081 - with a locked-down firewall, use the emulator.
- Login: tenant `dev`, `dev-admin@doktok.local`, password = `DOKTOK_DEV_SEED_PASSWORD` from `.env`.

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
