"""Weighted fraud scoring for invoice tampering detection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pdf2image import convert_from_path
from PIL import Image, UnidentifiedImageError

from .ela_detector import InvoiceElaAnalyzer
from .metadata_checker import InvoiceMetadataInspector
from .ocr_validator import InvoiceOcrMathValidator


def _final_verdict_from_score(final_score: float) -> str:
    """Translate a 0–100 fraud score into the required final verdict label."""

    if final_score >= 65:
        return "HIGH RISK - Likely Tampered"
    if final_score >= 40:
        return "MEDIUM RISK - Requires Review"
    return "LOW RISK - Appears Authentic"


def _shrink_image_to_max_width(invoice_image: Image.Image, *, max_width_px: int) -> Image.Image:
    """Downscale an image to max_width_px while keeping aspect ratio."""

    if max_width_px <= 0:
        return invoice_image

    width_px, height_px = invoice_image.size
    if width_px <= max_width_px:
        return invoice_image

    shrink_ratio = max_width_px / float(width_px)
    resized_height_px = max(1, int(height_px * shrink_ratio))
    return invoice_image.resize((int(max_width_px), int(resized_height_px)), Image.Resampling.LANCZOS)


def _score_value(result_payload: dict[str, Any], *, fallback: float) -> float:
    """Best-effort extraction of a numeric score from a detector payload."""

    try:
        return float(result_payload.get("score", fallback))
    except (TypeError, ValueError):
        return float(fallback)


class InvoiceFraudScorer:
    """Orchestrates ELA, metadata, and OCR checks into a final fraud score."""

    def __init__(
        self,
        *,
        results_directory: str,
        public_results_prefix: str | None = "/static/results",
        max_image_width_px: int = 2000,
        pdf_dpi: int = 200,
    ) -> None:
        """Create a scorer with initialized detectors."""

        self.ela_analyzer = InvoiceElaAnalyzer()
        self.metadata_inspector = InvoiceMetadataInspector()
        self.ocr_validator = InvoiceOcrMathValidator()

        self.results_directory = str(results_directory)
        self.public_results_prefix = public_results_prefix
        self.max_image_width_px = int(max_image_width_px)
        self.pdf_dpi = int(pdf_dpi)

    def analyze_invoice_file(self, invoice_file_path: str) -> dict[str, Any]:
        """Analyze an invoice file (JPG/PNG/PDF) and return a nested result dict."""

        invoice_path = Path(invoice_file_path)
        extension = invoice_path.suffix.lower()

        if extension == ".pdf":
            invoice_image = self._render_pdf_first_page(invoice_path)
            return self.analyze_invoice_image(invoice_image, is_pdf=True)

        image_load = self._load_image_with_metadata(invoice_path)
        return self.analyze_invoice_image(
            image_load["invoice_image"],
            metadata_override=image_load["metadata_result"],
        )

    def analyze_invoice_image(
        self,
        invoice_image: Image.Image,
        *,
        is_pdf: bool = False,
        metadata_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Analyze an already-loaded invoice image (used for PDF first-page rendering)."""

        analysis_image = _shrink_image_to_max_width(invoice_image, max_width_px=self.max_image_width_px)

        ela_result = self._run_ela_check(analysis_image)
        metadata_result = (
            metadata_override if metadata_override is not None else self._run_metadata_check(invoice_image, is_pdf=is_pdf)
        )
        ocr_result = self._run_ocr_check(analysis_image)

        return self._assemble_fraud_report(ela_result, metadata_result, ocr_result)

    def _assemble_fraud_report(
        self,
        ela_result: dict[str, Any],
        metadata_result: dict[str, Any],
        ocr_result: dict[str, Any],
    ) -> dict[str, Any]:
        """Combine detector scores using the required weights and thresholds."""

        final_score = (
            _score_value(ela_result, fallback=50.0) * 0.4
            + _score_value(metadata_result, fallback=50.0) * 0.3
            + _score_value(ocr_result, fallback=40.0) * 0.3
        )

        return {
            "final_score": float(round(final_score, 2)),
            "verdict": _final_verdict_from_score(final_score),
            "ela": ela_result,
            "metadata": metadata_result,
            "ocr": ocr_result,
        }

    def _render_pdf_first_page(self, invoice_path: Path) -> Image.Image:
        """Convert a PDF's first page into a Pillow RGB image."""

        try:
            rendered_pages = convert_from_path(str(invoice_path), dpi=self.pdf_dpi, first_page=1, last_page=1)
            if not rendered_pages:
                raise ValueError("PDF conversion returned no pages.")
            return rendered_pages[0].convert("RGB")
        except Exception as pdf_error:
            raise ValueError(f"Failed to convert PDF to image: {pdf_error}") from pdf_error

    def _load_image_with_metadata(self, invoice_path: Path) -> dict[str, Any]:
        """Load an image file and capture metadata before the file handle is released."""

        try:
            with Image.open(str(invoice_path)) as opened_image:
                metadata_result = self._run_metadata_check(opened_image, is_pdf=False)
                return {"invoice_image": opened_image.copy(), "metadata_result": metadata_result}
        except (OSError, UnidentifiedImageError) as image_error:
            raise ValueError(f"Failed to read invoice image: {image_error}") from image_error

    def _run_ela_check(self, invoice_image: Image.Image) -> dict[str, Any]:
        """Run ELA even if upstream steps fail (returns structured error on exception)."""

        try:
            return self.ela_analyzer.analyze_invoice_image(
                invoice_image,
                self.results_directory,
                jpeg_quality=90,
                public_results_prefix=self.public_results_prefix,
            )
        except Exception as ela_error:  # noqa: BLE001 - isolate ELA failures from other checks
            return {
                "score": 50.0,
                "verdict": "INCONCLUSIVE - ELA failed",
                "visualization_path": None,
                "metrics": {},
                "error": str(ela_error),
            }

    def _run_metadata_check(self, invoice_image: Image.Image, *, is_pdf: bool) -> dict[str, Any]:
        """Inspect EXIF metadata for images; PDFs have no EXIF so return a fixed result."""

        if is_pdf:
            return {
                "score": 50.0,
                "verdict": "SUSPICIOUS - No EXIF metadata found",
                "flags": ["PDF input has no EXIF metadata; metadata checks are limited."],
                "metadata": {},
                "error": None,
            }

        try:
            return self.metadata_inspector.inspect_invoice_image(invoice_image)
        except Exception as metadata_error:  # noqa: BLE001 - keep pipeline running
            return {
                "score": 50.0,
                "verdict": "INCONCLUSIVE - Metadata inspection failed",
                "flags": ["Could not extract EXIF metadata."],
                "metadata": {},
                "error": str(metadata_error),
            }

    def _run_ocr_check(self, invoice_image: Image.Image) -> dict[str, Any]:
        """Run OCR and math checks with a tolerant total validation."""

        try:
            return self.ocr_validator.validate_invoice_image(invoice_image, tolerance_ratio=0.15)
        except Exception as ocr_error:  # noqa: BLE001 - keep pipeline running
            return {
                "score": 40.0,
                "verdict": "INCONCLUSIVE - OCR failed",
                "flags": ["OCR validation failed unexpectedly."],
                "extracted_text": "",
                "amounts": [],
                "error": str(ocr_error),
            }

