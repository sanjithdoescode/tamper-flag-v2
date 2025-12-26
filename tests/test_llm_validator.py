"""LLM validator unit tests (n8n webhook integration)."""

from __future__ import annotations

from unittest.mock import Mock, patch

import requests

from detector.llm_validator import LLMValidator


def test_marks_too_little_text_as_incoherent() -> None:
    llm_validator = LLMValidator("http://localhost:5678/webhook/validate-ocr")

    validation = llm_validator.validate_text("short text")
    assert validation["sense_rating"] == 20.0
    assert validation["is_coherent"] is False
    assert validation["error"] is None


def test_returns_neutral_score_when_n8n_times_out() -> None:
    llm_validator = LLMValidator("http://localhost:5678/webhook/validate-ocr")

    with patch("detector.llm_validator.requests.post", side_effect=requests.exceptions.Timeout):
        validation = llm_validator.validate_text("This is long enough to trigger a webhook call " * 3)

    assert validation["sense_rating"] == 50.0
    assert validation["error"]
    assert "timed out" in " ".join(validation["flags"]).lower()


def test_parses_n8n_success_response_and_sets_coherent_flag() -> None:
    llm_validator = LLMValidator("http://localhost:5678/webhook/validate-ocr")

    fake_response = Mock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"sense_rating": 82, "reasoning": "Looks like a real invoice layout."}

    with patch("detector.llm_validator.requests.post", return_value=fake_response):
        validation = llm_validator.validate_text("This looks like a real invoice with vendor, date, total, and tax." * 2)

    assert validation["sense_rating"] == 82.0
    assert validation["is_coherent"] is True
    assert "coherent" in " ".join(validation["flags"]).lower()


def test_handles_non_200_n8n_response_as_unavailable() -> None:
    llm_validator = LLMValidator("http://localhost:5678/webhook/validate-ocr")

    fake_response = Mock()
    fake_response.status_code = 500

    with patch("detector.llm_validator.requests.post", return_value=fake_response):
        validation = llm_validator.validate_text("This is long enough to trigger a webhook call " * 3)

    assert validation["sense_rating"] == 50.0
    assert validation["error"]


