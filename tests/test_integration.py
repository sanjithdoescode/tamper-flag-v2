"""End-to-end detector evaluation using your sample invoice folders.

Place files here:
- tests/samples/legitimate/  (5+ clean invoices)
- tests/samples/tampered/   (5+ tampered invoices)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from detector.fraud_scorer import InvoiceFraudScorer


ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}


def _collect_sample_files(sample_directory: Path) -> list[Path]:
    """Collect sample invoice files by extension."""

    if not sample_directory.exists():
        return []

    return sorted(
        [
            candidate
            for candidate in sample_directory.iterdir()
            if candidate.is_file() and candidate.suffix.lower() in ALLOWED_EXTENSIONS
        ]
    )


@pytest.mark.integration
def test_detection_rate_exceeds_70_percent(tmp_path: Path) -> None:
    """Verify the detector flags a strong majority of tampered invoices."""

    tests_root = Path(__file__).resolve().parent
    legitimate_dir = tests_root / "samples" / "legitimate"
    tampered_dir = tests_root / "samples" / "tampered"

    legitimate_files = _collect_sample_files(legitimate_dir)
    tampered_files = _collect_sample_files(tampered_dir)

    assert len(legitimate_files) >= 5, f"Need 5+ legitimate samples in {legitimate_dir}"
    assert len(tampered_files) >= 5, f"Need 5+ tampered samples in {tampered_dir}"

    fraud_scorer = InvoiceFraudScorer(
        results_directory=str(tmp_path / "results"),
        public_results_prefix=None,
        max_image_width_px=2000,
        pdf_dpi=200,
    )

    true_positives = 0
    false_positives = 0

    print("\nfile,label,final_score,verdict")
    for tampered_path in tampered_files[:5]:
        analysis = fraud_scorer.analyze_invoice_file(str(tampered_path))
        flagged = float(analysis["final_score"]) >= 40.0
        true_positives += 1 if flagged else 0
        print(f"{tampered_path.name},tampered,{analysis['final_score']},{analysis['verdict']}")

    for legitimate_path in legitimate_files[:5]:
        analysis = fraud_scorer.analyze_invoice_file(str(legitimate_path))
        flagged = float(analysis["final_score"]) >= 40.0
        false_positives += 1 if flagged else 0
        print(f"{legitimate_path.name},legitimate,{analysis['final_score']},{analysis['verdict']}")

    detection_rate = true_positives / 5.0
    false_positive_rate = false_positives / 5.0

    print(f"\nDetection rate: {detection_rate:.2%}")
    print(f"False positive rate: {false_positive_rate:.2%}")

    assert detection_rate > 0.70


