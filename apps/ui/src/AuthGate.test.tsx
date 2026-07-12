import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { AuthGate } from "./AuthGate";
import { clearSession, setSession } from "./session";

afterEach(() => {
  vi.unstubAllGlobals();
  clearSession();
});

function stub(loginEnabled: boolean) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/auth/config")) {
        return new Response(JSON.stringify({ login_enabled: loginEnabled }), { status: 200 });
      }
      return new Response(JSON.stringify({}), { status: 200 }); // /preferences etc.
    }),
  );
}

test("login disabled runs token-free and renders the app", async () => {
  stub(false);
  render(
    <AuthGate>
      <div>PROTECTED</div>
    </AuthGate>,
  );
  await waitFor(() => expect(screen.getByText("PROTECTED")).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: "Sign in" })).not.toBeInTheDocument();
});

test("login enabled with no session shows the login screen", async () => {
  stub(true);
  render(
    <AuthGate>
      <div>PROTECTED</div>
    </AuthGate>,
  );
  await waitFor(() => expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument());
  expect(screen.queryByText("PROTECTED")).not.toBeInTheDocument();
});

test("login enabled with an existing session renders the app and a sign-out bar", async () => {
  stub(true);
  setSession("jwt-abc", { id: "u1", email: "dev-admin@doktok.local", role: "admin", tenant_id: "dev" });
  render(
    <AuthGate>
      <div>PROTECTED</div>
    </AuthGate>,
  );
  await waitFor(() => expect(screen.getByText("PROTECTED")).toBeInTheDocument());
  expect(screen.getByText(/signed in as dev-admin@doktok\.local/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Log out" })).toBeInTheDocument();
});
