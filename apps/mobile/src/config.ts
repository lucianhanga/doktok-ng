import Constants from "expo-constants";

// Backend URL resolution (#771). Android EMULATOR maps the host machine's loopback as 10.0.2.2,
// so that is the dev default; a physical device on the same LAN needs the Mac's LAN IP
// (e.g. http://192.168.1.20:8000). Override per build in app.json -> expo.extra.backendUrl.
const fromConfig = Constants.expoConfig?.extra?.backendUrl as string | undefined;

export const BACKEND_URL = (fromConfig ?? "http://10.0.2.2:8000").replace(/\/+$/, "");
