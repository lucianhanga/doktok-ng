"""Device-aware OCR engine recommendation (M17 #...).

Pure logic: given a hardware snapshot, suggest the OCR engine + parallelism that fit best. The
backend collects the snapshot (CPU/cores/RAM/GPU) and calls this; the UI shows it as a hint.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OcrRecommendation:
    engine: str  # paddleocr | rapidocr | glm-ocr
    concurrency: int
    reason: str


def recommend_ocr(
    *, cpu_vendor: str, logical_cores: int, total_ram_gb: float, has_gpu: bool
) -> OcrRecommendation:
    """Recommend an OCR engine + concurrency for the detected host.

    Parallelism is bounded by both cores (OCR is ~1 core/process) and RAM (~1.5 GB/process, leaving
    room for Postgres/embeddings/OS), capped at 6 - a sane ceiling for a single box.
    """
    cores = max(1, logical_cores)
    by_cores = max(1, cores - 1)  # leave a core for the OS / other services
    by_ram = max(1, int(total_ram_gb // 3))  # ~1.5 GB/process + headroom for the rest of the stack
    concurrency = max(1, min(by_cores, by_ram, 6))

    if has_gpu:
        return OcrRecommendation(
            "rapidocr",
            min(cores, 8),
            "A GPU was detected - run OCR on the GPU (RapidOCR GPU provider) for top throughput.",
        )
    if "intel" in cpu_vendor.lower():
        return OcrRecommendation(
            "rapidocr",
            concurrency,
            f"Intel CPU with {cores} cores - RapidOCR with the OpenVINO backend is the fastest "
            f"same-quality option here; run {concurrency} in parallel.",
        )
    return OcrRecommendation(
        "rapidocr",
        concurrency,
        f"CPU-only host with {cores} cores - RapidOCR (ONNX) is lighter and faster than Paddle at "
        f"the same quality; run {concurrency} in parallel.",
    )
