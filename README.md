# Invoice Tampering Detector v2.0

Local invoice fraud detection using **metadata forensics** plus **LLM semantic validation** (n8n → Ollama).

## What changed in v2.0

### Removed
- Unreliable compression-forensics approaches that produced high false positives
- OCR math heuristics (line-item parsing and total checks)

### Added
- **Metadata-first scoring (60%)** with field-level weighting and critical overrides
- **LLM validation (40%)** by sending OCR text to a local **n8n webhook**, which calls **Phi-3 via Ollama**
- **Multi-page PDFs**: analyze **all pages**, return the **worst-case (max risk)** page

## Architecture

```
Invoice Upload
  ↓
Metadata Analysis (risk 0-100, weight 60%)
  - Software signatures (editors)
  - DateTime consistency
  - Device info signals
  - Thumbnail presence
  ↓
OCR Extraction (raw text only)
  ↓
LLM Semantic Validation (coherence 0-100)
  - POST text → n8n webhook
  - n8n → Ollama (Phi-3)
  - LLM returns sense_rating (coherence) + reasoning
  ↓
Final Risk Score (0-100):
  risk = metadata_risk*0.60 + (100 - llm_coherence)*0.40
```

## Installation

### 1) Python dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) System dependencies

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

Verify:

```bash
tesseract --version
```

#### Poppler (required for PDF rendering via `pdf2image`)
- Fedora:

```bash
sudo dnf install -y poppler-utils
```

- Ubuntu/Debian:

```bash
sudo apt-get install -y poppler-utils
```

### 3) Ollama + Phi-3 (local LLM)

Install Ollama and pull the model:

```bash
ollama pull phi3
```

Start Ollama:

```bash
ollama serve
```

### 4) n8n workflow

Start n8n (any local setup is fine). Then import:
- `n8n_workflow/llm_validation.json`

Activate the workflow. It exposes:
- Active webhook: `http://localhost:5678/webhook/validate-ocr`
- Test webhook: `http://localhost:5678/webhook-test/validate-ocr`

## Run

```bash
python app.py
```

Open:
- `http://localhost:5000`

## Configuration

Environment variables:

```bash
export N8N_WEBHOOK_URL=http://localhost:5678/webhook/validate-ocr
```

## API

### Analyze invoice

```bash
curl -s -X POST http://localhost:5000/api/analyze \
  -F "file=@invoice.jpg"
```

### Health check

```bash
curl -s http://localhost:5000/api/health
```

## Notes on scoring

- **Missing EXIF** is treated as suspicious in this build (risk score 75) even for PNG-like inputs. In production, you may want to soften PNG handling depending on your invoice sources.
- **Critical override**: if metadata shows a `CRITICAL:` software signature (editor), the verdict is forced to **HIGH RISK** regardless of LLM output.
- **LLM conversion**: the LLM returns coherence. The scorer converts that to risk as `risk = 100 - coherence` so coherent invoices lower risk.

## Testing

```bash
pytest -q
```

Integration tests expect sample folders:
- `tests/samples/legitimate/`
- `tests/samples/tampered/`


