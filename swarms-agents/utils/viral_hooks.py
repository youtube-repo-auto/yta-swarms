"""
utils/viral_hooks.py – Viral Shorts Hooks generator (eerste 3 sec, 90% retention).

Genereert 3 hook-varianten via Claude voor een gegeven title_concept + niche.
Elke variant is TTS-ready en eindigt met een cliffhanger.
"""

import json
import logging
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic()
    return _client


HOOK_SYSTEM = """You are a viral YouTube Shorts strategist specialised in financial content.
Your task: generate exactly 3 viral hook variants for the FIRST 3 SECONDS of a Short.

Rules:
- Each hook MUST be speakable in under 4 seconds (max ~10 words)
- Each hook MUST end with a hard cliffhanger (no full stop, use '...' or '?')
- Each hook targets 90%+ audience retention in the first 3 seconds
- Language matches the niche locale (Dutch for snowball_wealth)
- hook_visual describes ONE concrete image/shot for scene 1 (no text on screen)
- retention_score is your honest estimate between 0.80 and 0.99

Return ONLY valid JSON — no markdown, no explanation:
[
  {
    "hook_text": "...",
    "hook_visual": "...",
    "retention_score": 0.00,
    "formula": "shock_stat|myth_bust|personal_story|pain_question|future_projection"
  }
]"""

_FORMULA_EXAMPLES = {
    "snowball_wealth": [
        "shock_stat: '€500/maand → €1M in 30 jaar? Wacht...'",
        "myth_bust: 'Iedereen zegt koop huizen, maar...'",
        "personal_story: 'Mijn eerste €1000 ging ZO...'",
        "pain_question: 'Ben jij ook bang voor de volgende crash?'",
        "future_projection: '€1000 nu = €50k in 2030...'",
    ]
}


def generate_viral_hook(
    title_concept: str,
    niche: str = "snowball_wealth",
) -> dict:
    """
    Genereert 3 hook-varianten en retourneert de best scorende.

    Args:
        title_concept: Video title / concept string.
        niche:         Content niche (default: snowball_wealth).

    Returns:
        dict with keys: hook_text, hook_visual, estimated_retention, all_variants
    """
    examples = _FORMULA_EXAMPLES.get(niche, [])
    examples_str = "\n".join(f"  - {e}" for e in examples) if examples else ""

    user_msg = (
        f"Title concept: {title_concept}\n"
        f"Niche: {niche}\n"
    )
    if examples_str:
        user_msg += f"\nFormula examples for this niche:\n{examples_str}\n"
    user_msg += "\nGenerate exactly 3 hook variants. Return raw JSON array only."

    from utils.retry import retry_call

    raw = retry_call(
        _call_claude,
        user_msg,
        max_attempts=3,
        base_delay=2.0,
    )

    variants = _parse_variants(raw)
    best = max(variants, key=lambda v: v.get("retention_score", 0.0))

    logger.info(
        "viral_hooks: best hook for '%s' (score=%.2f): %s",
        title_concept,
        best.get("retention_score", 0.0),
        best.get("hook_text", ""),
    )

    return {
        "hook_text": best["hook_text"],
        "hook_visual": best["hook_visual"],
        "estimated_retention": best["retention_score"],
        "all_variants": variants,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _call_claude(user_msg: str) -> str:
    response = _get_client().messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        temperature=0.9,  # creative variance across 3 variants
        messages=[
            {"role": "user", "content": f"{HOOK_SYSTEM}\n\n{user_msg}"},
        ],
    )
    return response.content[0].text.strip()


def _parse_variants(raw: str) -> list[dict]:
    """Parse Claude's JSON response, strip markdown fences if present."""
    text = raw
    if text.startswith("```"):
        text = text[text.index("\n") + 1:]
        text = text[: text.rfind("```")].strip()

    data = json.loads(text)
    if not isinstance(data, list) or not data:
        raise ValueError(f"Expected JSON array of hooks, got: {text[:200]}")

    required = {"hook_text", "hook_visual", "retention_score"}
    for item in data:
        missing = required - item.keys()
        if missing:
            raise ValueError(f"Hook variant missing fields {missing}: {item}")

    return data
