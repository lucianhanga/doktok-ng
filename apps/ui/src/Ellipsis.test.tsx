import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { Ellipsis } from "./Ellipsis";

const LONG =
  "a-very-long-original-filename-that-would-otherwise-overflow-its-container.pdf";

test("renders the full text and exposes it as a title tooltip", () => {
  render(<Ellipsis text={LONG} />);
  const el = screen.getByText(LONG);
  expect(el).toHaveAttribute("title", LONG);
  expect(el).toHaveClass("truncate");
});

test("merges an extra className alongside the truncate utility", () => {
  render(<Ellipsis text={LONG} className="chat-source-title" />);
  const el = screen.getByText(LONG);
  expect(el).toHaveClass("truncate");
  expect(el).toHaveClass("chat-source-title");
});

test("can render as a different element and keeps the tooltip", () => {
  render(
    <table>
      <tbody>
        <tr>
          <Ellipsis as="td" text={LONG} />
        </tr>
      </tbody>
    </table>,
  );
  const el = screen.getByText(LONG);
  expect(el.tagName).toBe("TD");
  expect(el).toHaveAttribute("title", LONG);
});

test("uses text only for the tooltip when richer children are provided", () => {
  render(
    <Ellipsis text={LONG}>
      <span>visible label</span>
    </Ellipsis>,
  );
  const el = screen.getByText("visible label").parentElement;
  expect(el).toHaveAttribute("title", LONG);
  expect(el).toHaveClass("truncate");
});
