import { render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { DocumentPreviewModal } from "./DocumentPreviewModal";
import type { DokDocument } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
});

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

test("renders an image preview with new-tab + download and a safe rel", () => {
  render(<DocumentPreviewModal doc={doc()} onClose={() => {}} />);

  const img = screen.getByAltText("Preview of scan.png") as HTMLImageElement;
  expect(img.getAttribute("src")).toBe("/api/v1/documents/d1/file");

  const newTab = screen.getByRole("link", { name: /Open in new tab/ });
  expect(newTab).toHaveAttribute("target", "_blank");
  expect(newTab).toHaveAttribute("rel", "noopener noreferrer");

  expect(screen.getByRole("link", { name: "Download" })).toHaveAttribute(
    "href",
    "/api/v1/documents/d1/file?disposition=attachment",
  );
  expect(screen.getByLabelText("Close preview")).toBeInTheDocument();
});

test("previews an office document via its normalized PDF; download keeps the original", () => {
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

  const frame = screen.getByTitle("Preview of report.docx");
  expect(frame.getAttribute("src")).toBe("/api/v1/documents/d1/file?variant=normalized");

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
