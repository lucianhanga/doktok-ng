import React, { useEffect, useState } from "react";
import * as FileSystem from "expo-file-system/legacy";
import { Image, type ImageStyle, type StyleProp } from "react-native";

import { useAuth } from "../auth/AuthContext";

// Bearer-authenticated image (#773): RN <Image> headers are unreliable on Android (it may drop
// them, and the backend then answers 401), so thumbnails are downloaded with the token into the
// cache once and rendered from the local file - the same mechanism that makes open-PDF work.
export function AuthImage({
  uri,
  style,
  resizeMode = "cover",
}: {
  uri: string;
  style?: StyleProp<ImageStyle>;
  resizeMode?: "cover" | "contain" | "stretch" | "center";
}) {
  const { token } = useAuth();
  const [localUri, setLocalUri] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLocalUri(null);
    if (!token || !uri) return;
    const name = uri.split("/").pop()?.split("?")[0] ?? "img";
    const target = `${FileSystem.cacheDirectory}thumb-${name}-${Math.abs(hash(uri))}`;
    FileSystem.getInfoAsync(target).then((info) => {
      if (info.exists) {
        if (alive) setLocalUri(target);
        return;
      }
      FileSystem.downloadAsync(uri, target, {
        headers: { Authorization: `Bearer ${token}` },
      })
        .then((res) => {
          if (alive && res.status === 200) setLocalUri(target);
        })
        .catch(() => {});
    });
    return () => {
      alive = false;
    };
  }, [uri, token]);

  if (!localUri) return null;
  return <Image source={{ uri: localUri }} style={style} resizeMode={resizeMode} />;
}

function hash(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return h;
}
