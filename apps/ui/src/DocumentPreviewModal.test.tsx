import { render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { DocumentPreviewModal } from "./DocumentPreviewModal";
import type { DokDocument } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

/** Media loads go through the (authenticated) fetch and come back as blob URLs. */
function stubMediaFetch() {
  const fetchMock = vi.fn(
    async () => new Response(new Blob(["x"], { type: "application/octet-stream" })),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function doc(overrides: Partial<DokDocument> = {}): DokDocument {
  return {
    id: "d1",
    original_filename: "scan.png",
    detected_mime: "image/png",
    title: "scan",
    status: "active",
    created_at: "2026-06-11T00:00:00Z",
    metadata: {},
    ...overrides,
  };
}

test("renders an image preview with new-tab + download and a safe rel", async () => {
  const fetchMock = stubMediaFetch();
  render(<DocumentPreviewModal doc={doc()} onClose={() => {}} />);

  // The image bytes load through the authenticated fetch and render as a blob object URL.
  const img = (await screen.findByAltText("Preview of scan.png")) as HTMLImageElement;
  expect(img.getAttribute("src")).toBe("blob:mock");
  expect(fetchMock).toHaveBeenCalledWith("/api/v1/documents/d1/file");

  const newTab = screen.getByRole("link", { name: /Open in new tab/ });
  expect(newTab).toHaveAttribute("target", "_blank");
  expect(newTab).toHaveAttribute("rel", "noopener noreferrer");

  expect(screen.getByRole("link", { name: "Download" })).toHaveAttribute(
    "href",
    "/api/v1/documents/d1/file?disposition=attachment",
  );
  expect(screen.getByLabelText("Close preview")).toBeInTheDocument();
});

test("previews an office document via its normalized PDF; download keeps the original", async () => {
  const fetchMock = stubMediaFetch();
  render(
    <DocumentPreviewModal
      doc={doc({
        detected_mime:
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        original_filename: "report.docx",
        metadata: { system_document: "normalized/searchable.pdf" },
      })}
      onClose={() => {}}
    />,
  );

  const frame = await screen.findByTitle("Preview of report.docx");
  expect(frame.getAttribute("src")).toBe("blob:mock");
  expect(fetchMock).toHaveBeenCalledWith("/api/v1/documents/d1/file?variant=normalized");

  // Download still hands back the original .docx, not the converted PDF.
  expect(screen.getByRole("link", { name: "Download" })).toHaveAttribute(
    "href",
    "/api/v1/documents/d1/file?disposition=attachment",
  );
});

test("shows a not-previewable fallback for unsupported types", () => {
  render(
    <DocumentPreviewModal
      doc={doc({ detected_mime: "application/zip", original_filename: "archive.zip" })}
      onClose={() => {}}
    />,
  );
  expect(screen.getByText(/Preview is not available/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Open in new tab/ })).toBeInTheDocument();
});
