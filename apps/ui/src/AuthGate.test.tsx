import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

test("logging in through the form transitions to the app without a reload", async () => {
  // Regression: after a successful login the gate must show the app immediately (previously it stayed
  // on the login screen until a manual refresh).
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/auth/config")) {
        return new Response(JSON.stringify({ login_enabled: true }), { status: 200 });
      }
      if (url.endsWith("/auth/login") && init?.method === "POST") {
        return new Response(
          JSON.stringify({
            access_token: "jwt-1",
            token_type: "bearer",
            expires_in: 3600,
            user: { id: "u1", tenant_id: "dev", email: "dev-admin@doktok.local", display_name: "", role: "admin" },
          }),
          { status: 200 },
        );
      }
      return new Response(JSON.stringify({}), { status: 200 }); // /preferences
    }),
  );
  render(
    <AuthGate>
      <div>PROTECTED</div>
    </AuthGate>,
  );
  await waitFor(() => expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument());
  await userEvent.type(screen.getByLabelText("Tenant"), "dev");
  await userEvent.type(screen.getByLabelText("Email"), "dev-admin@doktok.local");
  await userEvent.type(screen.getByLabelText("Password"), "testtesttest"); // pragma: allowlist secret
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  await waitFor(() => expect(screen.getByText("PROTECTED")).toBeInTheDocument());
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
