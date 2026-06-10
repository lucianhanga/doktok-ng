import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import App from "./App";
import type { HealthStatus } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
});

function mockHealth(payload: HealthStatus) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(payload), { status: 200 })),
  );
}

test("renders the DokTok NG shell", async () => {
  mockHealth({ status: "ok", service: "doktok-ng-backend", version: "0.0.0", environment: "test" });
  render(<App />);
  expect(screen.getByRole("heading", { level: 1, name: "DokTok NG" })).toBeInTheDocument();
  // Let the async health fetch settle to avoid act() warnings.
  await waitFor(() => expect(screen.getByText("doktok-ng-backend")).toBeInTheDocument());
});

test("shows backend status when health succeeds", async () => {
  mockHealth({ status: "ok", service: "doktok-ng-backend", version: "0.0.0", environment: "test" });
  render(<App />);
  await waitFor(() => {
    expect(screen.getByText("doktok-ng-backend")).toBeInTheDocument();
  });
});

test("shows an error when the backend is unreachable", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      throw new Error("network down");
    }),
  );
  render(<App />);
  await waitFor(() => {
    expect(screen.getByRole("alert")).toHaveTextContent("Backend unreachable");
  });
});
