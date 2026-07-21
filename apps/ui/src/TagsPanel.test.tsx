import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { TagsPanel } from "./TagsPanel";

const TAG1 = {
  id: "t1",
  tenant_id: "t1",
  name: "Rome Trip",
  normalized: "rome trip",
  description: "the trip",
  color: "teal",
  status: "active",
  merged_into: null,
  scope: "tenant",
  owner_user_id: null,
  created_at: "2026-07-21T00:00:00Z",
  updated_at: null,
  document_count: 3,
};
const TAG2 = {
  ...TAG1,
  id: "t2",
  name: "Receipts",
  normalized: "receipts",
  description: "",
  color: "amber",
  document_count: 0,
};

afterEach(() => {
  vi.restoreAllMocks();
});

/** Fetch mock for the tags API: list + create (with 409 shapes) + patch + delete. */
function mockTags(opts: {
  list?: unknown[];
  createResponders?: Array<() => Response>;
  deleteResponders?: Array<() => Response>;
}) {
  const calls: { url: string; method: string; body?: string }[] = [];
  const createResponders = [...(opts.createResponders ?? [])];
  const deleteResponders = [...(opts.deleteResponders ?? [])];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: init?.body as string | undefined });
      if (url === "/api/v1/tags" && method === "GET")
        return new Response(JSON.stringify(opts.list ?? []), { status: 200 });
      if (url === "/api/v1/tags" && method === "POST") {
        const respond = createResponders.shift();
        return respond
          ? respond()
          : new Response(
              JSON.stringify({ ...TAG1, id: "new-1", name: JSON.parse(init!.body as string).name }),
              { status: 201 },
            );
      }
      if (url.startsWith("/api/v1/tags/") && method === "PATCH")
        return new Response(JSON.stringify({ ...TAG1, ...JSON.parse(init!.body as string) }), {
          status: 200,
        });
      if (url.startsWith("/api/v1/tags/") && method === "DELETE") {
        const respond = deleteResponders.shift();
        return respond ? respond() : new Response(null, { status: 204 });
      }
      return new Response("{}", { status: 404 });
    }),
  );
  return calls;
}

const conflict = (detail: unknown, status = 409) =>
  new Response(JSON.stringify({ detail }), { status });

test("lists tags with swatch, description and usage count; empty state when none", async () => {
  mockTags({ list: [TAG1, TAG2] });
  const first = render(<TagsPanel />);
  await waitFor(() => expect(screen.getByText("Rome Trip")).toBeInTheDocument());
  expect(screen.getByText("the trip")).toBeInTheDocument();
  expect(screen.getByText("3 docs")).toBeInTheDocument();
  expect(screen.getByText("0 docs")).toBeInTheDocument();
  first.unmount();

  mockTags({ list: [] });
  render(<TagsPanel />);
  await waitFor(() => expect(screen.getByText(/No tags yet/)).toBeInTheDocument());
});

test("create flow with a near-miss warning and create-anyway retry", async () => {
  const calls = mockTags({
    list: [TAG1],
    createResponders: [
      () => conflict({ code: "similar", similar: [{ id: "t1", name: "Rome Trip" }] }),
    ],
  });
  render(<TagsPanel />);
  await waitFor(() => expect(screen.getByText("Rome Trip")).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "New tag" }));
  fireEvent.change(screen.getByLabelText("Tag name"), { target: { value: "Trip Rome" } });
  fireEvent.click(screen.getByRole("radio", { name: "color teal" }));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  // The 409 similar warning shows, naming the candidate; "Create anyway" resends with the flag.
  await waitFor(() => expect(screen.getByText(/Similar tags exist: Rome Trip/)).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "Create anyway" }));
  await waitFor(() =>
    expect(screen.queryByLabelText("New tag")).not.toBeInTheDocument(),
  );
  const posts = calls.filter((c) => c.method === "POST");
  expect(JSON.parse(posts[0].body!).allow_similar).toBe(false);
  expect(JSON.parse(posts[1].body!).allow_similar).toBe(true);
});

test("an exact duplicate warning blocks creation without a retry", async () => {
  mockTags({
    list: [TAG1],
    createResponders: [() => conflict({ code: "duplicate", existing: { id: "t1", name: "Rome Trip" } })],
  });
  render(<TagsPanel />);
  await waitFor(() => expect(screen.getByText("Rome Trip")).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "New tag" }));
  fireEvent.change(screen.getByLabelText("Tag name"), { target: { value: "rome trip" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(screen.getByText(/already exists/)).toBeInTheDocument(),
  );
  expect(screen.queryByRole("button", { name: "Create anyway" })).not.toBeInTheDocument();
});

test("edit patches the tag", async () => {
  const calls = mockTags({ list: [TAG1] });
  render(<TagsPanel />);
  await waitFor(() => expect(screen.getByText("Rome Trip")).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  const nameInput = screen.getByLabelText("Tag name");
  fireEvent.change(nameInput, { target: { value: "Rome 2026" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(screen.queryByLabelText(/Edit tag/)).not.toBeInTheDocument());
  const patch = calls.find((c) => c.method === "PATCH");
  expect(patch!.url).toContain("/api/v1/tags/t1");
  expect(JSON.parse(patch!.body!)).toEqual({ name: "Rome 2026", description: "the trip", color: "teal" });
});

test("delete confirms, shows the in-use count, and force-deletes", async () => {
  const calls = mockTags({
    list: [TAG1],
    deleteResponders: [() => conflict({ code: "in_use", document_count: 3 })],
  });
  render(<TagsPanel />);
  await waitFor(() => expect(screen.getByText("Rome Trip")).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  const dialog = await screen.findByRole("alertdialog");
  fireEvent.click(within(dialog).getByRole("button", { name: /^Delete$/ }));
  // The in-use warning names the count; "Delete anyway" forces it.
  await waitFor(() => expect(screen.getByText(/Used on 3 documents/)).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "Delete anyway" }));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  const deletes = calls.filter((c) => c.method === "DELETE");
  expect(deletes[0].url).not.toContain("force");
  expect(deletes[1].url).toContain("force=true");
});
