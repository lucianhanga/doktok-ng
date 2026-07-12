import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { LoginScreen } from "./LoginScreen";
import { clearSession, hasSession } from "./session";

afterEach(() => {
  vi.unstubAllGlobals();
  clearSession();
});

function stubLogin(status: number, body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(body), { status })),
  );
}

async function fillAndSubmit() {
  await userEvent.type(screen.getByLabelText("Tenant"), "dev");
  await userEvent.type(screen.getByLabelText("Email"), "dev-admin@doktok.local");
  await userEvent.type(screen.getByLabelText("Password"), "seed-password-123");
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

test("successful login stores the session and calls onLoggedIn", async () => {
  stubLogin(200, {
    access_token: "jwt-xyz",
    token_type: "bearer",
    expires_in: 3600,
    user: { id: "u1", tenant_id: "dev", email: "dev-admin@doktok.local", display_name: "", role: "admin" },
  });
  const onLoggedIn = vi.fn();
  render(<LoginScreen onLoggedIn={onLoggedIn} />);
  await fillAndSubmit();
  await waitFor(() => expect(onLoggedIn).toHaveBeenCalled());
  expect(hasSession()).toBe(true);
});

test("a rejected login shows an error and stores no session", async () => {
  stubLogin(401, { detail: "invalid email or password" });
  render(<LoginScreen onLoggedIn={vi.fn()} />);
  await fillAndSubmit();
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent(/invalid tenant, email, or password/i),
  );
  expect(hasSession()).toBe(false);
});

test("a throttled login shows the rate-limit message", async () => {
  stubLogin(429, { detail: "too many login attempts" });
  render(<LoginScreen onLoggedIn={vi.fn()} />);
  await fillAndSubmit();
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/too many login attempts/i));
});
