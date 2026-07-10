import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { AdminPanel } from "./AdminPanel";

afterEach(() => vi.unstubAllGlobals());

const USERS = [
  { id: "u1", email: "alice@x.com", display_name: "Alice", role: "editor", status: "active" },
  { id: "u2", email: "bob@x.com", display_name: "Bob", role: "viewer", status: "deactivated" },
];

function stub(onCall?: (url: string, method: string, body: unknown) => unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(init.body as string) : undefined;
      const override = onCall?.(url, method, body);
      if (override !== undefined) return new Response(JSON.stringify(override), { status: 200 });
      if (method === "GET" && url.endsWith("/admin/users")) {
        return new Response(JSON.stringify(USERS), { status: 200 });
      }
      if (method === "GET") return new Response(JSON.stringify([]), { status: 200 });
      return new Response(JSON.stringify({}), { status: 200 });
    }),
  );
}

test("lists members with role and status", async () => {
  stub();
  render(<AdminPanel />);
  await waitFor(() => expect(screen.getByRole("cell", { name: "alice@x.com" })).toBeInTheDocument());
  expect(screen.getByRole("cell", { name: "bob@x.com" })).toBeInTheDocument();
  // Deactivated member shows a reactivate action.
  expect(screen.getByRole("button", { name: "Reactivate" })).toBeInTheDocument();
});

test("deactivates an active member", async () => {
  const calls: string[] = [];
  stub((url, method) => {
    if (method === "POST" && url.endsWith("/u1/deactivate")) {
      calls.push(url);
      return { ...USERS[0], status: "deactivated" };
    }
    return undefined;
  });
  render(<AdminPanel />);
  await waitFor(() => expect(screen.getByRole("cell", { name: "alice@x.com" })).toBeInTheDocument());
  await userEvent.click(screen.getAllByRole("button", { name: "Deactivate" })[0]);
  await waitFor(() => expect(calls[0]).toContain("/api/v1/admin/users/u1/deactivate"));
});

test("invite reveals a one-time token", async () => {
  stub((url, method) => {
    if (method === "POST" && url.endsWith("/admin/invitations")) {
      return { user_id: "u9", email: "new@x.com", role: "viewer", token: "invite-tok-xyz", expires_at: "2026-07-17T00:00:00Z" };
    }
    return undefined;
  });
  render(<AdminPanel />);
  await waitFor(() => expect(screen.getByRole("cell", { name: "alice@x.com" })).toBeInTheDocument());
  await userEvent.type(screen.getByLabelText("New member email"), "new@x.com");
  await userEvent.click(screen.getByRole("button", { name: "Invite" }));
  await waitFor(() => expect(screen.getByText("invite-tok-xyz")).toBeInTheDocument());
});
