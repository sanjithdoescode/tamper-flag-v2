# v2 Pipeline Notes (metadata-first + LLM validation)

This repo intentionally focuses on **repeatable invoice tampering signals**:

- **Metadata (EXIF)** is treated as the primary fraud signal because it often contains
  concrete evidence of editing/export tools.
- **OCR** is used only to extract text; it does not attempt to validate arithmetic.
- **LLM validation** happens via a local n8n workflow that calls Ollama (Phi-3) and
  returns a coherence score plus a short explanation.

## 1) Metadata scoring (primary)

Implemented in `detector/metadata_checker.py` (`EnhancedMetadataChecker`).

The metadata score is a weighted combination of 4 categories:
- Software signatures (40%)
- DateTime consistency (25%)
- Device info signals (20%)
- Thumbnail presence (15%)

If EXIF is missing, the system treats it as suspicious (risk score 75) and records
flags explaining that field-level checks cannot be performed.

## 2) OCR extraction (text only)

Implemented in `detector/ocr_extractor.py` (`OCRExtractor`).

Preprocessing:
grayscale → OTSU threshold → median blur, then `pytesseract.image_to_string()`.

## 3) LLM validation (n8n → Ollama)

Implemented in `detector/llm_validator.py` (`LLMValidator`).

The validator POSTs OCR text to `N8N_WEBHOOK_URL` and expects:
```json
{"sense_rating": 0-100, "reasoning": "..."}
```

If n8n/Ollama is unavailable, the validator returns a neutral score (50) and sets
an `error` field so the API/UI can surface degraded mode.

## 4) Final scoring

Implemented in `detector/fraud_scorer.py` (`FraudScorer`).

Final risk:
`risk = metadata_risk*0.60 + (100 - llm_coherence)*0.40`

For PDFs, all pages are analyzed and the **worst-case** page score is returned.