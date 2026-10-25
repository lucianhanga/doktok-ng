import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

// Mirrors h0me/apps/web/src/pwa.test.ts: assert the static PWA pieces Chrome needs to offer
// "Install app" stay wired up (manifest shape, real icon files, index.html links).
const pub = resolve(__dirname, "../public");
const manifest = JSON.parse(readFileSync(resolve(pub, "manifest.webmanifest"), "utf8")) as {
  name: string;
  short_name: string;
  start_url: string;
  display: string;
  theme_color: string;
  background_color: string;
  icons: { src: string; sizes: string; type: string }[];
};

describe("PWA installability", () => {
  it("ships a web app manifest Chrome can install from", () => {
    expect(manifest.name).toBe("DokTok NG");
    expect(manifest.short_name).toBe("DokTok");
    expect(manifest.start_url).toBe("/");
    expect(manifest.display).toBe("standalone");
    expect(manifest.theme_color).toMatch(/^#[0-9a-f]{6}$/i);
    expect(manifest.background_color).toMatch(/^#[0-9a-f]{6}$/i);
  });

  it("references only icon files that exist, with 192 and 512 PNG icons present", () => {
    const pngSizes = manifest.icons
      .filter((icon) => icon.type === "image/png")
      .map((icon) => icon.sizes);
    expect(pngSizes).toContain("192x192");
    expect(pngSizes).toContain("512x512");
    for (const icon of manifest.icons) {
      expect(icon.src.startsWith("/")).toBe(true);
      expect(() => readFileSync(resolve(pub, icon.src.slice(1)))).not.toThrow();
    }
  });

  it("links the icon, apple-touch-icon, and manifest from index.html", () => {
    const html = readFileSync(resolve(__dirname, "../index.html"), "utf8");
    expect(html).toContain('href="/icon.svg"');
    expect(html).toContain('href="/apple-touch-icon.png"');
    expect(html).toContain('href="/manifest.webmanifest"');
    expect(html).toContain('name="theme-color"');
  });
});
