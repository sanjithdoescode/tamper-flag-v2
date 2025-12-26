# Forensic Checks and Validation Pipeline

## ELA (compression forensics)

The image is converted to RGB, re-saved as JPEG (default quality 90), then differenced against the original. If the max pixel diff is zero it’s treated as suspicious (score 50). Otherwise mean brightness and variance of the diff drive a 0–100 score, and a scaled diff image is saved for visualization.

**Source: `ela_detector.py` Lines 83-100**

```python
    if ela_metrics.max_pixel_difference == 0:
        return 50.0
    brightness_component = (ela_metrics.brightness_mean / 255.0) * 50.0
    variance_component = (ela_metrics.brightness_variance / 1000.0) * 50.0
    return float(min(100.0, brightness_component + variance_component))
```

## Metadata/EXIF inspection

EXIF is extracted; if absent, it returns a suspicious verdict and score 50. Flags add to the score for editing software markers (+30), DateTime vs DateTimeOriginal mismatch (+20), and missing Make/Model/DateTime (+15).

**Source: `metadata_checker.py` Lines 91-124**

```python
        exif_metadata = _extract_exif_metadata(invoice_image)
        if not exif_metadata:
            return {
                "score": 50.0,
                "verdict": "SUSPICIOUS - No EXIF metadata found",
                ...
            }
        flags: list[str] = []
        score = 0.0
        editing_software = _detect_editing_software(exif_metadata)
        if editing_software:
            score += 30.0
        ...
        if date_time and date_time_original and date_time != date_time_original:
            score += 20.0
        ...
        if missing_fields:
            score += 15.0
```

## OCR + math validation

Image is preprocessed (grayscale → OTSU threshold → median blur) then sent to Tesseract. Currency strings are parsed with a regex; the checker flags too few amounts (<2), duplicate amounts, and when line items don’t sum to the total within ±15%. Each flag adds to the 0–100 risk score.

**Source: `ocr_validator.py` Lines 66-120**

```python
def _preprocess_for_ocr(invoice_image: Image.Image) -> np.ndarray:
    invoice_rgb = invoice_image.convert("RGB")
    grayscale = cv2.cvtColor(np.array(invoice_rgb), cv2.COLOR_RGB2GRAY)
    _, thresholded = cv2.threshold(grayscale, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return cv2.medianBlur(thresholded, 3)
...
    if len(amount_values) < 2:
        score += 40.0
    ...
    if duplicates:
        score += 20.0
    ...
    if _sum_mismatch_with_tolerance(amount_values, tolerance_ratio=tolerance_ratio):
        score += 35.0
```

## Scoring Blend

(from README)

The API aggregates ELA, metadata, and OCR subscores into a weighted `final_score = 0.4ELA + 0.3metadata + 0.3OCR`, then labels LOW/MEDIUM/HIGH risk.

## Tools & Libraries Used

*   **Pillow** for image I/O, recompression, EXIF, and diff/brightness stats.
*   **OpenCV + NumPy** for OCR preprocessing.
*   **Tesseract** via `pytesseract` for text extraction.
*   **Regex parsing and simple arithmetic checks** for amount consistency.