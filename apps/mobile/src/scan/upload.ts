import * as FileSystem from "expo-file-system/legacy";

import { BACKEND_URL } from "../config";
import { jpegDimensions, jpgToPdf, type PdfPage } from "./pdf";

// Scan-set assembly + upload (#774): page JPEGs -> one multi-page PDF -> multipart upload to the
// SAME ingestion endpoint the web dropzone uses (POST /api/v1/ingestion/upload, field "files").
export interface UploadOutcome {
  accepted: string[];
  rejected: string[];
}

function base64ToBytes(b64: string): Uint8Array {
  const bin = globalThis.atob ? globalThis.atob(b64) : "";
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

/** Build the PDF for the scanned pages (file:// paths) and return its cache path. */
export async function buildScanPdf(pagePaths: string[]): Promise<string> {
  const pages: PdfPage[] = [];
  for (const path of pagePaths) {
    const b64 = await FileSystem.readAsStringAsync(path, {
      encoding: FileSystem.EncodingType.Base64,
    });
    const bytes = base64ToBytes(b64);
    const { width, height } = jpegDimensions(bytes);
    pages.push({ bytes, width, height });
  }
  const pdf = jpgToPdf(pages);
  const target = `${FileSystem.cacheDirectory}scan-${Date.now()}.pdf`;
  const b64Pdf = bytesToBase64(pdf);
  await FileSystem.writeAsStringAsync(target, b64Pdf, {
    encoding: FileSystem.EncodingType.Base64,
  });
  return target;
}

function bytesToBase64(bytes: Uint8Array): string {
  let bin = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    bin += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return globalThis.btoa ? globalThis.btoa(bin) : bin;
}

/** Upload the assembled PDF as one document; progress 0..1 via the callback. */
export async function uploadScanPdf(
  pdfPath: string,
  token: string,
  onProgress?: (fraction: number) => void,
): Promise<UploadOutcome> {
  const task = FileSystem.createUploadTask(
    `${BACKEND_URL}/api/v1/ingestion/upload`,
    pdfPath,
    {
      httpMethod: "POST",
      uploadType: FileSystem.FileSystemUploadType.MULTIPART,
      fieldName: "files",
      mimeType: "application/pdf",
      headers: { Authorization: `Bearer ${token}` },
    },
    (progress) => {
      if (onProgress && progress.totalBytesExpectedToSend > 0) {
        onProgress(progress.totalBytesSent / progress.totalBytesExpectedToSend);
      }
    },
  );
  const res = await task.uploadAsync();
  if (!res || res.status !== 200) {
    throw new Error(`upload failed (HTTP ${res?.status ?? "?"})`);
  }
  return JSON.parse(res.body) as UploadOutcome;
}
