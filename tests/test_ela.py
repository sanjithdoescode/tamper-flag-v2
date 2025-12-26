"""ELA unit tests covering common edge cases."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from detector.ela_detector import InvoiceElaAnalyzer


def test_scores_identical_recompression_as_suspicious_missing_compression_artifacts(tmp_path: Path) -> None:
    """If recompression produces no differences, ELA should return score=50 as specified."""

    invoice_image = Image.new("RGB", (64, 64), color=(10, 10, 10))
    ela_analyzer = InvoiceElaAnalyzer()

    ela_analyzer._recompress_and_reload_as_rgb = lambda original_rgb, jpeg_quality: original_rgb  # type: ignore[attr-defined]  # noqa: E501

    result = ela_analyzer.analyze_invoice_image(
        invoice_image,
        str(tmp_path),
        jpeg_quality=90,
        public_results_prefix=None,
    )

    assert result["score"] == 50.0
    assert "compression artifacts" in result["verdict"].lower()
    assert result["visualization_path"] is not None
    assert Path(result["visualization_path"]).exists()


def test_returns_inconclusive_on_unreadable_image_path(tmp_path: Path) -> None:
    """Unreadable input paths should return a structured, non-crashing error payload."""

    ela_analyzer = InvoiceElaAnalyzer()
    result = ela_analyzer.analyze_image_path(
        str(tmp_path / "missing.jpg"),
        str(tmp_path),
        public_results_prefix=None,
    )

    assert result["score"] == 50.0
    assert result["visualization_path"] is None
    assert result["error"]


