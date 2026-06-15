import { useState } from "react";

import { documentPageImageUrl, type LayoutPage } from "./api";

/**
 * Renders a document page image with its OCR line boxes drawn on top. Boxes are positioned as a
 * proportion of the page's pixel size, so they line up at any display size. A toggle shows/hides
 * them (default on) and a pager moves between pages.
 */
export function OcrBoxOverlay({ docId, pages }: { docId: string; pages: LayoutPage[] }) {
  const [idx, setIdx] = useState(0);
  const [showBoxes, setShowBoxes] = useState(true);
  const page = pages[Math.min(idx, pages.length - 1)];
  const sized = page.width_px > 0 && page.height_px > 0;

  return (
    <div className="ocr-overlay">
      <div className="ocr-overlay-toolbar">
        <label className="ocr-overlay-toggle">
          <input
            type="checkbox"
            checked={showBoxes}
            onChange={(e) => setShowBoxes(e.target.checked)}
          />
          <span>Show text regions ({page.lines.length})</span>
        </label>
        {pages.length > 1 && (
          <span className="ocr-overlay-pager">
            <button type="button" onClick={() => setIdx((i) => Math.max(0, i - 1))} disabled={idx === 0}>
              ‹
            </button>
            <span className="muted">
              Page {page.page_number} / {pages.length}
            </span>
            <button
              type="button"
              onClick={() => setIdx((i) => Math.min(pages.length - 1, i + 1))}
              disabled={idx >= pages.length - 1}
            >
              ›
            </button>
          </span>
        )}
      </div>
      <div className="ocr-overlay-canvas">
        <img src={documentPageImageUrl(docId, page.page_number)} alt={`Page ${page.page_number}`} />
        {showBoxes && sized && (
          <div className="ocr-overlay-boxes" aria-hidden="true">
            {page.lines.map((ln, i) => (
              <div
                key={i}
                className="ocr-box"
                title={ln.text}
                style={{
                  left: `${(ln.x0 / page.width_px) * 100}%`,
                  top: `${(ln.y0 / page.height_px) * 100}%`,
                  width: `${((ln.x1 - ln.x0) / page.width_px) * 100}%`,
                  height: `${((ln.y1 - ln.y0) / page.height_px) * 100}%`,
                }}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
