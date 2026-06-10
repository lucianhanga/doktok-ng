import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import App from "./App";
import type { HealthStatus } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
});

const HEALTH: HealthStatus = {
  status: "ok",
  service: "doktok-ng-backend",
  version: "0.0.0",
  environment: "test",
};

function mockRoutes() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/v1/stats")) {
        return new Response(JSON.stringify({ documents: 0, jobs: {}, entities: 0 }), {
          status: 200,
        });
      }
      if (url.includes("/health")) {
        return new Response(JSON.stringify(HEALTH), { status: 200 });
      }
      // jobs / documents / audit / search / entities
      return new Response(JSON.stringify([]), { status: 200 });
    }),
  );
}

test("renders the DokTok NG shell with the Overview landing", async () => {
  mockRoutes();
  render(<App />);
  expect(screen.getByRole("heading", { level: 1, name: "DokTok NG" })).toBeInTheDocument();
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: "Overview" })).toBeInTheDocument(),
  );
});

test("Status tab shows backend health", async () => {
  mockRoutes();
  render(<App />);
  await userEvent.click(screen.getByRole("button", { name: "Status" }));
  await waitFor(() => expect(screen.getByText("doktok-ng-backend")).toBeInTheDocument());
});

test("Ingestion tab shows the jobs view", async () => {
  mockRoutes();
  render(<App />);
  await userEvent.click(screen.getByRole("button", { name: "Ingestion" }));
  await waitFor(() =>
    expect(screen.getByText(/No ingestion jobs yet/i)).toBeInTheDocument(),
  );
});
