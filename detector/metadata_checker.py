"""Priority-based EXIF metadata analysis.

v2.0: Metadata is the PRIMARY fraud signal (60% of the final score).

The detector does not try to do compression forensics. It focuses on concrete,
repeatable signals that frequently survive real-world invoice tampering flows:
editing software signatures, inconsistent timestamps, and suspicious device info.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from PIL import ExifTags, Image, UnidentifiedImageError


def _safe_display_value(raw_value: Any, *, max_chars: int = 140) -> str:
    """Convert an EXIF value into a UI-safe string with a hard cap."""

    try:
        if isinstance(raw_value, bytes):
            rendered = raw_value.decode("utf-8", errors="replace")
        else:
            rendered = str(raw_value)
    except Exception:
        rendered = repr(raw_value)

    rendered = rendered.replace("\n", " ").strip()
    if len(rendered) <= max_chars:
        return rendered
    return f"{rendered[: max_chars - 1]}…"


def _risk_bucket(score_0_to_100: float) -> str:
    """Human-facing risk label."""

    if score_0_to_100 >= 70:
        return "HIGH RISK"
    if score_0_to_100 >= 45:
        return "MEDIUM RISK"
    return "LOW RISK"


def _clamp_score(score_0_to_100: float) -> float:
    return float(max(0.0, min(100.0, score_0_to_100)))


def _extract_exif(invoice_image: Image.Image) -> dict[str, str]:
    """Extract EXIF using Pillow's private hook, returned as a string-keyed dict."""

    try:
        exif_raw = invoice_image._getexif()  # noqa: SLF001 - required by spec
    except Exception:
        exif_raw = None

    if not exif_raw:
        return {}

    exif_metadata: dict[str, str] = {}
    for tag_id, tag_value in exif_raw.items():
        tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
        exif_metadata[tag_name] = _safe_display_value(tag_value)

    return exif_metadata


def _parse_exif_datetime(datetime_text: str) -> datetime | None:
    """Parse EXIF DateTime-like strings: 'YYYY:MM:DD HH:MM:SS'."""

    datetime_text = (datetime_text or "").strip()
    if not datetime_text:
        return None

    try:
        return datetime.strptime(datetime_text, "%Y:%m:%d %H:%M:%S")  # noqa: DTZ007 - EXIF has no TZ
    except ValueError:
        return None


@dataclass(frozen=True)
class _FieldAssessment:
    score: float
    flags: list[str]
    weight: float

    def as_dict(self) -> dict[str, Any]:
        return {"score": float(self.score), "flags": list(self.flags), "weight": float(self.weight)}


class EnhancedMetadataChecker:
    """
    Priority-based metadata analysis with field-level weighting.

    Real invoice tampering usually leaves consistent traces in EXIF: a known editor
    in the Software tag, unexpected timestamp changes, or missing/odd device info.
    """

    def __init__(self) -> None:
        self.field_weights = {
            "software": 0.40,
            "datetime": 0.25,
            "device": 0.20,
            "thumbnail": 0.15,
        }

        self.editing_software = {
            "photoshop": 90,
            "gimp": 85,
            "paint.net": 85,
            "pixlr": 80,
            "affinity photo": 85,
            "adobe acrobat": 70,
            "microsoft word": 75,
        }

        self.scanner_brands = [
            "canon",
            "epson",
            "hp",
            "brother",
            "ricoh",
            "fujitsu",
            "xerox",
        ]

    def analyze(self, image_path: str, *, invoice_image: Image.Image | None = None) -> dict[str, Any]:
        """
        Perform weighted metadata analysis.

        Returns a 0-100 risk score and a per-field breakdown suitable for APIs/UI.
        """

        file_extension = Path(image_path).suffix.lower()

        loaded_image: Image.Image | None = None
        try:
            if invoice_image is None:
                loaded_image = Image.open(image_path)
                invoice_image = loaded_image

            assert invoice_image is not None  # for type narrowing
            exif_metadata = _extract_exif(invoice_image)

            if not exif_metadata:
                missing_exif_flags = [
                    f"No EXIF metadata present for {file_extension or 'unknown'} input; treated as suspicious.",
                ]
                # You explicitly chose to treat PNG/no-EXIF as high risk (same as JPEG/no-EXIF).
                fixed_missing_exif_score = 75.0
                field_breakdown = {
                    category: _FieldAssessment(
                        score=fixed_missing_exif_score,
                        flags=[f"{category}: cannot validate (missing EXIF)."],
                        weight=self.field_weights[category],
                    ).as_dict()
                    for category in self.field_weights
                }
                return {
                    "score": fixed_missing_exif_score,
                    "field_breakdown": field_breakdown,
                    "all_flags": missing_exif_flags,
                    "verdict": _risk_bucket(fixed_missing_exif_score),
                    "raw_metadata": {},
                    "error": None,
                }

            software_assessment = self._analyze_software_field(exif_metadata)
            datetime_assessment = self._analyze_datetime_fields(exif_metadata)
            device_assessment = self._analyze_device_info(exif_metadata, file_extension=file_extension)
            thumbnail_assessment = self._analyze_thumbnail_consistency(image_path, invoice_image=invoice_image)

            field_breakdown = {
                "software": software_assessment.as_dict(),
                "datetime": datetime_assessment.as_dict(),
                "device": device_assessment.as_dict(),
                "thumbnail": thumbnail_assessment.as_dict(),
            }

            weighted_score = (
                software_assessment.score * software_assessment.weight
                + datetime_assessment.score * datetime_assessment.weight
                + device_assessment.score * device_assessment.weight
                + thumbnail_assessment.score * thumbnail_assessment.weight
            )

            all_flags: list[str] = []
            for section_key in ("software", "datetime", "device", "thumbnail"):
                all_flags.extend(field_breakdown[section_key]["flags"])

            # Critical override: editing software is strong evidence.
            if software_assessment.score >= 80:
                weighted_score = max(float(weighted_score), 75.0)

            weighted_score = _clamp_score(float(weighted_score))

            return {
                "score": weighted_score,
                "field_breakdown": field_breakdown,
                "all_flags": all_flags,
                "verdict": _risk_bucket(weighted_score),
                "raw_metadata": exif_metadata,
                "error": None,
            }
        except (OSError, UnidentifiedImageError) as open_error:
            fallback_score = 50.0
            return {
                "score": fallback_score,
                "field_breakdown": {
                    category: _FieldAssessment(
                        score=fallback_score,
                        flags=[f"{category}: metadata read failed ({open_error})."],
                        weight=self.field_weights[category],
                    ).as_dict()
                    for category in self.field_weights
                },
                "all_flags": ["Could not read image metadata."],
                "verdict": _risk_bucket(fallback_score),
                "raw_metadata": {},
                "error": str(open_error),
            }
        finally:
            if loaded_image is not None:
                try:
                    loaded_image.close()
                except Exception:
                    pass

    def _analyze_software_field(self, metadata: dict[str, str]) -> _FieldAssessment:
        """Check Software field for editing tool signatures. Weight: 40% of metadata score."""

        weight = self.field_weights["software"]

        software_hint = (
            metadata.get("Software")
            or metadata.get("ProcessingSoftware")
            or metadata.get("CreatorTool")
            or ""
        ).strip()

        if not software_hint:
            return _FieldAssessment(
                score=40.0,
                flags=["Software: tag missing (cannot confirm capture source)."],
                weight=weight,
            )

        normalized_software = software_hint.lower()

        matched_editor: str | None = None
        matched_score: float | None = None
        for signature, risk_score in self.editing_software.items():
            if signature in normalized_software:
                matched_editor = signature
                matched_score = float(risk_score)
                break

        if matched_editor is not None and matched_score is not None:
            return _FieldAssessment(
                score=matched_score,
                flags=[f"CRITICAL: Editing software signature detected: {software_hint}"],
                weight=weight,
            )

        if any(scanner_brand in normalized_software for scanner_brand in self.scanner_brands) or "scan" in normalized_software:
            return _FieldAssessment(
                score=5.0,
                flags=[f"Software suggests scanning pipeline: {software_hint}"],
                weight=weight,
            )

        return _FieldAssessment(
            score=10.0,
            flags=[f"Software looks benign: {software_hint}"],
            weight=weight,
        )

    def _analyze_datetime_fields(self, metadata: dict[str, str]) -> _FieldAssessment:
        """Check DateTime consistency across EXIF tags. Weight: 25% of metadata score."""

        weight = self.field_weights["datetime"]
        flags: list[str] = []

        modified_text = metadata.get("DateTime", "")  # typically last-save time
        created_text = metadata.get("DateTimeOriginal", "") or metadata.get("DateTimeDigitized", "")

        modified_dt = _parse_exif_datetime(modified_text)
        created_dt = _parse_exif_datetime(created_text)

        if modified_text and modified_dt is None:
            flags.append(f"DateTime: unparseable value '{modified_text}'.")
        if created_text and created_dt is None:
            flags.append(f"DateTimeOriginal/DateTimeDigitized: unparseable value '{created_text}'.")

        if modified_dt is None and created_dt is None:
            return _FieldAssessment(
                score=50.0,
                flags=flags + ["DateTime: missing creation/modification timestamps."],
                weight=weight,
            )

        now_local = datetime.now()  # noqa: DTZ005 - EXIF has no TZ; use local wall-clock
        future_margin = timedelta(minutes=10)
        for label, candidate_dt in (("modified", modified_dt), ("created", created_dt)):
            if candidate_dt is not None and candidate_dt > now_local + future_margin:
                return _FieldAssessment(
                    score=90.0,
                    flags=flags + [f"DateTime: {label} timestamp is in the future ({candidate_dt})."],
                    weight=weight,
                )

        if modified_dt is not None and created_dt is None:
            return _FieldAssessment(
                score=60.0,
                flags=flags + ["DateTime: modification timestamp exists but creation timestamp is missing."],
                weight=weight,
            )

        if modified_dt is None and created_dt is not None:
            return _FieldAssessment(
                score=35.0,
                flags=flags + ["DateTime: creation timestamp exists but modification timestamp is missing."],
                weight=weight,
            )

        assert modified_dt is not None and created_dt is not None
        delta = modified_dt - created_dt
        if abs(delta) > timedelta(days=1):
            return _FieldAssessment(
                score=70.0,
                flags=flags
                + [f"DateTime: modified more than 1 day from creation ({delta.days} day gap)."],
                weight=weight,
            )

        return _FieldAssessment(
            score=10.0,
            flags=flags + ["DateTime: timestamps are consistent."],
            weight=weight,
        )

    def _analyze_device_info(self, metadata: dict[str, str], *, file_extension: str) -> _FieldAssessment:
        """Validate Make/Model fields for expected scanner signatures. Weight: 20%."""

        weight = self.field_weights["device"]
        make_value = (metadata.get("Make") or "").strip()
        model_value = (metadata.get("Model") or "").strip()

        device_text = " ".join([make_value, model_value]).strip().lower()
        if device_text and any(scanner_brand in device_text for scanner_brand in self.scanner_brands):
            return _FieldAssessment(
                score=5.0,
                flags=[f"Device: legitimate scanner detected ({make_value} {model_value}).".strip()],
                weight=weight,
            )

        if file_extension in {".jpg", ".jpeg"} and (not make_value or not model_value):
            return _FieldAssessment(
                score=60.0,
                flags=["Device: Make/Model missing for JPEG input (often stripped by editors/exports)."],
                weight=weight,
            )

        if not make_value and not model_value:
            return _FieldAssessment(
                score=45.0,
                flags=["Device: missing Make/Model."],
                weight=weight,
            )

        return _FieldAssessment(
            score=15.0,
            flags=[f"Device: captured by '{make_value} {model_value}'.".strip()],
            weight=weight,
        )

    def _analyze_thumbnail_consistency(
        self,
        image_path: str,
        *,
        invoice_image: Image.Image | None = None,
    ) -> _FieldAssessment:
        """Basic embedded-thumbnail existence check. Weight: 15%."""

        weight = self.field_weights["thumbnail"]

        loaded_image: Image.Image | None = None
        try:
            if invoice_image is None:
                loaded_image = Image.open(image_path)
                invoice_image = loaded_image

            assert invoice_image is not None
            try:
                exif = invoice_image.getexif()
                thumbnail_bytes = exif.get_thumbnail() if hasattr(exif, "get_thumbnail") else None
            except Exception:
                thumbnail_bytes = None

            if thumbnail_bytes:
                return _FieldAssessment(
                    score=10.0,
                    flags=["Thumbnail: embedded thumbnail is present."],
                    weight=weight,
                )

            return _FieldAssessment(
                score=40.0,
                flags=["Thumbnail: no embedded thumbnail detected."],
                weight=weight,
            )
        except (OSError, UnidentifiedImageError) as open_error:
            return _FieldAssessment(
                score=50.0,
                flags=[f"Thumbnail: could not be checked ({open_error})."],
                weight=weight,
            )
        finally:
            if loaded_image is not None:
                try:
                    loaded_image.close()
                except Exception:
                    pass

