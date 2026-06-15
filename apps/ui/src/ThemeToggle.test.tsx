import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test } from "vitest";

import { ThemeToggle } from "./ThemeToggle";

beforeEach(() => {
  localStorage.clear();
  document.documentElement.setAttribute("data-theme", "dark");
});

afterEach(() => {
  localStorage.clear();
});

test("reflects the current theme as a switch (dark = checked)", () => {
  render(<ThemeToggle />);
  const sw = screen.getByRole("switch", { name: "Dark mode" });
  expect(sw).toHaveAttribute("aria-checked", "true");
});

test("toggling flips the theme, persists it, and updates <html data-theme>", async () => {
  render(<ThemeToggle />);
  const sw = screen.getByRole("switch", { name: "Dark mode" });

  await userEvent.click(sw);
  expect(sw).toHaveAttribute("aria-checked", "false");
  expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  expect(localStorage.getItem("doktok-theme")).toBe("light");

  await userEvent.click(sw);
  expect(sw).toHaveAttribute("aria-checked", "true");
  expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  expect(localStorage.getItem("doktok-theme")).toBe("dark");
});
