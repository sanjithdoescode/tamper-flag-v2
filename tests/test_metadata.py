"""Metadata checker unit tests (EXIF scoring rules)."""

from __future__ import annotations

from PIL import Image

from detector.metadata_checker import InvoiceMetadataInspector, _truncate_display_value


class FakeExifCarrier:
    """Small helper object that mimics Pillow's EXIF hook for controlled testing."""

    def __init__(self, exif_payload):
        self._exif_payload = exif_payload

    def _getexif(self):  # noqa: SLF001 - intentional: matches Pillow's private EXIF method
        return self._exif_payload


def test_marks_no_exif_as_suspicious() -> None:
    """Images without EXIF should return score=50 per spec."""

    invoice_png = Image.new("RGB", (32, 32), color=(255, 255, 255))
    metadata_inspector = InvoiceMetadataInspector()
    result = metadata_inspector.inspect_invoice_image(invoice_png)

    assert result["score"] == 50.0
    assert "no exif" in result["verdict"].lower()


def test_flags_photoshop_datetime_mismatch_and_missing_fields() -> None:
    """Software markers + timestamp mismatch + missing fields should accumulate the right score."""

    # Standard EXIF tag IDs: Software=305, DateTime=306, DateTimeOriginal=36867, Make=271, Model=272
    exif_payload = {
        305: "Adobe Photoshop 24.0",
        306: "2024:01:02 10:00:00",
        36867: "2024:01:01 10:00:00",
    }
    metadata_inspector = InvoiceMetadataInspector()
    result = metadata_inspector.inspect_invoice_image(FakeExifCarrier(exif_payload))  # type: ignore[arg-type]

    assert result["score"] == 65.0
    joined_flags = " ".join(result["flags"]).lower()
    assert "edited with" in joined_flags
    assert "datetime differs" in joined_flags
    assert "missing critical exif fields" in joined_flags


def test_truncates_metadata_values_to_100_chars() -> None:
    """Long EXIF strings should be truncated for safe UI display."""

    long_value = "x" * 250
    truncated = _truncate_display_value(long_value, max_chars=100)
    assert len(truncated) <= 100


