import * as Notifications from "expo-notifications";
import { Platform } from "react-native";

// Local notifications for ingestion completion (#775): the channel + permission are set up lazily
// on first use, so the app never nags at startup. The handler makes banners show even while the
// app is in the foreground (default is to swallow them).
const CHANNEL_ID = "ingestion";
let ready = false;

async function ensureReady(): Promise<boolean> {
  if (ready) return true;
  Notifications.setNotificationHandler({
    handleNotification: async () => ({
      shouldPlaySound: false,
      shouldSetBadge: false,
      shouldShowBanner: true,
      shouldShowList: true,
    }),
  });
  if (Platform.OS === "android") {
    await Notifications.setNotificationChannelAsync(CHANNEL_ID, {
      name: "Ingestion",
      importance: Notifications.AndroidImportance.DEFAULT,
    });
  }
  const perms = await Notifications.getPermissionsAsync();
  if (!perms.granted) {
    const req = await Notifications.requestPermissionsAsync();
    if (!req.granted) return false;
  }
  ready = true;
  return true;
}

/** Fire an immediate local notification (no-op when the user denied permission). */
export async function notifyIngestion(filename: string, ok: boolean, detail?: string) {
  if (!(await ensureReady())) return;
  await Notifications.scheduleNotificationAsync({
    content: {
      title: ok ? "Document ready" : "Document failed",
      body: ok ? filename : `${filename}${detail ? ` — ${detail}` : ""}`,
      ...(Platform.OS === "android" ? { channelId: CHANNEL_ID } : {}),
    },
    trigger: null, // immediately
  });
}
