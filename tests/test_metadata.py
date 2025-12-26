"""Metadata checker unit tests (v2 field-weighted EXIF scoring rules)."""

from __future__ import annotations

from PIL import Image

from detector.metadata_checker import EnhancedMetadataChecker, _safe_display_value


class FakeExifCarrier:
    """Small helper object that mimics Pillow's EXIF hook for controlled testing."""

    def __init__(self, exif_payload):
        self._exif_payload = exif_payload

    def _getexif(self):  # noqa: SLF001 - intentional: matches Pillow's private EXIF method
        return self._exif_payload


def test_marks_no_exif_as_high_risk_for_png_inputs() -> None:
    """Images without EXIF should return score=75 (treated as suspicious) in v2."""

    invoice_png = Image.new("RGB", (32, 32), color=(255, 255, 255))
    metadata_checker = EnhancedMetadataChecker()
    result = metadata_checker.analyze("invoice.png", invoice_image=invoice_png)

    assert result["score"] == 75.0
    assert result["verdict"] == "HIGH RISK"
    assert "exif" in " ".join(result["all_flags"]).lower()


def test_flags_photoshop_and_enforces_minimum_score() -> None:
    """Editing software in EXIF should create a CRITICAL flag and enforce a minimum score."""

    # Standard EXIF tag IDs: Software=305, DateTime=306, DateTimeOriginal=36867, Make=271, Model=272
    exif_payload = {
        305: "Adobe Photoshop 24.0",
        306: "2024:01:02 10:00:00",
        36867: "2024:01:01 10:00:00",
    }
    metadata_checker = EnhancedMetadataChecker()
    result = metadata_checker.analyze("invoice.jpg", invoice_image=FakeExifCarrier(exif_payload))  # type: ignore[arg-type]

    assert float(result["score"]) >= 75.0
    joined_flags = " ".join(result.get("all_flags", [])).lower()
    assert "critical" in joined_flags
    assert "photoshop" in joined_flags
    assert "field_breakdown" in result


def test_truncates_metadata_values_for_safe_ui_display() -> None:
    """Long EXIF strings should be truncated for safe UI display."""

    long_value = "x" * 250
    truncated = _safe_display_value(long_value, max_chars=60)
    assert len(truncated) <= 60


