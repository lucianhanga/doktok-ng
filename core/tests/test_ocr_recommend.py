"""Device-aware OCR recommendation logic (M17 #375)."""

from __future__ import annotations

from doktok_core.settings.ocr_recommend import recommend_ocr


def test_intel_cpu_recommends_rapidocr_openvino() -> None:
    rec = recommend_ocr(cpu_vendor="GenuineIntel", logical_cores=4, total_ram_gb=8.0, has_gpu=False)
    assert rec.engine == "rapidocr"
    assert rec.concurrency == 2  # min(cores-1=3, ram//3=2, cap 6)
    assert "openvino" in rec.reason.lower()


def test_amd_cpu_recommends_rapidocr_onnx_more_parallel() -> None:
    rec = recommend_ocr(
        cpu_vendor="AuthenticAMD", logical_cores=16, total_ram_gb=32.0, has_gpu=False
    )
    assert rec.engine == "rapidocr"
    assert rec.concurrency == 6  # capped at 6
    assert "openvino" not in rec.reason.lower()


def test_gpu_recommends_gpu_ocr() -> None:
    rec = recommend_ocr(cpu_vendor="GenuineIntel", logical_cores=8, total_ram_gb=32.0, has_gpu=True)
    assert rec.engine == "rapidocr"
    assert "gpu" in rec.reason.lower()


def test_tiny_box_falls_back_to_single_process() -> None:
    rec = recommend_ocr(cpu_vendor="", logical_cores=2, total_ram_gb=4.0, has_gpu=False)
    assert rec.concurrency == 1
