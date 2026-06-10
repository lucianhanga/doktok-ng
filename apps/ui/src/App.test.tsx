import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import App from "./App";
import type { HealthStatus, IngestionJob } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
});

const HEALTH: HealthStatus = {
  status: "ok",
  service: "doktok-ng-backend",
  version: "0.0.0",
  environment: "test",
};

function mockRoutes(jobs: IngestionJob[] = []) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/v1/ingestion/jobs")) {
        return new Response(JSON.stringify(jobs), { status: 200 });
      }
      return new Response(JSON.stringify(HEALTH), { status: 200 });
    }),
  );
}

test("renders the DokTok NG shell", async () => {
  mockRoutes();
  render(<App />);
  expect(screen.getByRole("heading", { level: 1, name: "DokTok NG" })).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText("doktok-ng-backend")).toBeInTheDocument());
});

test("shows backend status by default", async () => {
  mockRoutes();
  render(<App />);
  await waitFor(() => expect(screen.getByText("doktok-ng-backend")).toBeInTheDocument());
});

test("switches to the ingestion view", async () => {
  mockRoutes([]);
  render(<App />);
  await screen.findByText("doktok-ng-backend");

  await userEvent.click(screen.getByRole("button", { name: "Ingestion" }));

  await waitFor(() =>
    expect(screen.getByText(/No ingestion jobs yet/i)).toBeInTheDocument(),
  );
});
