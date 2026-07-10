import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { AdminPanel } from "./AdminPanel";

afterEach(() => vi.unstubAllGlobals());

const CTX = {
  tenant_id: "3f9a2c1b-0000-4000-8000-000000000000",
  tenant_name: "Hanga Household",
  user_id: "self-x", // not one of the members below, so no self-guard interferes
  email: "op@x.com",
  role: "admin",
};
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
      if (method === "GET" && url.endsWith("/admin/context")) {
        return new Response(JSON.stringify(CTX), { status: 200 });
      }
      if (method === "GET" && url.endsWith("/admin/users")) {
        return new Response(JSON.stringify(USERS), { status: 200 });
      }
      if (method === "GET") return new Response(JSON.stringify([]), { status: 200 });
      return new Response(JSON.stringify({}), { status: 200 });
    }),
  );
}

test("shows the tenant context header and members", async () => {
  stub();
  render(<AdminPanel />);
  await waitFor(() => expect(screen.getByRole("heading", { name: "Hanga Household" })).toBeInTheDocument());
  expect(screen.getByRole("cell", { name: /alice@x\.com/ })).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: /bob@x\.com/ })).toBeInTheDocument();
  // Deactivated member offers Reactivate.
  expect(screen.getByRole("button", { name: "Reactivate" })).toBeInTheDocument();
});

test("deactivating a member requires confirmation", async () => {
  const calls: string[] = [];
  stub((url, method) => {
    if (method === "POST" && url.endsWith("/u1/deactivate")) {
      calls.push(url);
      return { ...USERS[0], status: "deactivated" };
    }
    return undefined;
  });
  render(<AdminPanel />);
  await waitFor(() => expect(screen.getByRole("cell", { name: /alice@x\.com/ })).toBeInTheDocument());
  await userEvent.click(screen.getByRole("button", { name: "Deactivate" })); // alice's row
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText(/immediately blocks all their sessions/i)).toBeInTheDocument();
  await userEvent.click(within(dialog).getByRole("button", { name: "Deactivate" }));
  await waitFor(() => expect(calls[0]).toContain("/api/v1/admin/users/u1/deactivate"));
});

test("inviting reveals a one-time token", async () => {
  stub((url, method) => {
    if (method === "POST" && url.endsWith("/admin/invitations")) {
      return {
        user_id: "u9",
        email: "new@x.com",
        role: "viewer",
        token: "invite-tok-xyz",
        expires_at: "2026-07-17T00:00:00Z",
      };
    }
    return undefined;
  });
  render(<AdminPanel />);
  await waitFor(() => expect(screen.getByRole("cell", { name: /alice@x\.com/ })).toBeInTheDocument());
  await userEvent.click(screen.getByRole("button", { name: "Invite member" })); // opens the panel
  await userEvent.type(screen.getByLabelText("Invite email"), "new@x.com");
  await userEvent.click(screen.getByRole("button", { name: "Invite member" })); // submit (link mode)
  await waitFor(() => expect(screen.getByText("invite-tok-xyz")).toBeInTheDocument());
});

test("the caller's own row cannot be deactivated", async () => {
  // ctx.user_id === u1, so alice is 'you' and Deactivate is disabled on her row.
  stub((url, method) => {
    if (method === "GET" && url.endsWith("/admin/context")) return { ...CTX, user_id: "u1" };
    return undefined;
  });
  render(<AdminPanel />);
  await waitFor(() => expect(screen.getByText("(you)")).toBeInTheDocument());
  const selfRow = screen.getByRole("cell", { name: /alice@x\.com/ }).closest("tr")!;
  expect(within(selfRow).getByRole("button", { name: "Deactivate" })).toBeDisabled();
});
