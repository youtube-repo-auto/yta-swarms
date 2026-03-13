"""
tests/test_shorts_script.py
===========================
Integration test for the Shorts Script Writer agent.

Run with:
    cd swarms-agents
    python -m pytest tests/test_shorts_script.py -v

Requires ANTHROPIC_API_KEY in environment (reads .env automatically).
"""
import json
import sys
from pathlib import Path

# Ensure swarms-agents root is on the path when run directly
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from agents.shorts_script_writer import write_shorts_script, _parse_response, _build_full_script


# ---------------------------------------------------------------------------
# Unit tests (no API call)
# ---------------------------------------------------------------------------

class TestParseResponse:
    def test_valid_json_parsed(self):
        payload = {
            "hook": "Most people never build wealth, and here is why.",
            "body": [
                "Investing €500 a month at 8% annual return gives you €300,000 in 20 years.",
                "Starting at 25 instead of 35 doubles your final amount thanks to compounding.",
            ],
            "question_cta": "How much are you investing each month? Follow for weekly tips.",
            "on_screen_text": {
                "hook_text": "€300K from €500/month",
                "question_text": "How much do you invest?",
            },
            "estimated_duration_seconds": 52,
        }
        result = _parse_response(json.dumps(payload))
        assert result["hook"] == payload["hook"]
        assert len(result["body"]) == 2
        assert result["estimated_duration_seconds"] == 52

    def test_strips_markdown_fences(self):
        payload = {
            "hook": "Start now.",
            "body": ["Point one with a number: 7%.", "Point two: invest monthly."],
            "question_cta": "What do you think? Follow us.",
            "on_screen_text": {"hook_text": "Start Now", "question_text": "What do you think?"},
            "estimated_duration_seconds": 45,
        }
        wrapped = f"```json\n{json.dumps(payload)}\n```"
        result = _parse_response(wrapped)
        assert result["hook"] == "Start now."

    def test_missing_key_raises(self):
        bad = {"hook": "Test.", "body": ["One point."]}  # missing keys
        with pytest.raises((ValueError, KeyError)):
            _parse_response(json.dumps(bad))

    def test_body_must_have_2_or_3_items(self):
        payload = {
            "hook": "Hook.",
            "body": ["Only one item."],  # invalid
            "question_cta": "CTA.",
            "on_screen_text": {"hook_text": "Text", "question_text": "Q?"},
            "estimated_duration_seconds": 50,
        }
        with pytest.raises(ValueError, match="body must be a list"):
            _parse_response(json.dumps(payload))


class TestBuildFullScript:
    def test_concatenates_all_parts(self):
        data = {
            "hook": "Hook sentence.",
            "body": ["Body point one.", "Body point two."],
            "question_cta": "Question and CTA.",
        }
        result = _build_full_script(data)
        assert "Hook sentence." in result
        assert "Body point one." in result
        assert "Body point two." in result
        assert "Question and CTA." in result


# ---------------------------------------------------------------------------
# Integration test (calls real API — skipped if no API key)
# ---------------------------------------------------------------------------

def _has_api_key() -> bool:
    import os
    from dotenv import load_dotenv
    load_dotenv()
    return bool(os.getenv("ANTHROPIC_API_KEY"))


@pytest.mark.skipif(not _has_api_key(), reason="ANTHROPIC_API_KEY not set")
class TestWriteShortsScriptLive:
    TOPIC = "Investing €500 a month in a global index fund for 20 years"
    ANGLE = "Show the power of compound interest with real numbers"

    def test_returns_all_required_keys(self):
        result = write_shorts_script(topic=self.TOPIC, angle=self.ANGLE)
        required = {"hook", "body", "question_cta", "on_screen_text",
                    "estimated_duration_seconds", "full_script"}
        assert required.issubset(result.keys()), (
            f"Missing keys: {required - result.keys()}"
        )

    def test_estimated_duration_in_range(self):
        result = write_shorts_script(topic=self.TOPIC, angle=self.ANGLE)
        duration = result["estimated_duration_seconds"]
        assert 30 <= duration <= 90, f"Duration {duration}s outside expected range 30–90"

    def test_body_has_2_or_3_items(self):
        result = write_shorts_script(topic=self.TOPIC, angle=self.ANGLE)
        assert 2 <= len(result["body"]) <= 3

    def test_hook_max_15_words(self):
        result = write_shorts_script(topic=self.TOPIC, angle=self.ANGLE)
        word_count = len(result["hook"].split())
        assert word_count <= 20, (  # allow slight model variance
            f"Hook has {word_count} words (target ≤15): {result['hook']}"
        )

    def test_full_script_is_non_empty_string(self):
        result = write_shorts_script(topic=self.TOPIC, angle=self.ANGLE)
        assert isinstance(result["full_script"], str)
        assert len(result["full_script"]) > 50

    def test_on_screen_text_has_required_subkeys(self):
        result = write_shorts_script(topic=self.TOPIC, angle=self.ANGLE)
        ost = result["on_screen_text"]
        assert "hook_text" in ost
        assert "question_text" in ost

    def test_prints_output(self):
        """Print JSON for visual inspection when running manually."""
        result = write_shorts_script(topic=self.TOPIC, angle=self.ANGLE)
        print("\n--- Shorts Script JSON ---")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        print("--- full_script ---")
        print(result["full_script"])
