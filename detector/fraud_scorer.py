"""Invoice fraud scoring (v2.0): metadata-first + LLM validation via n8n."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from pdf2image import convert_from_path
from PIL import Image, UnidentifiedImageError

from .llm_validator import LLMValidator
from .metadata_checker import EnhancedMetadataChecker
from .ocr_extractor import OCRExtractor


def _final_verdict_from_score(final_score: float) -> str:
    """Translate a 0–100 risk score into the required verdict label."""

    if final_score >= 70:
        return "HIGH RISK - Likely Tampered"
    if final_score >= 45:
        return "MEDIUM RISK - Requires Review"
    return "LOW RISK - Appears Authentic"


def _shrink_to_max_width(invoice_image: Image.Image, *, max_width_px: int) -> Image.Image:
    """Downscale large images to keep OCR latency predictable."""

    if max_width_px <= 0:
        return invoice_image

    width_px, height_px = invoice_image.size
    if width_px <= max_width_px:
        return invoice_image

    shrink_ratio = max_width_px / float(width_px)
    resized_height_px = max(1, int(height_px * shrink_ratio))
    return invoice_image.resize((int(max_width_px), int(resized_height_px)), Image.Resampling.LANCZOS)


class FraudScorer:
    """
    Complete fraud pipeline for invoice tampering detection.

    v2 weights:
    - metadata_risk: 60%
    - llm_risk_from_coherence: 40%  (computed as 100 - sense_rating)
    """

    def __init__(
        self,
        n8n_webhook_url: str = "http://localhost:5678/webhook/validate-ocr",
        *,
        pdf_dpi: int = 200,
        max_image_width_px: int = 2000,
    ) -> None:
        self.metadata_checker = EnhancedMetadataChecker()
        self.ocr_extractor = OCRExtractor()
        self.llm_validator = LLMValidator(n8n_webhook_url)

        self.weights = {"metadata": 0.60, "llm": 0.40}
        self.pdf_dpi = int(pdf_dpi)
        self.max_image_width_px = int(max_image_width_px)

    def analyze_invoice(self, image_path: str) -> dict[str, Any]:
        """Analyze an invoice file path (JPG/PNG/PDF)."""

        started_at = time.time()
        invoice_path = Path(image_path)

        try:
            if invoice_path.suffix.lower() == ".pdf":
                analysis = self._analyze_pdf(invoice_path)
            else:
                analysis = self._analyze_single_image(invoice_path)
        except Exception as analysis_error:  # noqa: BLE001 - API boundary
            elapsed_ms = int((time.time() - started_at) * 1000)
            return {
                "final_score": 50.0,
                "verdict": "INCONCLUSIVE - Analysis failed",
                "confidence": "Low (processing error)",
                "metadata": {"score": 50.0, "error": str(analysis_error)},
                "llm_validation": {"sense_rating": 50.0, "error": str(analysis_error)},
                "ocr_text_preview": "",
                "score_breakdown": {"metadata_contribution": 30.0, "llm_contribution": 20.0, "weights": self.weights},
                "processing_time_ms": elapsed_ms,
                "error": str(analysis_error),
            }

        analysis["processing_time_ms"] = int((time.time() - started_at) * 1000)
        return analysis

    def _analyze_single_image(self, invoice_path: Path) -> dict[str, Any]:
        """Run the v2 pipeline against a single image file."""

        try:
            with Image.open(str(invoice_path)) as opened_image:
                invoice_image = opened_image.convert("RGB")

            metadata_result = self.metadata_checker.analyze(str(invoice_path), invoice_image=invoice_image)
            ocr_image = _shrink_to_max_width(invoice_image, max_width_px=self.max_image_width_px)
            ocr_result = self.ocr_extractor.extract_text(invoice_image=ocr_image)
        except (OSError, UnidentifiedImageError) as open_error:
            raise ValueError(f"Failed to read invoice image: {open_error}") from open_error

        llm_validation = self.llm_validator.validate_text(ocr_result.get("raw_text", ""))
        return self._assemble_report(
            metadata_result=metadata_result,
            llm_validation=llm_validation,
            ocr_text=ocr_result.get("raw_text", ""),
        )

    def _analyze_pdf(self, invoice_path: Path) -> dict[str, Any]:
        """Render all PDF pages, score each, and return the worst-case (max risk) page."""

        try:
            rendered_pages = convert_from_path(str(invoice_path), dpi=self.pdf_dpi)
        except Exception as pdf_error:
            raise ValueError(f"PDF conversion failed: {pdf_error}") from pdf_error

        if not rendered_pages:
            raise ValueError("PDF conversion returned no pages.")

        page_summaries: list[dict[str, Any]] = []
        worst_page_report: dict[str, Any] | None = None
        worst_page_score: float = -1.0

        for page_index, page_image in enumerate(rendered_pages, start=1):
            rgb_page = page_image.convert("RGB")
            metadata_result = self.metadata_checker.analyze(str(invoice_path), invoice_image=rgb_page)
            ocr_image = _shrink_to_max_width(rgb_page, max_width_px=self.max_image_width_px)
            ocr_result = self.ocr_extractor.extract_text(invoice_image=ocr_image)
            llm_validation = self.llm_validator.validate_text(ocr_result.get("raw_text", ""))

            page_report = self._assemble_report(
                metadata_result=metadata_result,
                llm_validation=llm_validation,
                ocr_text=ocr_result.get("raw_text", ""),
            )

            page_report["pdf_page_number"] = page_index
            page_report["pdf_page_count"] = len(rendered_pages)

            page_summaries.append(
                {
                    "page_number": page_index,
                    "final_score": page_report.get("final_score"),
                    "metadata_score": metadata_result.get("score"),
                    "llm_sense_rating": llm_validation.get("sense_rating"),
                }
            )

            page_score = float(page_report.get("final_score", 0.0))
            if page_score > worst_page_score:
                worst_page_score = page_score
                worst_page_report = page_report

        assert worst_page_report is not None
        worst_page_report["pdf_pages"] = page_summaries
        return worst_page_report

    def _assemble_report(
        self,
        *,
        metadata_result: dict[str, Any],
        llm_validation: dict[str, Any],
        ocr_text: str,
    ) -> dict[str, Any]:
        """Compute final score + verdict, including a CRITICAL metadata override."""

        metadata_score = float(metadata_result.get("score", 50.0))
        llm_coherence = float(llm_validation.get("sense_rating", 50.0))
        llm_risk_score = float(max(0.0, min(100.0, 100.0 - llm_coherence)))

        final_score = metadata_score * self.weights["metadata"] + llm_risk_score * self.weights["llm"]

        verdict = self._get_verdict(final_score, metadata_result=metadata_result)
        confidence = self._calculate_confidence(metadata_score=metadata_score, llm_risk_score=llm_risk_score)

        return {
            "final_score": round(float(final_score), 2),
            "verdict": verdict,
            "confidence": confidence,
            "metadata": metadata_result,
            "llm_validation": llm_validation,
            "ocr_text_preview": (ocr_text or "")[:300],
            "score_breakdown": {
                "metadata_contribution": round(metadata_score * self.weights["metadata"], 2),
                "llm_contribution": round(llm_risk_score * self.weights["llm"], 2),
                "llm_risk_score": round(llm_risk_score, 2),
                "weights": dict(self.weights),
            },
        }

    def _get_verdict(self, final_score: float, *, metadata_result: dict[str, Any]) -> str:
        """Determine verdict with context-aware logic."""

        all_flags = metadata_result.get("all_flags") or []
        if any("CRITICAL" in str(flag) for flag in all_flags):
            return "HIGH RISK - Critical Metadata Violation"
        return _final_verdict_from_score(final_score)

    def _calculate_confidence(self, *, metadata_score: float, llm_risk_score: float) -> str:
        """Assess confidence using agreement between metadata risk and LLM-derived risk."""

        metadata_high_risk = metadata_score > 60
        llm_high_risk = llm_risk_score > 60

        metadata_low_risk = metadata_score < 40
        llm_low_risk = llm_risk_score < 40

        if metadata_high_risk and llm_high_risk:
            return "High (metadata + LLM both indicate tampering)"
        if metadata_low_risk and llm_low_risk:
            return "High (metadata + LLM both look normal)"
        return "Medium (signals disagree - manual review recommended)"

