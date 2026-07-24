import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { GlobalDropOverlay, INGEST_FILES_EVENT } from "./GlobalDropOverlay";

function fileDrag(type: string): Event {
  const ev = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperty(ev, "dataTransfer", { value: { types: ["Files"], files: [] } });
  return ev;
}

afterEach(() => {
  vi.restoreAllMocks();
  window.location.hash = "";
});

test("dragging files anywhere shows the overlay and switches to Overview", () => {
  window.location.hash = "#/documents";
  render(<GlobalDropOverlay />);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

  fireEvent(window, fileDrag("dragenter"));

  expect(screen.getByRole("dialog", { name: "Drop files to ingest" })).toBeInTheDocument();
  expect(window.location.hash).toBe("#/overview");
});

test("non-file drags never show the overlay", () => {
  render(<GlobalDropOverlay />);
  const ev = new Event("dragenter", { bubbles: true, cancelable: true });
  Object.defineProperty(ev, "dataTransfer", { value: { types: ["text/plain"], files: [] } });
  fireEvent(window, ev);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("leaving the window hides the overlay (nested enter/leave pairs)", () => {
  render(<GlobalDropOverlay />);
  fireEvent(window, fileDrag("dragenter")); // depth 1
  fireEvent(window, fileDrag("dragenter")); // depth 2 (crossing a child element)
  fireEvent(window, fileDrag("dragleave")); // depth 1 - still visible
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  fireEvent(window, fileDrag("dragleave")); // depth 0 - hidden
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("Escape hides the overlay", () => {
  render(<GlobalDropOverlay />);
  fireEvent(window, fileDrag("dragenter"));
  fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("dropping on the overlay closes it and forwards the files to the dropzone", () => {
  const received: File[][] = [];
  window.addEventListener(INGEST_FILES_EVENT, (e) => {
    received.push((e as CustomEvent<{ files: File[] }>).detail.files);
  });
  render(<GlobalDropOverlay />);
  fireEvent(window, fileDrag("dragenter"));

  const file = new File(["x"], "a.pdf", { type: "application/pdf" });
  fireEvent.drop(screen.getByRole("dialog"), { dataTransfer: { files: [file] } });

  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(received).toHaveLength(1);
  expect(received[0][0].name).toBe("a.pdf");
});

test("a stray window drop is swallowed (browser never navigates to the file)", () => {
  render(<GlobalDropOverlay />);
  const ev = new Event("drop", { bubbles: true, cancelable: true });
  Object.defineProperty(ev, "dataTransfer", { value: { types: ["Files"], files: [] } });
  fireEvent(window, ev);
  expect(ev.defaultPrevented).toBe(true);
});
