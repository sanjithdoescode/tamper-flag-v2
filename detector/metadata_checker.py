"""EXIF metadata inspection for invoice image tampering risk signals."""

from __future__ import annotations

from typing import Any

from PIL import ExifTags, Image, UnidentifiedImageError


def _truncate_display_value(raw_value: Any, *, max_chars: int = 100) -> str:
    """Convert a metadata value to a display string and truncate it safely."""

    try:
        if isinstance(raw_value, bytes):
            display_value = raw_value.decode("utf-8", errors="replace")
        else:
            display_value = str(raw_value)
    except Exception:
        display_value = repr(raw_value)

    display_value = display_value.replace("\n", " ").strip()
    if len(display_value) <= max_chars:
        return display_value
    return f"{display_value[:max_chars - 1]}…"


def _risk_verdict_from_score(score: float) -> str:
    """Turn a metadata score into a short risk verdict."""

    if score >= 65:
        return "HIGH METADATA RISK"
    if score >= 40:
        return "MEDIUM METADATA RISK"
    return "LOW METADATA RISK"


def _extract_exif_metadata(invoice_image: Image.Image) -> dict[str, str]:
    """Extract EXIF tags as a friendly string-keyed dict."""

    exif_raw: dict[int, Any] | None
    try:
        exif_raw = invoice_image._getexif()  # noqa: SLF001 - Pillow EXIF API is private
    except Exception:
        exif_raw = None

    if not exif_raw:
        return {}

    exif_metadata: dict[str, str] = {}
    for tag_id, tag_value in exif_raw.items():
        tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
        exif_metadata[tag_name] = _truncate_display_value(tag_value, max_chars=100)

    return exif_metadata


def _detect_editing_software(exif_metadata: dict[str, str]) -> str | None:
    """Detect common editing software markers from EXIF tags."""

    software_hint = exif_metadata.get("Software") or exif_metadata.get("ProcessingSoftware") or ""
    searchable = software_hint.lower()

    editing_markers = ["photoshop", "gimp", "paint.net", "paint shop", "adobe"]
    if any(marker in searchable for marker in editing_markers) and software_hint:
        return software_hint

    return None


class InvoiceMetadataInspector:
    """Analyze EXIF metadata for risk indicators that often follow editing."""

    def inspect_image_path(self, image_path: str) -> dict[str, Any]:
        """Inspect EXIF from an image file path."""

        try:
            with Image.open(image_path) as opened_image:
                return self.inspect_invoice_image(opened_image)
        except (OSError, UnidentifiedImageError) as open_error:
            return {
                "score": 50.0,
                "verdict": "INCONCLUSIVE - Metadata read failed",
                "flags": ["Could not read image metadata."],
                "metadata": {},
                "error": str(open_error),
            }

    def inspect_invoice_image(self, invoice_image: Image.Image) -> dict[str, Any]:
        """Inspect an already-loaded image for EXIF risk signals."""

        exif_metadata = _extract_exif_metadata(invoice_image)
        if not exif_metadata:
            return {
                "score": 50.0,
                "verdict": "SUSPICIOUS - No EXIF metadata found",
                "flags": ["No EXIF metadata present (often stripped by editors or screenshots)."],
                "metadata": {},
                "error": None,
            }

        flags: list[str] = []
        score = 0.0

        editing_software = _detect_editing_software(exif_metadata)
        if editing_software:
            score += 30.0
            flags.append(f"Edited with: {editing_software}")

        date_time = exif_metadata.get("DateTime")
        date_time_original = exif_metadata.get("DateTimeOriginal")
        if date_time and date_time_original and date_time != date_time_original:
            score += 20.0
            flags.append("DateTime differs from DateTimeOriginal (possible re-save or edit).")

        missing_fields = [field for field in ("Make", "Model", "DateTime") if not exif_metadata.get(field)]
        if missing_fields:
            score += 15.0
            flags.append(f"Missing critical EXIF fields: {', '.join(missing_fields)}")

        return {
            "score": float(min(100.0, score)),
            "verdict": _risk_verdict_from_score(score),
            "flags": flags,
            "metadata": exif_metadata,
            "error": None,
        }

