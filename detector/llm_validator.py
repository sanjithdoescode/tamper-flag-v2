"""LLM-backed OCR semantic validation via n8n (v2.0).

This module NEVER talks to Ollama directly. It posts OCR text (plain text body)
to an n8n webhook, where the workflow calls the local LLM and returns a structured score.
"""

from __future__ import annotations

from typing import Any

import requests


def _clamp_0_to_100(candidate_score: float) -> float:
    return float(max(0.0, min(100.0, candidate_score)))


class LLMValidator:
    """
    Validate OCR text coherence using a local n8n → Ollama pipeline.

    sense_rating is a 0-100 coherence score:
    - 0-30: gibberish / clearly fake
    - 81-100: clear, coherent invoice-like structure
    """

    def __init__(self, n8n_webhook_url: str = "http://localhost:5678/webhook/validate-ocr") -> None:
        self.n8n_webhook_url = str(n8n_webhook_url)
        self.timeout_seconds = 15

    def validate_text(self, ocr_text: str) -> dict[str, Any]:
        """
        Send OCR text to n8n workflow for LLM validation.

        Returns:
        - sense_rating: 0-100 coherence score
        - reasoning: short explanation from LLM
        - is_coherent: sense_rating > 50
        - flags: human-friendly notes for reviewers
        - error: None or error string when n8n is unavailable
        """

        ocr_text = ocr_text or ""
        text_for_llm = ocr_text[:1000] if len(ocr_text) > 1000 else ocr_text
        sent_preview = text_for_llm[:200]

        if len(text_for_llm.strip()) < 50:
            return {
                "sense_rating": 20.0,
                "reasoning": "Insufficient text extracted",
                "is_coherent": False,
                "flags": ["Too little text to analyze (<50 chars)"],
                "sent_text_preview": sent_preview,
                "sent_text_length": len(text_for_llm),
                "error": None,
            }

        try:
            response = requests.post(
                self.n8n_webhook_url,
                data=text_for_llm,
                headers={"Content-Type": "text/plain; charset=utf-8"},
                timeout=self.timeout_seconds,
            )

            if response.status_code != 200:
                raise ValueError(f"n8n returned status {response.status_code}")

            try:
                payload = response.json()
            except ValueError:
                # n8n should always return JSON; capture raw body for diagnostics
                raw_body = response.text.strip()
                raise ValueError(
                    f"n8n returned non-JSON body (length={len(raw_body)}): {raw_body[:300] or '<empty>'}"
                ) from None
            raw_rating = payload.get("sense_rating", 50)
            try:
                sense_rating = _clamp_0_to_100(float(raw_rating))
            except (TypeError, ValueError):
                sense_rating = 50.0

            reasoning_text = str(payload.get("reasoning") or "No reasoning provided").strip()

            flags: list[str] = []
            if sense_rating < 40:
                flags.append(f"LLM flagged incoherent text: {reasoning_text}")
            elif sense_rating > 70:
                flags.append("LLM validated coherent invoice structure")
            else:
                flags.append("LLM uncertain - possible OCR errors or ambiguous text")

            return {
                "sense_rating": float(sense_rating),
                "reasoning": reasoning_text,
                "is_coherent": bool(sense_rating > 50),
                "flags": flags,
                "error": None,
                "sent_text_preview": sent_preview,
                "sent_text_length": len(text_for_llm),
            }
        except requests.exceptions.Timeout:
            return {
                "sense_rating": 50.0,
                "reasoning": "LLM validation timeout",
                "is_coherent": False,
                "flags": ["LLM validation timed out - using neutral score"],
                "error": f"Timeout after {self.timeout_seconds} seconds",
                "sent_text_preview": sent_preview,
                "sent_text_length": len(text_for_llm),
            }
        except Exception as request_error:  # noqa: BLE001 - API boundary
            return {
                "sense_rating": 50.0,
                "reasoning": f"LLM validation error: {request_error}",
                "is_coherent": False,
                "flags": ["LLM validation unavailable - using neutral score"],
                "error": str(request_error),
                "sent_text_preview": sent_preview,
                "sent_text_length": len(text_for_llm),
            }

    def test_connection(self) -> tuple[bool, str]:
        """
        Best-effort check that the n8n webhook endpoint is reachable.

        Uses POST (not GET) because the webhook node only accepts POST.
        Sends a small plain-text probe so n8n routing matches production.
        """

        probe_url = self.n8n_webhook_url
        try:
            response = requests.post(
                probe_url,
                data="healthcheck-probe",
                headers={"Content-Type": "text/plain; charset=utf-8"},
                timeout=5,
            )
            if response.status_code < 500:
                return True, f"n8n reachable ({response.status_code})"
            return False, f"n8n returned {response.status_code}"
        except Exception as connection_error:  # noqa: BLE001 - connectivity probe
            return False, f"n8n webhook not accessible: {connection_error}"


