# Invoice Tampering Detector (Local, Free)

Production-ready **invoice fraud detection** for JPEG/PNG/PDF invoices using:
- **Error Level Analysis (ELA)** to surface localized edits
- **EXIF inspection** to spot editing software and metadata inconsistencies
- **OCR + math validation** to catch amount tampering and inconsistent totals

No paid APIs. No database. Runs locally.

---

## Architecture (high level)

```
Browser_UI
  |
  v
Flask_API (/api/analyze)
  |
  v
InvoiceFraudScorer
  |-- ELA (Pillow) --------------> score_0_100 + ELA image
  |-- EXIF metadata (Pillow) ----> score_0_100 + flags
  |-- OCR + math (OpenCV+Tesseract) -> score_0_100 + flags
  |
  v
Weighted final_score = ela*0.4 + metadata*0.3 + ocr*0.3
Verdict:
  0-39  LOW RISK
  40-64 MEDIUM RISK
  65-100 HIGH RISK
```

---

## Installation

### 1) Python deps

> **⚠️ IMPORTANT:** 
Use ```*Python 3.11```. ```Python 3.14``` has compatibility issues with scientific packages like numpy that require compilation from source.

```bash
cd invoice-tampering-detector
python3.11 -m venv .venv  # Use python3.11, not python
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) System deps (required for OCR + PDF)

#### Tesseract OCR
- Fedora:

```bash
sudo dnf install -y tesseract tesseract-langpack-eng
```

- Ubuntu/Debian:

```bash
sudo apt-get update
sudo apt-get install -y tesseract-ocr
```

- macOS (Homebrew):

```bash
brew install tesseract
```

Verify:

```bash
tesseract --version
```

#### Poppler (PDF rendering for `pdf2image`)
- Fedora:

```bash
sudo dnf install -y poppler-utils
```

- Ubuntu/Debian:

```bash
sudo apt-get install -y poppler-utils
```

- macOS (Homebrew):

```bash
brew install poppler
```

---

## Run (Web UI)

```bash
cd invoice-tampering-detector
python app.py
```

Open `http://localhost:5000` and upload an invoice (JPG/PNG/PDF).

ELA visualizations are saved under `static/results/`.

---

## Run (API)

```bash
curl -s -X POST http://localhost:5000/api/analyze \
  -F "file=@tests/samples/legitimate/example.jpg" | jq
```

Response shape:

```json
{
  "final_score": 75.5,
  "verdict": "HIGH RISK - Likely Tampered",
  "ela": { "...": "..." },
  "metadata": { "...": "..." },
  "ocr": { "...": "..." }
}
```

---

## Testing with your sample invoices

Put at least 5 files in each folder:
- `tests/samples/legitimate/`
- `tests/samples/tampered/`

Then run:

```bash
cd invoice-tampering-detector
pytest -q
```

The integration test prints a small result table and asserts:
- **Detection rate > 70%** (tampered invoices flagged as MEDIUM/HIGH risk)

---

## How it works (detectors)

### ELA (Pillow)
- Re-saves the image as JPEG quality=90, then computes the pixel difference.
- Enhances the difference image to highlight regions that compress differently.
- Scores from mean brightness + variance using:
  - `score = min(100, (brightness/255*50) + (variance/1000*50))`
- If there are **no compression artifacts** (max diff == 0), it returns **score=50** (suspicious).

### Metadata (EXIF)
- Extracts EXIF tags and checks for:
  - Editing software (Photoshop/GIMP/Paint.NET) (+30)
  - `DateTime` != `DateTimeOriginal` (+20)
  - Missing Make/Model/DateTime (+15)
  - No EXIF at all => **50** (suspicious)

### OCR + math validation
- Preprocesses: grayscale → OTSU threshold → median blur
- OCR via Tesseract, parses amounts with regex:
  - `\$?\d+[,.]?\d*\.?\d{2}`
- Scores flags:
  - <2 amounts (+40)
  - duplicates (+20)
  - line-items sum mismatch vs total beyond ±15% (+35)

---

## Known limitations
- **ELA is strongest on JPEG** inputs. PNG screenshots or images with stripped compression history can trigger the “no artifacts” heuristic.
- **EXIF is often missing** after messaging apps, screenshots, or PDF export; treat metadata as supporting evidence, not proof.
- **OCR is sensitive to scan quality**. Low-resolution or skewed invoices can increase false positives.
- **Multi-page PDFs**: only the first page is analyzed (by design for speed).

---

## Project layout

```
invoice-tampering-detector/
  app.py
  detector/
    ela_detector.py
    metadata_checker.py
    ocr_validator.py
    fraud_scorer.py
  templates/
    index.html
  static/
    results/
  uploads/
  tests/
    test_ela.py
    test_metadata.py
    test_integration.py
    samples/
      legitimate/
      tampered/
```


