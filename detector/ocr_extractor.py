"""OCR text extraction for invoice images (v2.0).

This module extracts text only. It does not attempt to validate totals, line items,
or perform fraud scoring. Semantic validation is delegated to the LLM via n8n.
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np
import pytesseract
from PIL import Image


def _mean_tesseract_confidence(preprocessed_image: np.ndarray) -> float | None:
    """Estimate OCR confidence as the mean of word-level confidences (0-100)."""

    try:
        ocr_data = pytesseract.image_to_data(preprocessed_image, output_type=pytesseract.Output.DICT)
    except Exception:
        return None

    confidence_values: list[int] = []
    for confidence_text in ocr_data.get("conf", []):
        try:
            confidence_int = int(float(confidence_text))
        except (TypeError, ValueError):
            continue

        if confidence_int >= 0:
            confidence_values.append(confidence_int)

    if not confidence_values:
        return None

    return float(sum(confidence_values) / len(confidence_values))


class OCRExtractor:
    """
    Extract text from invoice images using Tesseract.

    Business behavior: return raw text as-is, even if messy. The next stage (LLM)
    decides whether the content is coherent invoice-like text or suspicious gibberish.
    """

    def preprocess_image(self, invoice_image_bgr: np.ndarray) -> np.ndarray:
        """Prepare an image for OCR: grayscale → OTSU → median blur."""

        grayscale = cv2.cvtColor(invoice_image_bgr, cv2.COLOR_BGR2GRAY)
        _, thresholded = cv2.threshold(grayscale, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return cv2.medianBlur(thresholded, 3)

    def extract_text(
        self,
        image_path: str | None = None,
        *,
        invoice_image: Image.Image | None = None,
    ) -> dict[str, Any]:
        """
        Extract all text from an invoice image.

        Returns:
        - raw_text: full OCR output
        - text_length: character count
        - extraction_confidence: optional mean confidence (0-100) when available
        - error: None or string
        """

        try:
            if invoice_image is not None:
                invoice_rgb = invoice_image.convert("RGB")
                invoice_rgb_pixels = np.array(invoice_rgb)
                invoice_bgr_pixels = cv2.cvtColor(invoice_rgb_pixels, cv2.COLOR_RGB2BGR)
            else:
                if not image_path:
                    raise ValueError("Missing image_path and invoice_image.")

                invoice_bgr_pixels = cv2.imread(image_path)
                if invoice_bgr_pixels is None:
                    raise ValueError("OpenCV could not read the invoice image.")

            preprocessed = self.preprocess_image(invoice_bgr_pixels)
            raw_text = pytesseract.image_to_string(preprocessed)
            raw_text = (raw_text or "").replace("\x00", "")

            confidence_estimate = _mean_tesseract_confidence(preprocessed)

            return {
                "raw_text": raw_text,
                "text_length": int(len(raw_text)),
                "extraction_confidence": confidence_estimate,
                "error": None,
            }
        except Exception as extraction_error:  # noqa: BLE001 - OCR can fail for many image-specific reasons
            return {
                "raw_text": "",
                "text_length": 0,
                "extraction_confidence": None,
                "error": str(extraction_error),
            }


