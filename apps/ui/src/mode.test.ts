import { expect, test } from "vitest";

import { loadUiMode, saveUiMode } from "./mode";

test("UI mode defaults to simple (unset or corrupt stored value)", () => {
  localStorage.removeItem("doktok-ui-mode");
  expect(loadUiMode()).toBe("simple");
  localStorage.setItem("doktok-ui-mode", '"fancy"');
  expect(loadUiMode()).toBe("simple");
});

test("UI mode round-trips through persistence", () => {
  saveUiMode("advanced");
  expect(loadUiMode()).toBe("advanced");
  saveUiMode("simple");
  expect(loadUiMode()).toBe("simple");
});
