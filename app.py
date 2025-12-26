"""Flask API for local invoice tampering analysis (v2.0).

v2 pipeline:
- Metadata-first scoring (EXIF) as the primary signal
- OCR extraction (text only)
- LLM semantic validation via n8n webhook (local-only)

Endpoints:
- GET /            : upload UI
- POST /api/analyze: analyze invoice (JPG/PNG/PDF)
- GET /api/health  : API + n8n connectivity status
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from PIL import UnidentifiedImageError
from werkzeug.utils import secure_filename

from detector.fraud_scorer import FraudScorer


PROJECT_ROOT = Path(__file__).resolve().parent
UPLOAD_FOLDER = PROJECT_ROOT / "uploads"

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}
MAX_UPLOAD_BYTES = 16 * 1024 * 1024


def _ensure_runtime_directories() -> None:
    """Ensure `uploads/` exists so the app can run locally."""

    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)


def _is_allowed_filename(uploaded_filename: str) -> bool:
    """Validate a filename by extension (strict allow-list)."""

    return Path(uploaded_filename).suffix.lower() in ALLOWED_EXTENSIONS


def _save_uploaded_file(uploaded_file) -> Path:
    """Persist an uploaded file to uploads/ with a collision-resistant name."""

    safe_name = secure_filename(uploaded_file.filename or "")
    if not safe_name:
        raise ValueError("Empty filename after sanitization.")

    unique_prefix = uuid.uuid4().hex[:12]
    destination_path = UPLOAD_FOLDER / f"{unique_prefix}_{safe_name}"

    try:
        uploaded_file.save(destination_path)
    except OSError as save_error:
        raise OSError(f"Failed to save upload: {save_error}") from save_error

    return destination_path


_ensure_runtime_directories()

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)
app.config["N8N_WEBHOOK_URL"] = os.environ.get(
    "N8N_WEBHOOK_URL",
    "http://localhost:5678/webhook/validate-ocr",
)

fraud_scorer = FraudScorer(
    n8n_webhook_url=app.config["N8N_WEBHOOK_URL"],
    pdf_dpi=200,
    max_image_width_px=2000,
)


@app.get("/")
def index():
    """Serve the single-page upload UI."""

    return render_template("index.html")


@app.post("/api/analyze")
def analyze_invoice():
    """Analyze an uploaded invoice and return a JSON fraud assessment."""

    uploaded_file = request.files.get("file")
    if uploaded_file is None:
        return jsonify({"error": "Missing file field 'file'."}), 400

    if not _is_allowed_filename(uploaded_file.filename or ""):
        return jsonify({"error": "Invalid file type. Allowed: jpg, jpeg, png, pdf."}), 400

    saved_path: Path | None = None
    try:
        saved_path = _save_uploaded_file(uploaded_file)
        analysis_result = fraud_scorer.analyze_invoice(str(saved_path))

        return jsonify(analysis_result)
    except (ValueError, UnidentifiedImageError) as client_error:
        app.logger.exception("Invoice analysis rejected: %s", client_error)
        return jsonify({"error": str(client_error)}), 400
    except Exception as processing_error:  # noqa: BLE001 - API boundary
        app.logger.exception("Invoice analysis failed: %s", processing_error)
        return jsonify({"error": str(processing_error)}), 500
    finally:
        if saved_path and saved_path.exists():
            try:
                os.remove(saved_path)
            except OSError:
                pass


@app.get("/api/health")
def health_check():
    """Check API health plus n8n webhook reachability."""

    n8n_ok, n8n_message = fraud_scorer.llm_validator.test_connection()
    return jsonify(
        {
            "status": "healthy" if n8n_ok else "degraded",
            "components": {
                "api": "operational",
                "n8n_webhook": "operational" if n8n_ok else "unavailable",
                "n8n_message": n8n_message,
            },
            "n8n_webhook_url": app.config["N8N_WEBHOOK_URL"],
        }
    )


if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "").strip() == "1"
    app.run(host="0.0.0.0", port=5000, debug=debug_mode)


