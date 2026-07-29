// Minimal JPEG->PDF writer (#774): embeds each JPEG as one page (DCTDecode passthrough - no
// recompression, no native deps). A scanned page image becomes a same-sized PDF page.
export interface PdfPage {
  bytes: Uint8Array;
  width: number;
  height: number;
}

// Parse a JPEG's SOF marker for dimensions (we never trust the file extension).
export function jpegDimensions(bytes: Uint8Array): { width: number; height: number } {
  let i = 2; // skip FFD8
  while (i + 8 < bytes.length) {
    if (bytes[i] !== 0xff) {
      i += 1;
      continue;
    }
    const marker = bytes[i + 1];
    const len = (bytes[i + 2] << 8) | bytes[i + 3];
    // SOF0..SOF15 except DHT(C4), JPG(C8), DAC(CC)
    if (marker >= 0xc0 && marker <= 0xcf && marker !== 0xc4 && marker !== 0xc8 && marker !== 0xcc) {
      return { height: (bytes[i + 5] << 8) | bytes[i + 6], width: (bytes[i + 7] << 8) | bytes[i + 8] };
    }
    i += 2 + len;
  }
  throw new Error("not a JPEG (no SOF marker)");
}

class PdfWriter {
  private parts: Uint8Array[] = [];
  private len = 0;
  push(data: Uint8Array | string) {
    const bytes = typeof data === "string" ? new TextEncoder().encode(data) : data;
    this.parts.push(bytes);
    this.len += bytes.length;
  }
  offset(): number {
    return this.len;
  }
  build(): Uint8Array {
    const out = new Uint8Array(this.len);
    let at = 0;
    for (const p of this.parts) {
      out.set(p, at);
      at += p.length;
    }
    return out;
  }
}

export function jpgToPdf(pages: PdfPage[]): Uint8Array {
  if (pages.length === 0) throw new Error("no pages");
  const w = new PdfWriter();
  const offsets: number[] = [];
  const obj = (n: number, body: () => void) => {
    offsets[n] = w.offset();
    w.push(`${n} 0 obj\n`);
    body();
    w.push("\nendobj\n");
  };

  w.push("%PDF-1.4\n");
  // object layout: 1=catalog, 2=pages, then per page i: page obj, image obj, content obj
  const pageObj = (i: number) => 3 + i * 3;
  const kids = pages.map((_, i) => `${pageObj(i)} 0 R`).join(" ");
  obj(1, () => w.push(`<< /Type /Catalog /Pages 2 0 R >>`));
  obj(2, () => w.push(`<< /Type /Pages /Kids [${kids}] /Count ${pages.length} >>`));

  pages.forEach((p, i) => {
    const n = pageObj(i);
    obj(n, () =>
      w.push(
        `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${p.width} ${p.height}] ` +
          `/Resources << /XObject << /Im0 ${n + 1} 0 R >> /ProcSet [/PDF /ImageC] >> ` +
          `/Contents ${n + 2} 0 R >>`,
      ),
    );
    obj(n + 1, () => {
      w.push(
        `<< /Type /XObject /Subtype /Image /Width ${p.width} /Height ${p.height} ` +
          `/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${p.bytes.length} >>\nstream\n`,
      );
      w.push(p.bytes);
      w.push("\nendstream");
    });
    const content = `q\n${p.width} 0 0 ${p.height} 0 0 cm\n/Im0 Do\nQ\n`;
    obj(n + 2, () => w.push(`<< /Length ${content.length} >>\nstream\n${content}endstream`));
  });

  const xrefAt = w.offset();
  const total = pageObj(pages.length - 1) + 2; // last object number
  let xref = `xref\n0 ${total + 1}\n0000000000 65535 f \n`;
  for (let n = 1; n <= total; n++) {
    xref += `${String(offsets[n] ?? 0).padStart(10, "0")} 00000 n \n`;
  }
  w.push(xref);
  w.push(`trailer\n<< /Size ${total + 1} /Root 1 0 R >>\nstartxref\n${xrefAt}\n%%EOF\n`);
  return w.build();
}
