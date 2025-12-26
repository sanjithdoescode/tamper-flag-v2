"""OCR + math validation for invoice consistency checks."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

import cv2
import numpy as np
import pytesseract
from PIL import Image, UnidentifiedImageError


AMOUNT_REGEX = re.compile(r"\$?\d+[,.]?\d*\.?\d{2}")


def _tesseract_is_available() -> dict[str, Any]:
    """Return a small diagnostic dict describing Tesseract availability."""

    try:
        version = str(pytesseract.get_tesseract_version())
        return {"available": True, "version": version}
    except Exception as version_error:
        return {"available": False, "version": None, "error": str(version_error)}


def _inconclusive_ocr_report(*, verdict: str, flags: list[str], error: str) -> dict[str, Any]:
    """Return a consistent OCR response payload when OCR can't run."""

    return {
        "score": 40.0,
        "verdict": verdict,
        "flags": flags,
        "extracted_text": "",
        "amounts": [],
        "error": error,
    }


def _risk_verdict_from_score(score: float) -> str:
    """Convert a 0–100 OCR score into a short verdict."""

    if score >= 65:
        return "HIGH OCR RISK"
    if score >= 40:
        return "MEDIUM OCR RISK"
    return "LOW OCR RISK"


def _parse_currency_amount(amount_text: str) -> float | None:
    """Parse common OCR currency strings into a float (best-effort)."""

    cleaned = amount_text.replace("$", "").replace(" ", "")
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    elif "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")

    try:
        return float(cleaned)
    except ValueError:
        return None


def _preprocess_for_ocr(invoice_image: Image.Image) -> np.ndarray:
    """Apply grayscale + OTSU threshold + median blur as OCR-friendly preprocessing."""

    invoice_rgb = invoice_image.convert("RGB")
    rgb_pixels = np.array(invoice_rgb)
    grayscale = cv2.cvtColor(rgb_pixels, cv2.COLOR_RGB2GRAY)
    _, thresholded = cv2.threshold(grayscale, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return cv2.medianBlur(thresholded, 3)


def _extract_text_with_tesseract(preprocessed_image: np.ndarray, *, tesseract_config: str) -> str:
    """Extract text using Tesseract from a preprocessed image array."""

    return pytesseract.image_to_string(preprocessed_image, config=tesseract_config)


def _extract_amounts_from_text(extracted_text: str) -> dict[str, Any]:
    """Parse amounts from OCR text and return both display and numeric representations."""

    raw_amounts = AMOUNT_REGEX.findall(extracted_text)
    parsed_amounts: list[dict[str, Any]] = []
    numeric_values: list[float] = []

    for raw_amount in raw_amounts:
        parsed_value = _parse_currency_amount(raw_amount)
        if parsed_value is None:
            continue
        parsed_amounts.append({"raw": raw_amount, "value": float(parsed_value)})
        numeric_values.append(float(parsed_value))

    return {"amounts": parsed_amounts, "numeric_values": numeric_values}


def _score_amount_consistency_checks(amount_values: list[float], *, tolerance_ratio: float) -> dict[str, Any]:
    """Score amount-based OCR findings and return score additions plus flags."""

    flags: list[str] = []
    score = 0.0

    if len(amount_values) < 2:
        score += 40.0
        flags.append("Too few amounts detected by OCR (< 2).")

    if amount_values:
        rounded_values = [round(value, 2) for value in amount_values]
        duplicates = [value for value, count in Counter(rounded_values).items() if count > 1]
        if duplicates:
            score += 20.0
            duplicate_list = ", ".join(f"{value:.2f}" for value in sorted(duplicates))
            flags.append(f"Duplicate amounts detected: {duplicate_list}")

    if _sum_mismatch_with_tolerance(amount_values, tolerance_ratio=tolerance_ratio):
        score += 35.0
        flags.append("Line items do not sum to the total within the allowed tolerance.")

    return {"score": score, "flags": flags}


def _sum_mismatch_with_tolerance(amount_values: list[float], *, tolerance_ratio: float) -> bool:
    """Check if line items (all but max) differ from max total beyond tolerance."""

    if len(amount_values) < 3:
        return False

    sorted_values = sorted(amount_values)
    total_value = sorted_values[-1]
    if total_value <= 0:
        return False

    line_items_sum = float(sum(sorted_values[:-1]))
    mismatch_ratio = abs(line_items_sum - total_value) / total_value
    return mismatch_ratio > tolerance_ratio


class InvoiceOcrMathValidator:
    """Run OCR and validate basic arithmetic consistency among detected amounts."""

    def validate_image_path(self, image_path: str) -> dict[str, Any]:
        """Validate OCR math checks for an image file path."""

        try:
            with Image.open(image_path) as opened_image:
                return self.validate_invoice_image(opened_image)
        except (OSError, UnidentifiedImageError) as open_error:
            return {
                "score": 40.0,
                "verdict": "INCONCLUSIVE - OCR read failed",
                "flags": ["Could not read the invoice image for OCR."],
                "extracted_text": "",
                "amounts": [],
                "error": str(open_error),
            }

    def validate_invoice_image(
        self,
        invoice_image: Image.Image,
        *,
        tolerance_ratio: float = 0.15,
        tesseract_config: str | None = None,
    ) -> dict[str, Any]:
        """Run OCR extraction and scoring with a ±tolerance_ratio total check."""

        tesseract_status = _tesseract_is_available()
        if not bool(tesseract_status.get("available")):
            return _inconclusive_ocr_report(
                verdict="INCONCLUSIVE - Tesseract not installed",
                flags=["Tesseract OCR is not available; install it to enable OCR checks."],
                error=str(tesseract_status.get("error") or "Tesseract not found"),
            )

        try:
            preprocessed = _preprocess_for_ocr(invoice_image)
            extracted_text = _extract_text_with_tesseract(
                preprocessed, tesseract_config=tesseract_config or ""
            )
        except Exception as ocr_error:  # noqa: BLE001 - OCR can fail for many image-specific reasons
            return _inconclusive_ocr_report(
                verdict="INCONCLUSIVE - OCR extraction failed",
                flags=["OCR extraction failed; poor scan quality can trigger this."],
                error=str(ocr_error),
            )

        extracted_text_display = extracted_text.replace("\x00", "")[:500]
        amount_bundle = _extract_amounts_from_text(extracted_text)
        numeric_values: list[float] = amount_bundle["numeric_values"]

        scoring_bundle = _score_amount_consistency_checks(numeric_values, tolerance_ratio=tolerance_ratio)
        score = float(scoring_bundle["score"])
        flags: list[str] = scoring_bundle["flags"]

        return {
            "score": float(min(100.0, score)),
            "verdict": _risk_verdict_from_score(score),
            "flags": flags,
            "extracted_text": extracted_text_display,
            "amounts": amount_bundle["amounts"],
            "error": None,
        }

