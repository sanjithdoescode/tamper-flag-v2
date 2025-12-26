"""Error Level Analysis (ELA) for invoice image forensics.

ELA highlights regions that compress differently from the surrounding image,
which often occurs after localized edits (copy/paste, text replacement, etc.).
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from PIL import Image, ImageChops, ImageEnhance, ImageStat, UnidentifiedImageError


@dataclass(frozen=True)
class ElaMetrics:
    """Measured ELA statistics used for scoring."""

    brightness_mean: float
    brightness_variance: float
    max_pixel_difference: int
    jpeg_quality: int


def _utc_timestamp_compact() -> str:
    """Return a filesystem-safe UTC timestamp string."""

    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def _ensure_directory(directory_path: str) -> None:
    """Create the directory if missing (best-effort)."""

    try:
        os.makedirs(directory_path, exist_ok=True)
    except OSError as os_error:
        raise OSError(f"Failed to create directory: {directory_path}") from os_error


def _risk_verdict_for_score(score: float, *, method_label: str) -> str:
    """Convert a 0–100 score into a human-readable verdict string."""

    if score >= 65:
        return f"HIGH {method_label} RISK"
    if score >= 40:
        return f"MEDIUM {method_label} RISK"
    return f"LOW {method_label} RISK"


def _calculate_ela_difference(original_rgb: Image.Image, recompressed_rgb: Image.Image) -> dict[str, Any]:
    """Compute ELA visualization and return it with the max pixel difference."""

    difference_image = ImageChops.difference(original_rgb, recompressed_rgb)
    extrema_by_channel = difference_image.getextrema()
    max_difference = max(channel_extrema[1] for channel_extrema in extrema_by_channel)

    if max_difference == 0:
        return {"ela_image": difference_image, "max_difference": 0}

    scale_factor = 255.0 / float(max_difference)
    enhanced_difference = ImageEnhance.Brightness(difference_image).enhance(scale_factor)
    return {"ela_image": enhanced_difference, "max_difference": int(max_difference)}


def _measure_ela_metrics(ela_image: Image.Image, *, max_difference: int, jpeg_quality: int) -> ElaMetrics:
    """Measure mean brightness and variance from the ELA image."""

    ela_grayscale = ela_image.convert("L")
    grayscale_stats = ImageStat.Stat(ela_grayscale)

    return ElaMetrics(
        brightness_mean=float(grayscale_stats.mean[0]),
        brightness_variance=float(grayscale_stats.var[0]),
        max_pixel_difference=int(max_difference),
        jpeg_quality=int(jpeg_quality),
    )


def _score_from_ela_metrics(ela_metrics: ElaMetrics) -> float:
    """Compute the ELA anomaly score from the measured metrics."""

    if ela_metrics.max_pixel_difference == 0:
        return 50.0

    brightness_component = (ela_metrics.brightness_mean / 255.0) * 50.0
    variance_component = (ela_metrics.brightness_variance / 1000.0) * 50.0
    return float(min(100.0, brightness_component + variance_component))


class InvoiceElaAnalyzer:
    """Runs Error Level Analysis (ELA) and persists a visualization image."""

    def analyze_image_path(
        self,
        image_path: str,
        results_directory: str,
        jpeg_quality: int = 90,
        *,
        public_results_prefix: str | None = "/static/results",
    ) -> dict[str, Any]:
        """Analyze an image file path and return a structured ELA report."""

        try:
            with Image.open(image_path) as opened_image:
                return self.analyze_invoice_image(
                    opened_image,
                    results_directory,
                    jpeg_quality=jpeg_quality,
                    public_results_prefix=public_results_prefix,
                )
        except (OSError, UnidentifiedImageError) as open_error:
            return {
                "score": 50.0,
                "verdict": "INCONCLUSIVE - ELA read failed",
                "visualization_path": None,
                "metrics": {},
                "error": str(open_error),
            }

    def analyze_invoice_image(
        self,
        invoice_image: Image.Image,
        results_directory: str,
        *,
        jpeg_quality: int = 90,
        public_results_prefix: str | None = "/static/results",
    ) -> dict[str, Any]:
        """Run ELA on a Pillow image and save a visualization into results_directory."""

        _ensure_directory(results_directory)

        try:
            ela_observations = self._compute_ela_observations(invoice_image, jpeg_quality=jpeg_quality)
            ela_metrics: ElaMetrics = ela_observations["metrics"]
            visualization_path = self._persist_visualization_and_get_path(
                ela_observations["ela_image"],
                results_directory=results_directory,
                public_results_prefix=public_results_prefix,
            )

            return {
                "score": float(round(float(ela_observations["score"]), 2)),
                "verdict": str(ela_observations["verdict"]),
                "visualization_path": visualization_path,
                "metrics": {
                    "brightness_mean": ela_metrics.brightness_mean,
                    "brightness_variance": ela_metrics.brightness_variance,
                    "max_pixel_difference": ela_metrics.max_pixel_difference,
                    "jpeg_quality": ela_metrics.jpeg_quality,
                },
                "error": None,
            }
        except Exception as ela_error:  # noqa: BLE001 - safety net for forensic processing
            return {
                "score": 50.0,
                "verdict": "INCONCLUSIVE - ELA processing failed",
                "visualization_path": None,
                "metrics": {},
                "error": str(ela_error),
            }

    def _compute_ela_observations(self, invoice_image: Image.Image, *, jpeg_quality: int) -> dict[str, Any]:
        """Compute ELA image + metrics without writing any files."""

        original_rgb = invoice_image.convert("RGB")
        recompressed_rgb = self._recompress_and_reload_as_rgb(original_rgb, jpeg_quality=jpeg_quality)
        ela_result = _calculate_ela_difference(original_rgb, recompressed_rgb)

        ela_image: Image.Image = ela_result["ela_image"]
        max_difference: int = int(ela_result["max_difference"])
        ela_metrics = _measure_ela_metrics(ela_image, max_difference=max_difference, jpeg_quality=jpeg_quality)

        ela_score = _score_from_ela_metrics(ela_metrics)
        verdict = (
            "SUSPICIOUS - No compression artifacts detected"
            if max_difference == 0
            else _risk_verdict_for_score(ela_score, method_label="ELA")
        )

        return {"ela_image": ela_image, "metrics": ela_metrics, "score": ela_score, "verdict": verdict}

    def _persist_visualization_and_get_path(
        self,
        ela_image: Image.Image,
        *,
        results_directory: str,
        public_results_prefix: str | None,
    ) -> str:
        """Save ELA visualization and return a path suitable for UI display."""

        visualization_filename = f"ela_{_utc_timestamp_compact()}.png"
        visualization_file_path = os.path.join(results_directory, visualization_filename)
        self._save_visualization(ela_image, visualization_file_path)

        if public_results_prefix:
            return f"{public_results_prefix.rstrip('/')}/{visualization_filename}"
        return visualization_file_path

    def _recompress_and_reload_as_rgb(self, original_rgb: Image.Image, *, jpeg_quality: int) -> Image.Image:
        """Re-save an image as JPEG at the given quality and reload it as RGB."""

        temporary_file_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as temp_file:
                temporary_file_path = temp_file.name

            original_rgb.save(temporary_file_path, "JPEG", quality=int(jpeg_quality))
            with Image.open(temporary_file_path) as recompressed_image:
                return recompressed_image.convert("RGB")
        except OSError as io_error:
            raise OSError("Failed to recompress image for ELA.") from io_error
        finally:
            if temporary_file_path:
                try:
                    os.remove(temporary_file_path)
                except OSError:
                    pass

    def _save_visualization(self, ela_image: Image.Image, destination_path: str) -> None:
        """Persist the ELA visualization image to disk."""

        try:
            ela_image.save(destination_path, "PNG")
        except OSError as save_error:
            raise OSError(f"Failed to save ELA visualization: {destination_path}") from save_error

