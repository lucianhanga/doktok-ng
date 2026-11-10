import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { existsSync } from "node:fs";
import { mkdtemp, rm } from "node:fs/promises";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const uiRoot = fileURLToPath(new URL("../", import.meta.url));
const chromeCandidates = [
  process.env.CHROME_BIN,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
].filter(Boolean);
const chromePath = chromeCandidates.find((candidate) => existsSync(candidate));
if (!chromePath) throw new Error("Chrome not found; set CHROME_BIN to a Chrome/Chromium executable");
if (typeof WebSocket !== "function") throw new Error("Node 22+ is required for the built-in WebSocket client");

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function freePort() {
  const server = createServer();
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  const port = typeof address === "object" && address ? address.port : 0;
  server.close();
  await once(server, "close");
  return port;
}

async function waitForHttp(url, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return response;
      lastError = new Error(`${response.status} ${response.statusText}`);
    } catch (error) {
      lastError = error;
    }
    await delay(100);
  }
  throw new Error(`Timed out waiting for ${url}: ${lastError}`);
}

const featureNames = [
  "chunk_embed",
  "doc_classify",
  "doc_metadata",
  "entities",
  "extract",
  "ner",
  "structured_records",
  "thumbnail",
];
const makeFeatures = (docs) => docs.flatMap((doc) =>
  featureNames.map((feature) => ({
    document_id: doc.id,
    feature,
    status: "done",
    feature_version: 1,
    attempts: 1,
    max_attempts: 3,
  })),
);
const makeProcessing = (docs) => Object.fromEntries(docs.map((doc) => [
  doc.id,
  {
    extraction_method: "text",
    ocr_outcome: "not_needed",
    page_count: doc.metadata.page_count,
    normalized_from_mime: "application/pdf",
    status: doc.status,
    features_done: 4,
    features_failed: doc.id === "active" ? 1 : 0,
  },
]));
const makeStats = (docs) => Object.fromEntries(docs.map((doc) => [
  doc.id,
  { entity_count: 2, chunk_count: 3, category: doc.mockCategory ?? "Invoices" },
]));
const makeTags = (docs) => Object.fromEntries(docs.map((doc) => [
  doc.id,
  [
    { id: "t1", name: "Rome Trip", color: "teal" },
    { id: "t2", name: "Receipts", color: "amber" },
  ],
]));

let documents = [
  {
    id: "failed",
    original_filename: "failed-document-with-a-long-name-that-must-stay-inside-the-viewport.pdf",
    title: "Failed document with a long title that must stay inside the viewport",
    status: "failed",
  },
  {
    id: "processing",
    original_filename: "processing-document-with-a-long-name-that-must-stay-inside-the-viewport.pdf",
    title: "Processing document with a long title that must stay inside the viewport",
    status: "processing",
  },
  {
    id: "active",
    original_filename: "active-document-with-a-long-name-that-must-stay-inside-the-viewport.pdf",
    title: "Active document with a long title that must stay inside the viewport",
    status: "active",
  },
].map((doc) => ({
  ...doc,
  detected_mime: "application/pdf",
  created_at: "2026-06-10T00:00:00Z",
  document_date: "2026-01-15",
  summary: "A summary used to exercise Advanced thumbnail metadata at narrow widths.",
  metadata: { page_count: 1 },
}));
let features = makeFeatures(documents);
let processing = makeProcessing(documents);
let stats = makeStats(documents);
let tags = makeTags(documents);
let tagCatalog = [];
const bulkTagCalls = [];
const listRequests = [];

function installDocuments(next) {
  documents = next;
  features = makeFeatures(documents);
  processing = makeProcessing(documents);
  stats = makeStats(documents);
  tags = makeTags(documents);
}

function documentPage(params) {
  const query = (params.get("q") ?? "").toLocaleLowerCase();
  const statuses = params.getAll("status");
  const categories = params.getAll("category");
  const selectedTags = params.getAll("tag");
  const filtered = documents.filter((doc) => {
    const title = `${doc.title ?? ""} ${doc.original_filename}`.toLocaleLowerCase();
    if (query && !title.includes(query)) return false;
    if (statuses.length > 0 && !statuses.includes(doc.status)) return false;
    if (categories.length > 0 && !categories.includes(stats[doc.id].category)) return false;
    if (selectedTags.length > 0) {
      const docTags = tags[doc.id].map((tag) => tag.id);
      const matches = params.get("tag_match") === "any"
        ? selectedTags.some((tag) => docTags.includes(tag))
        : selectedTags.every((tag) => docTags.includes(tag));
      if (!matches) return false;
    }
    if (params.get("needs_attention") === "true" && processing[doc.id].features_failed === 0) return false;
    if (params.get("unidentifiable") === "true" && !doc.unidentifiable) return false;
    return true;
  });
  const sort = params.get("sort") ?? "acquired";
  const dir = params.get("dir") === "asc" ? 1 : -1;
  filtered.sort((a, b) => {
    const values = (doc) => {
      if (sort === "title") return doc.title ?? doc.original_filename;
      if (sort === "created") return doc.document_date ?? "";
      if (sort === "category") return stats[doc.id].category;
      return doc.created_at;
    };
    return values(a).localeCompare(values(b)) * dir;
  });
  const start = Number(params.get("cursor") ?? "0");
  const limit = Number(params.get("limit") ?? "50");
  const items = filtered.slice(start, start + limit);
  const next = start + limit < filtered.length ? String(start + limit) : null;
  const ids = new Set(items.map((doc) => doc.id));
  return {
    items,
    total: filtered.length,
    next_cursor: next,
    features: features.filter((feature) => ids.has(feature.document_id)),
    processing: Object.fromEntries(Object.entries(processing).filter(([id]) => ids.has(id))),
    stats: Object.fromEntries(Object.entries(stats).filter(([id]) => ids.has(id))),
    tags: Object.fromEntries(Object.entries(tags).filter(([id]) => ids.has(id))),
  };
}

function apiResponse(request) {
  const url = new URL(request.url);
  const { pathname, searchParams: params } = url;
  if (pathname.endsWith("/thumbnail")) return { status: 404, body: "" };
  if (pathname === "/health") return { status: 200, body: { status: "ok" } };
  if (pathname === "/api/v1/auth/config") return { status: 200, body: { login_enabled: false } };
  if (pathname === "/api/v1/preferences") return { status: 200, body: {} };
  if (pathname === "/api/v1/settings/ai") return { status: 200, body: { no_egress: true } };
  if (pathname === "/api/v1/documents/ids") {
    const allParams = new URLSearchParams(params);
    allParams.set("limit", "10000");
    const matching = documentPage(allParams);
    return { status: 200, body: { ids: matching.items.map((doc) => doc.id), total: matching.total, truncated: false } };
  }
  if (pathname === "/api/v1/documents") {
    listRequests.push({ sort: params.get("sort"), dir: params.get("dir"), query: params.get("q") });
    return { status: 200, body: documentPage(params) };
  }
  if (pathname === "/api/v1/documents/tags:bulk" && request.method === "POST") {
    const body = JSON.parse(request.postData ?? "{}");
    bulkTagCalls.push(body);
    return { status: 200, body: { updated: body.document_ids.length } };
  }
  const detail = pathname.match(/^\/api\/v1\/documents\/([^/]+)\/detail$/);
  if (detail) {
    const doc = documents.find((item) => item.id === decodeURIComponent(detail[1]));
    return {
      status: 200,
      body: {
        document: doc,
        features: features.filter((feature) => feature.document_id === doc.id),
        categories: [],
        entities: { total: 0, by_type: [], top: [] },
        content: { length: 0, excerpt: "" },
        recent_activity: [],
        processing: {
          ...processing[doc.id],
          received_at: null,
          activated_at: null,
          ocr_confidence: null,
          language: "en",
          steps: [],
          total_duration_ms: 0,
          total_tokens: 0,
        },
        chunk_count: 3,
        extraction: { method: "text", ocr_confidence: null },
        tags: tags[doc.id],
      },
    };
  }
  if (pathname === "/api/v1/chat/threads") return { status: 200, body: [] };
  if (pathname === "/api/v1/features") return { status: 200, body: features };
  if (pathname === "/api/v1/categories") {
    const counts = new Map();
    for (const value of Object.values(stats)) counts.set(value.category, (counts.get(value.category) ?? 0) + 1);
    return { status: 200, body: [...counts].map(([name, count]) => ({ name, document_count: count })) };
  }
  if (pathname === "/api/v1/tags") return { status: 200, body: tagCatalog };
  if (pathname === "/api/v1/features/catalog" || pathname === "/api/v1/features/groups") {
    return { status: 200, body: [] };
  }
  if (pathname.endsWith("/similar") || pathname.endsWith("/notes")) return { status: 200, body: [] };
  return { status: 200, body: {} };
}

let preview;
let chrome;
let socket;
let profile;

try {
  const previewPort = await freePort();
  const debugPort = await freePort();
  profile = await mkdtemp(join(tmpdir(), "doktok-documents-layout-"));
  preview = spawn(
    process.execPath,
    [
      join(uiRoot, "node_modules", "vite", "bin", "vite.js"),
      "preview",
      "--host",
      "127.0.0.1",
      "--port",
      String(previewPort),
      "--strictPort",
    ],
    { cwd: uiRoot, stdio: "ignore" },
  );
  chrome = spawn(
    chromePath,
    [
      "--headless=new",
      "--disable-gpu",
      `--remote-debugging-port=${debugPort}`,
      "--remote-allow-origins=*",
      `--user-data-dir=${profile}`,
      "about:blank",
    ],
    { stdio: "ignore" },
  );

  await Promise.all([
    waitForHttp(`http://127.0.0.1:${previewPort}/`),
    waitForHttp(`http://127.0.0.1:${debugPort}/json/list`),
  ]);
  const targets = await fetch(`http://127.0.0.1:${debugPort}/json/list`).then((response) => response.json());
  const target = targets.find((item) => item.type === "page");
  assert(target, "Chrome page target is available");
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });

  let nextId = 0;
  const pending = new Map();
  const send = (method, params = {}) => {
    const id = ++nextId;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`CDP timeout: ${method}`));
      }, 10000);
      pending.set(id, {
        resolve: (value) => {
          clearTimeout(timer);
          resolve(value);
        },
        reject: (error) => {
          clearTimeout(timer);
          reject(error);
        },
      });
      socket.send(JSON.stringify({ id, method, params }));
    });
  };

  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.id) {
      const request = pending.get(message.id);
      if (!request) return;
      pending.delete(message.id);
      if (message.error) request.reject(new Error(JSON.stringify(message.error)));
      else request.resolve(message.result);
      return;
    }
    if (message.method === "Fetch.requestPaused") {
      const { requestId, request } = message.params;
      const pathname = new URL(request.url).pathname;
      if (pathname.startsWith("/api/") || pathname === "/health") {
        const response = apiResponse(request);
        const body = typeof response.body === "string" ? response.body : JSON.stringify(response.body);
        void send("Fetch.fulfillRequest", {
          requestId,
          responseCode: response.status,
          responseHeaders: [{ name: "Content-Type", value: "application/json" }],
          body: Buffer.from(body).toString("base64"),
        }).catch((error) => {
          const expected = String(error).includes("Invalid InterceptionId") || String(error).includes("CDP timeout: Fetch.fulfillRequest");
          if (!expected) throw error;
        });
      } else {
        void send("Fetch.continueRequest", { requestId }).catch((error) => {
          const expected = String(error).includes("Invalid InterceptionId") || String(error).includes("CDP timeout: Fetch.continueRequest");
          if (!expected) throw error;
        });
      }
    }
  });

  const evaluate = async (expression) => {
    const response = await send("Runtime.evaluate", {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (response.exceptionDetails) throw new Error(JSON.stringify(response.exceptionDetails));
    return response.result.value;
  };
  const waitFor = async (expression, description) => {
    const deadline = Date.now() + 10000;
    while (Date.now() < deadline) {
      if (await evaluate(`Boolean(${expression})`)) return;
      await delay(100);
    }
    const pageText = await evaluate("document.body.innerText.slice(-3000)");
    throw new Error(`Timed out waiting for ${description}: ${pageText}`);
  };
  const clickButton = async (label) => {
    const clicked = await evaluate(`(() => {
      const button = [...document.querySelectorAll('button')].find((item) => item.textContent === ${JSON.stringify(label)});
      if (!button) return false;
      button.click();
      return true;
    })()`);
    assert(clicked, `${label} button is available`);
  };
  const setControlValue = async (selector, value, eventType = "input") => {
    const changed = await evaluate(`(() => {
      const control = document.querySelector(${JSON.stringify(selector)});
      if (!control) return false;
      const prototype = control instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(prototype, 'value').set.call(control, ${JSON.stringify(value)});
      control.dispatchEvent(new Event(${JSON.stringify(eventType)}, { bubbles: true }));
      return true;
    })()`);
    assert(changed, `${selector} is available`);
  };
  const pressKey = async (key, code, virtualKey) => {
    const nativeVirtualKeyCode = key === "Enter" ? 36 : undefined;
    const text = key === "Enter" ? "\r" : undefined;
    await send("Input.dispatchKeyEvent", { type: "keyDown", key, code, windowsVirtualKeyCode: virtualKey, nativeVirtualKeyCode, text });
    await send("Input.dispatchKeyEvent", { type: "keyUp", key, code, windowsVirtualKeyCode: virtualKey, nativeVirtualKeyCode });
  };

  await send("Page.enable");
  await send("Fetch.enable", { patterns: [{ urlPattern: "*" }] });
  await send("Emulation.setDeviceMetricsOverride", {
    width: 390,
    height: 844,
    deviceScaleFactor: 1,
    mobile: true,
  });
  await send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 1 });
  await send("Page.navigate", { url: `http://127.0.0.1:${previewPort}/#/documents` });
  await waitFor("document.querySelector('.panel[data-mode=\"simple\"]')", "Simple Documents panel");

  const results = [];
  for (const mode of ["simple", "advanced"]) {
    if (mode === "advanced") {
      await clickButton("Advanced");
      await waitFor("document.querySelector('.panel[data-mode=\"advanced\"]')", "Advanced Documents panel");
    }
    await clickButton("List");
    await waitFor("document.querySelector('.datatable-scroll')", `${mode} List layout`);
    const listMetrics = await evaluate(`(() => {
      const main = document.querySelector('.app-main');
      const table = document.querySelector('.datatable-scroll');
      const bounds = table.getBoundingClientRect();
      return {
        innerWidth: window.innerWidth,
        documentScrollWidth: document.documentElement.scrollWidth,
        appMainClientWidth: main.clientWidth,
        appMainScrollWidth: main.scrollWidth,
        tableClientWidth: table.clientWidth,
        tableScrollWidth: table.scrollWidth,
        tableBounds: { left: bounds.left, right: bounds.right, width: bounds.width },
        tableLabel: table.getAttribute('aria-label'),
        tableTabIndex: table.getAttribute('tabindex'),
      };
    })()`);
    assert.equal(listMetrics.innerWidth, 390, `${mode}/list: viewport width`);
    assert.ok(listMetrics.documentScrollWidth <= 390, `${mode}/list: document overflow ${JSON.stringify(listMetrics)}`);
    assert.ok(listMetrics.appMainClientWidth > 0, `${mode}/list: positive app-main client width`);
    assert.ok(listMetrics.appMainScrollWidth <= listMetrics.appMainClientWidth, `${mode}/list: app-main overflow ${JSON.stringify(listMetrics)}`);
    assert.ok(listMetrics.tableClientWidth > 0 && listMetrics.tableScrollWidth > 0, `${mode}/list: positive table client widths`);
    assert.ok(listMetrics.tableBounds.width > 0 && listMetrics.tableBounds.left >= 0 && listMetrics.tableBounds.right <= 390, `${mode}/list: table bounds ${JSON.stringify(listMetrics.tableBounds)}`);
    assert.ok(listMetrics.tableScrollWidth > listMetrics.tableClientWidth, `${mode}/list: horizontal table overflow ${JSON.stringify(listMetrics)}`);
    assert.equal(listMetrics.tableLabel, "Documents table", `${mode}/list: accessible scroll-region name`);
    assert.equal(listMetrics.tableTabIndex, "0", `${mode}/list: keyboard-focusable scroll region`);
    await evaluate(`(() => {
      const table = document.querySelector('.datatable-scroll');
      table.scrollLeft = 0;
      table.focus();
    })()`);
    await pressKey("ArrowRight", "ArrowRight", 39);
    await waitFor("document.querySelector('.datatable-scroll').scrollLeft > 0", `${mode}/list: ArrowRight scrolls horizontal overflow`);
    await evaluate("document.querySelector('.datatable-scroll').scrollLeft = 0");
    results.push({ mode, view: "list", horizontalKeyboardScroll: true, ...listMetrics });

    if (mode === "advanced") {
      await evaluate(`document.querySelector('.datatable tbody input[aria-label^="Select "]').click()`);
      await waitFor("document.querySelector('[aria-label=\"Bulk actions\"]')", "Advanced mobile bulk actions");
      const bulkOverflow = await evaluate("document.documentElement.scrollWidth");
      assert.ok(bulkOverflow <= 390, `advanced/list/bulk: document overflow ${bulkOverflow}`);
      await clickButton("Clear");
    }

    await clickButton("Thumbnails");
    await waitFor("document.querySelectorAll('.docs-grid article').length === 3", `three ${mode} cards`);
    for (const [size, label] of [["s", "Small"], ["m", "Medium"], ["l", "Large"]]) {
      await evaluate(`document.querySelector('[aria-label="${label} thumbnails"]').click()`);
      await waitFor(`document.querySelector('.docs-grid')?.dataset.size === '${size}'`, `${label} cards`);
      await delay(100);
      const metrics = await evaluate(`(() => {
        const main = document.querySelector('.app-main');
        const grid = document.querySelector('.docs-grid');
        const cards = [...document.querySelectorAll('.doc-card-grid')];
        return {
          innerWidth: window.innerWidth,
          documentClientWidth: document.documentElement.clientWidth,
          documentScrollWidth: document.documentElement.scrollWidth,
          appMainClientWidth: main.clientWidth,
          appMainScrollWidth: main.scrollWidth,
          gridClientWidth: grid.clientWidth,
          gridScrollWidth: grid.scrollWidth,
          cardCount: cards.length,
          cardBounds: cards.map((card) => {
            const rect = card.getBoundingClientRect();
            return { left: rect.left, right: rect.right, width: rect.width };
          }),
          featureChips: grid.querySelectorAll('.feature-chips').length,
          fieldsControl: document.querySelector('.tile-fields-control') !== null,
          coarsePointer: matchMedia('(pointer: coarse)').matches,
          selectionAreas: [...grid.querySelectorAll('.doc-card-check')].map((area) => ({
            tag: area.tagName,
            width: area.getBoundingClientRect().width,
            height: area.getBoundingClientRect().height,
          })),
        };
      })()`);
      const context = `${mode}/${size}`;
      assert.equal(metrics.innerWidth, 390, `${context}: viewport width`);
      assert.ok(metrics.documentScrollWidth <= 390, `${context}: document overflow ${JSON.stringify(metrics)}`);
      assert.ok(metrics.appMainClientWidth > 0, `${context}: positive app-main client width`);
      assert.ok(metrics.appMainScrollWidth <= metrics.appMainClientWidth, `${context}: app-main overflow ${JSON.stringify(metrics)}`);
      assert.ok(metrics.appMainScrollWidth <= 390, `${context}: app-main exceeds 390px`);
      assert.ok(metrics.gridClientWidth > 0 && metrics.gridScrollWidth > 0, `${context}: positive grid client widths`);
      assert.ok(metrics.gridScrollWidth <= metrics.gridClientWidth, `${context}: grid overflow`);
      assert.equal(metrics.cardCount, 3, `${context}: all cards render`);
      for (const bounds of metrics.cardBounds) {
        assert.ok(bounds.width > 0 && bounds.left >= 0 && bounds.right <= 390, `${context}: card bounds ${JSON.stringify(bounds)}`);
      }
      if (mode === "simple") {
        assert.equal(metrics.featureChips, 0, `${context}: no Simple feature chips`);
        assert.equal(metrics.fieldsControl, false, `${context}: no Simple Fields control`);
        assert.equal(metrics.selectionAreas.length, 0, `${context}: no Simple selection controls`);
      } else {
        assert.ok(metrics.featureChips >= 3, `${context}: Advanced feature badges remain`);
        assert.equal(metrics.fieldsControl, true, `${context}: Advanced Fields control remains`);
        assert.equal(metrics.selectionAreas.length, 3, `${context}: Advanced selection areas remain`);
        assert.equal(metrics.coarsePointer, true, `${context}: coarse-pointer emulation`);
        for (const area of metrics.selectionAreas) {
          assert.equal(area.tag, "LABEL", `${context}: selection hit area is a label`);
          assert.ok(area.width >= 44 && area.height >= 44, `${context}: 44px selection target ${JSON.stringify(area)}`);
        }
      }
      results.push({ mode, size, ...metrics });
    }
  }

  const point = await evaluate(`(() => {
    const area = document.querySelector('.doc-card-check');
    const rect = area.getBoundingClientRect();
    return { x: rect.right - 2, y: rect.top + rect.height / 2 };
  })()`);
  await send("Input.dispatchMouseEvent", { type: "mousePressed", ...point, button: "left", clickCount: 1 });
  await send("Input.dispatchMouseEvent", { type: "mouseReleased", ...point, button: "left", clickCount: 1 });
  await waitFor("document.querySelector('.doc-card-check input')?.checked", "selection from padded label area");

  await evaluate(`(() => {
    const trigger = document.querySelector('.tile-fields-btn');
    trigger.focus();
    trigger.click();
  })()`);
  await waitFor("document.querySelector('.tile-fields-popover')", "Tile fields overlay");
  await evaluate("document.querySelector('.tile-fields-popover input').focus()");
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await waitFor("!document.querySelector('.tile-fields-popover')", "Tile fields Escape close");
  assert.equal(
    await evaluate("document.activeElement === document.querySelector('.tile-fields-btn')"),
    true,
    "Tile fields Escape restores trigger focus",
  );

  await evaluate(`(() => {
    const trigger = document.querySelector('.docs-filter-disclosure > button');
    trigger.focus();
    trigger.click();
  })()`);
  await waitFor("document.querySelector('.docs-filter-dialog[open]')", "mobile Filters dialog");
  const dialogOverflow = await evaluate("document.documentElement.scrollWidth");
  assert.ok(dialogOverflow <= 390, `mobile Filters dialog overflow ${dialogOverflow}`);
  await evaluate("document.querySelector('.docs-filter-dialog-actions button').click()");
  await waitFor("!document.querySelector('.docs-filter-dialog[open]')", "mobile Filters close");
  assert.equal(
    await evaluate("document.activeElement === document.querySelector('.docs-filter-disclosure > button')"),
    true,
    "native Filters dialog restores trigger focus",
  );

  const e2eChecks = [];
  const healthLabels = await evaluate(`[...document.querySelectorAll('.doc-card-status')].map((item) => item.textContent)`);
  for (const expected of ["Failed", "Processing", "Needs attention"]) {
    assert.ok(healthLabels.includes(expected), `thumbnail health includes ${expected}: ${healthLabels}`);
  }
  e2eChecks.push("visible thumbnail health");

  installDocuments(Array.from({ length: 60 }, (_, index) => ({
    id: `d${index}`,
    original_filename: `report-${String(index).padStart(3, "0")}.pdf`,
    title: `Report ${String(index).padStart(3, "0")}`,
    detected_mime: "application/pdf",
    status: index === 0 ? "failed" : "active",
    created_at: new Date(Date.UTC(2026, 0, index + 1)).toISOString(),
    document_date: new Date(Date.UTC(2025, 0, index + 1)).toISOString().slice(0, 10),
    summary: `Summary for report ${index}`,
    metadata: { page_count: index === 0 ? 42 : 1 },
    mockCategory: index === 0 || index % 2 === 0 ? "Receipts" : "Invoices",
  })));
  tagCatalog = [{
    id: "t1",
    tenant_id: "tenant-1",
    name: "Rome Trip",
    normalized: "rome trip",
    description: "",
    color: "teal",
    status: "active",
    merged_into: null,
    scope: "tenant",
    owner_user_id: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: null,
    document_count: 60,
  }];
  await send("Emulation.setDeviceMetricsOverride", {
    width: 1280,
    height: 900,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await send("Emulation.setTouchEmulationEnabled", { enabled: false });
  await evaluate("localStorage.clear(); location.hash = '#/documents?view=list&order=newest'");
  await send("Page.reload", { ignoreCache: true });
  await waitFor("document.querySelector('.panel[data-mode=\"simple\"] .result-count')?.textContent.includes('Showing 50 of 60 documents')", "desktop Simple 60-document list");
  e2eChecks.push("Simple desktop List with 1-page and 42-page documents");

  await setControlValue('.docs-filter-controls input[aria-label="Search documents"]', "report");
  await waitFor("document.querySelector('.result-count')?.textContent.includes('Showing 50 of 60 documents (search)')", "unified desktop search");
  await clickButton("Filters (1)");
  await waitFor("document.querySelector('.docs-filter-popover[data-open=\"true\"]')", "desktop Filters popover");
  for (const label of ["Failed", "Receipts"]) {
    const clicked = await evaluate(`(() => {
      const field = [...document.querySelectorAll('.docs-filter-popover label')].find((item) => item.textContent.trim().startsWith(${JSON.stringify(label)}));
      if (!field) return false;
      field.querySelector('input').click();
      return true;
    })()`);
    assert(clicked, `${label} desktop filter is available`);
  }
  await waitFor("document.querySelector('.result-count')?.textContent.includes('Showing 1 of 1 document (search + filtered)')", "immediate desktop multi-filter");
  assert.equal(await evaluate("document.querySelectorAll('.docs-applied-chip').length"), 3, "query/status/category chips");
  assert.ok(await evaluate("location.hash.includes('status=failed') && location.hash.includes('category=Receipts')"), "multi-filter URL");
  await clickButton("Done");
  e2eChecks.push("desktop immediate unified search and multi-filter chips");

  await setControlValue('.docs-filter-controls input[aria-label="Search documents"]', "no-such-document");
  await waitFor("document.querySelector('.docs-empty-recovery')", "zero-result recovery");
  await clickButton("Clear filters");
  await waitFor("document.querySelector('.result-count')?.textContent.includes('Showing 50 of 60 documents')", "Clear filters recovery");
  e2eChecks.push("zero results and recovery");

  const orders = {
    newest: ["acquired", "desc"],
    oldest: ["acquired", "asc"],
    "title-asc": ["title", "asc"],
    "title-desc": ["title", "desc"],
    "document-date-desc": ["created", "desc"],
    "document-date-asc": ["created", "asc"],
    "category-asc": ["category", "asc"],
    "category-desc": ["category", "desc"],
  };
  await setControlValue('select[aria-label="Sort by"]', "oldest", "change");
  await waitFor("location.hash.includes('order=oldest')", "oldest before named sort sweep");
  const initialSortDeadline = Date.now() + 10000;
  while (!listRequests.some((request) => request.sort === "acquired" && request.dir === "asc")) {
    if (Date.now() > initialSortDeadline) throw new Error("Timed out waiting for initial oldest request");
    await delay(100);
  }
  for (const [order, [sort, dir]] of Object.entries(orders)) {
    const before = listRequests.length;
    await setControlValue('select[aria-label="Sort by"]', order, "change");
    await waitFor(`location.hash.includes('order=${order}')`, `${order} shared URL`);
    await waitFor(`document.querySelector('select[aria-label="Sort by"]').value === '${order}'`, `${order} selection`);
    const deadline = Date.now() + 10000;
    while (!listRequests.slice(before).some((request) => request.sort === sort && request.dir === dir)) {
      if (Date.now() > deadline) throw new Error(`Timed out waiting for ${order} request`);
      await delay(100);
    }
  }
  e2eChecks.push("all eight named sorts and request mappings");

  await setControlValue('select[aria-label="Sort by"]', "oldest", "change");
  await waitFor("location.hash.includes('order=oldest')", "oldest before Load more");
  await clickButton("Load more");
  await waitFor("document.querySelector('.result-count')?.textContent.includes('Showing 60 of 60 documents')", "Load more second page");
  e2eChecks.push("Load more preserves the loaded window");

  await evaluate("document.querySelector('.datatable tbody .link-button').click()");
  await waitFor("document.querySelector('section[aria-label=\"Document detail\"]')", "42-page DocumentDetail");
  await waitFor(`[...document.querySelectorAll('dt')].find((item) => item.textContent === 'Pages')?.nextElementSibling.textContent === '42'`, "DocumentDetail shows 42 pages");
  await clickButton("← Back to documents");
  await waitFor("document.querySelector('.result-count')?.textContent.includes('Showing 60 of 60 documents')", "DocumentDetail return preserves both pages");
  await evaluate("document.querySelectorAll('.datatable tbody .link-button')[1].click()");
  await waitFor("document.querySelector('section[aria-label=\"Document detail\"]')", "1-page DocumentDetail");
  await waitFor(`[...document.querySelectorAll('dt')].find((item) => item.textContent === 'Pages')?.nextElementSibling.textContent === '1'`, "DocumentDetail shows 1 page");
  await clickButton("← Back to documents");
  await waitFor("document.querySelector('.result-count')?.textContent.includes('Showing 60 of 60 documents')", "small DocumentDetail return");
  e2eChecks.push("42-page and 1-page DocumentDetail return without losing loaded pages");

  await evaluate("location.hash = '#/documents?q=report&status=failed&category=Receipts&view=thumbnails&order=title-desc'");
  await waitFor("document.querySelectorAll('.docs-grid article').length === 1 && document.querySelector('select[aria-label=\"Sort by\"]').value === 'title-desc'", "shared thumbnail URL");
  await send("Page.reload", { ignoreCache: true });
  await waitFor("document.querySelectorAll('.docs-grid article').length === 1 && document.querySelector('select[aria-label=\"Sort by\"]').value === 'title-desc'", "shared thumbnail URL after reload");
  assert.equal(await evaluate("document.querySelectorAll('.docs-applied-chip').length"), 3, "shared URL restores chips");
  e2eChecks.push("shared URLs survive reload in Thumbnails");

  await evaluate("location.hash = '#/documents?view=list&order=category-desc'");
  await waitFor("document.querySelector('select[aria-label=\"Sort by\"]')?.value === 'category-desc'", "history starting order");
  await evaluate("document.querySelector('a[href=\"#/chat\"]').click()");
  await waitFor("location.hash === '#/chat'", "Chat history entry");
  await evaluate("location.hash = '#/documents?order=oldest'");
  await waitFor("document.querySelector('select[aria-label=\"Sort by\"]')?.value === 'oldest'", "oldest history entry");
  await evaluate("history.back()");
  await waitFor("location.hash === '#/chat'", "Back to Chat");
  await evaluate("history.back()");
  await waitFor("location.hash.includes('order=category-desc') && document.querySelector('select[aria-label=\"Sort by\"]')?.value === 'category-desc'", "Back to category-desc");
  await evaluate("history.forward()");
  await waitFor("location.hash === '#/chat'", "Forward to Chat");
  await evaluate("history.forward()");
  await waitFor("location.hash.includes('order=oldest') && document.querySelector('select[aria-label=\"Sort by\"]')?.value === 'oldest'", "Forward to oldest");
  e2eChecks.push("Back and Forward restore Documents ordering");

  await clickButton("Advanced");
  await waitFor("document.querySelector('.panel[data-mode=\"advanced\"]')", "desktop Advanced mode");
  await evaluate("location.hash = '#/documents?view=list&order=newest'");
  await waitFor("document.querySelector('.result-count')?.textContent.includes('Showing 50 of 60 documents')", "unfiltered Advanced List");
  const visibleColumns = await evaluate("document.querySelectorAll('.datatable thead th').length");
  await clickButton("Columns");
  await evaluate(`(() => {
    const option = [...document.querySelectorAll('.datatable-cols-menu label')].find((item) => item.textContent.trim() === 'File');
    option.querySelector('input').click();
  })()`);
  await waitFor(`document.querySelectorAll('.datatable thead th').length === ${visibleColumns + 1}`, "DataTable column visibility");
  await clickButton("Columns");
  assert.ok(await evaluate("document.querySelector('.chip-legend')?.textContent.includes('semantic index') && document.querySelector('.chip-legend')?.textContent.includes('entities/keywords')"), "full Processing explanations");
  e2eChecks.push("Advanced DataTable customization and Processing explanations");

  await evaluate("document.querySelector('.datatable thead input[aria-label=\"Select all\"]').click()");
  await clickButton("Select all 60 matching");
  await waitFor("document.querySelector('.select-all-banner')?.textContent.includes('All 60 matching are selected.')", "Advanced select-all-matching");
  await clickButton("Tags…");
  await waitFor("document.querySelector('.bulk-tag-dialog')", "bulk tag dialog");
  await evaluate("document.querySelector('.bulk-tag-list input').click()");
  await clickButton("Apply");
  const bulkDeadline = Date.now() + 10000;
  while (bulkTagCalls.length === 0) {
    if (Date.now() > bulkDeadline) throw new Error("Timed out waiting for safe bulk tag update");
    await delay(100);
  }
  assert.equal(bulkTagCalls[0].document_ids.length, 60, "bulk tags target every matching id");
  assert.deepEqual(bulkTagCalls[0].add, ["t1"], "bulk tags add only the chosen tag");
  assert.deepEqual(bulkTagCalls[0].remove, [], "bulk tags do not remove tags");
  e2eChecks.push("select-all-matching and non-destructive bulk tag update");

  await clickButton("Thumbnails");
  await waitFor("document.querySelector('.panel[data-mode=\"advanced\"] .docs-grid') && document.querySelector('.tile-fields-control')", "desktop Advanced Thumbnails");
  await clickButton("Simple");
  await waitFor("document.querySelector('.panel[data-mode=\"simple\"] .docs-grid')", "desktop Simple Thumbnails");
  assert.equal(await evaluate("document.querySelector('.tile-fields-control') !== null || document.querySelector('.docs-grid .feature-chips') !== null || document.querySelector('.doc-card-check') !== null"), false, "Simple thumbnails hide Advanced operations");
  await clickButton("List");
  await waitFor("document.querySelector('.panel[data-mode=\"simple\"] .datatable')", "desktop Simple List");
  assert.equal(await evaluate("document.querySelector('[aria-label=\"Bulk actions\"]') !== null || document.querySelector('[aria-label=\"Reprocess all documents\"]') !== null || document.querySelector('.datatable input[aria-label^=\"Select\"]') !== null"), false, "Simple List hides selection and bulk operations");
  assert.equal(bulkTagCalls.length, 1, "Simple executes no additional bulk operation");
  e2eChecks.push("Simple and Advanced in both desktop layouts without hidden Simple operations");

  await send("Emulation.setDeviceMetricsOverride", {
    width: 390,
    height: 844,
    deviceScaleFactor: 1,
    mobile: true,
  });
  await send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 1 });
  await waitFor("!document.querySelector('.docs-filter-controls > .docs-query')", "mobile filter controls after resize");
  await clickButton("Filters");
  await waitFor("document.querySelector('.docs-filter-dialog[open]')", "mobile draft Filters");
  await setControlValue('.docs-filter-dialog input[aria-label="Search documents"]', "report");
  await waitFor("[...document.querySelectorAll('.docs-filter-dialog-actions button')].some((item) => item.textContent === 'Show 60 documents')", "mobile debounced draft count");
  assert.equal(await evaluate("location.hash.includes('q=report')"), false, "mobile draft does not commit before Apply");
  await clickButton("Show 60 documents");
  await waitFor("location.hash.includes('q=report') && !document.querySelector('.docs-filter-dialog[open]')", "mobile Apply commits once");
  await waitFor("document.querySelector('[role=\"log\"][aria-live=\"polite\"]')?.textContent.includes('Showing 50 of 60 documents (search)')", "polite result announcement");
  assert.ok(await evaluate("document.documentElement.scrollWidth <= 390"), "390px mobile Apply has no document overflow");
  e2eChecks.push("mobile draft/Apply, polite results, and 390px overflow check");

  await send("Emulation.setDeviceMetricsOverride", {
    width: 1280,
    height: 900,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await send("Emulation.setTouchEmulationEnabled", { enabled: false });
  await waitFor("document.querySelector('.docs-filter-controls > .docs-query')", "desktop filters after resize");
  await evaluate("document.querySelector('.docs-filter-controls > .docs-query input').focus()");
  await pressKey("Tab", "Tab", 9);
  assert.equal(await evaluate("document.activeElement === document.querySelector('.docs-filter-disclosure > button')"), true, "Tab reaches Filters");
  await pressKey("Enter", "Enter", 13);
  await waitFor("document.querySelector('.docs-filter-popover[data-open=\"true\"]')", "keyboard Enter opens Filters");
  await pressKey("Escape", "Escape", 27);
  await waitFor("!document.querySelector('.docs-filter-popover:popover-open')", "keyboard Escape closes Filters");
  assert.equal(await evaluate("document.activeElement === document.querySelector('.docs-filter-disclosure > button')"), true, "native popover returns keyboard focus");
  e2eChecks.push("keyboard-only desktop popover and native focus return");

  console.log(JSON.stringify({ viewport: 390, checks: results, paddedLabelSelection: true, overlayFocus: true, e2eChecks }, null, 2));
  console.log("Documents layout and real-App checks passed.");
} finally {
  if (socket) socket.close();
  await Promise.all([chrome, preview].map(async (child) => {
    if (!child || child.exitCode !== null || child.signalCode !== null) return;
    const exited = once(child, "exit");
    child.kill("SIGTERM");
    await exited;
  }));
  if (profile) await rm(profile, { recursive: true, force: true });
}
