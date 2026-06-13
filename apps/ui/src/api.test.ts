import { expect, test } from "vitest";

import { parseSse } from "./api";

test("parseSse extracts complete frames and carries the trailing partial", () => {
  const buffer =
    `event: meta\ndata: ${JSON.stringify({ type: "meta", rewritten_query: "q" })}\n\n` +
    `event: token\ndata: ${JSON.stringify({ type: "token", delta: "hi" })}\n\n` +
    'event: token\ndata: {"type": "token", "delta": "par'; // partial frame, no terminator

  const { events, rest } = parseSse(buffer);

  expect(events.map((e) => e.type)).toEqual(["meta", "token"]);
  expect(events[0].rewritten_query).toBe("q");
  expect(events[1].delta).toBe("hi");
  expect(rest).toContain('"delta": "par'); // the unterminated frame is carried forward
});

test("parseSse ignores malformed frames without throwing", () => {
  const buffer = "data: not-json\n\n" + `data: ${JSON.stringify({ type: "done", grounded: true })}\n\n`;
  const { events } = parseSse(buffer);
  expect(events).toHaveLength(1);
  expect(events[0].type).toBe("done");
});
