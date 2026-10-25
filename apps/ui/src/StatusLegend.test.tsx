import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { StatusLegend } from "./StatusLegend";

test("the legend is collapsed until the (?) button is clicked", () => {
  render(<StatusLegend />);
  const toggle = screen.getByRole("button", { name: /explain status colors/i });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByText("Queued / waiting")).not.toBeInTheDocument();
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByText("Processing")).toBeInTheDocument();
  expect(screen.getByText("Queued / waiting")).toBeInTheDocument();
  expect(screen.getByText("Failed")).toBeInTheDocument();
  expect(screen.getByText("Needs attention")).toBeInTheDocument();
  expect(screen.getByText("Done")).toBeInTheDocument();
  fireEvent.click(toggle);
  expect(screen.queryByText("Queued / waiting")).not.toBeInTheDocument();
});

test("the legend enumerates the granular stages with their color codes", () => {
  render(<StatusLegend />);
  fireEvent.click(screen.getByRole("button", { name: /explain status colors/i }));
  for (const label of ["Extract / OCR", "Chunk & embed", "NER", "Entity graph", "Relations", "Records"]) {
    expect(screen.getByText(label)).toBeInTheDocument();
  }
});
