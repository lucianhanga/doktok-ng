import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";

import { OcrBoxOverlay } from "./OcrBoxOverlay";

const pages = [
  {
    page_number: 1,
    width_px: 100,
    height_px: 200,
    dpi: 200,
    lines: [{ text: "Hello world", x0: 10, y0: 20, x1: 60, y1: 40 }],
  },
];

test("draws OCR boxes over the page image and toggles them", async () => {
  render(<OcrBoxOverlay docId="d1" pages={pages} />);

  expect(screen.getByAltText("Page 1")).toBeInTheDocument();
  // Each line becomes a box positioned by proportion, with its text as the tooltip.
  const box = screen.getByTitle("Hello world");
  expect(box).toBeInTheDocument();
  expect(box.style.left).toBe("10%"); // x0/width_px
  expect(box.style.width).toBe("50%"); // (x1-x0)/width_px

  // "Show text regions" is on by default; unchecking hides the boxes.
  await userEvent.click(screen.getByRole("checkbox"));
  expect(screen.queryByTitle("Hello world")).not.toBeInTheDocument();
});
