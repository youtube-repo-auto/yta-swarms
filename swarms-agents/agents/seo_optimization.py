# agents/seo_optimization.py
"""
SEO Optimization Agent
======================
1. Fetch job from Supabase (title_concept, script[:2000], niche, keyword_targets).
2. Use Claude Haiku to generate SEO metadata:
   - seo_title:       max 70 characters, compelling, keyword-rich
   - seo_description: max 5000 characters, English, CTA at the end
   - seo_tags:        max 500 characters total, comma-separated
3. Update video_jobs: seo_title, seo_description, seo_tags, status → SEO_OPTIMIZED.

Exports: generate_seo_for_job(video_job_id: str) -> dict
"""
import json
import logging

from anthropic import Anthropic
from dotenv import load_dotenv

from utils.retry import retry_call

load_dotenv()

logger = logging.getLogger(__name__)
_anthropic = Anthropic()

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM = """You are a YouTube SEO specialist for English-language finance and investment content.
Based on the video title, niche, script, and target keywords, generate optimal metadata.
All metadata (title, description, tags) must be in English.

Rules:
- seo_title:       max 70 characters, compelling, contains the primary keyword
- seo_description: max 5000 characters, English, descriptive, end with a clear CTA
- seo_tags:        comma-separated, max 500 characters total, mix of broad and specific

Return ONLY a valid JSON object in this exact format:
{
  "seo_title": "...",
  "seo_description": "...",
  "seo_tags": "tag1, tag2, tag3"
}"""


# ---------------------------------------------------------------------------
# Core generation
# ---------------------------------------------------------------------------

def _generate_seo(title: str, niche: str, script_excerpt: str, keywords: list[str]) -> dict:
    """Call Claude Haiku to generate SEO metadata. Returns parsed dict."""
    kw_str = ", ".join(keywords) if keywords else "none provided"
    user_msg = (
        f"Video title: {title}\n"
        f"Niche: {niche or 'general'}\n"
        f"Target keywords: {kw_str}\n\n"
        f"Script (first 2000 characters):\n{script_excerpt}\n\n"
        "Generate the SEO metadata.\n"
        "Return ONLY the raw JSON object. No markdown, no explanation."
    )

    def _call() -> str:
        response = _anthropic.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            temperature=0.0,
            messages=[{"role": "user", "content": f"{_SYSTEM}\n\n{user_msg}"}],
        )
        return response.content[0].text.strip()

    raw = retry_call(_call, max_attempts=2, base_delay=2.0, exceptions=(Exception,))

    # Strip markdown code blocks if present
    if raw.startswith("```"):
        raw = raw[raw.index("\n") + 1:]
        raw = raw[:raw.rfind("```")].strip()

    if not raw:
        raise ValueError(f"Empty response from Claude Haiku for '{title}'")

    return json.loads(raw)


def _validate(data: dict, title: str) -> dict:
    """Trim fields that are too long and validate presence."""
    required = {"seo_title", "seo_description", "seo_tags"}
    missing = required - data.keys()
    if missing:
        raise ValueError(f"SEO JSON missing fields {missing} for '{title}'")

    if len(data["seo_title"]) > 70:
        data["seo_title"] = data["seo_title"][:70].rstrip()

    if len(data["seo_description"]) > 5000:
        data["seo_description"] = data["seo_description"][:5000].rstrip()

    if len(data["seo_tags"]) > 500:
        # Trim whole tags, never mid-tag
        tags = [t.strip() for t in data["seo_tags"].split(",")]
        trimmed, total = [], 0
        for tag in tags:
            if total + len(tag) + 2 > 500:
                break
            trimmed.append(tag)
            total += len(tag) + 2
        data["seo_tags"] = ", ".join(trimmed)

    return data


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def generate_seo_for_job(video_job_id: str) -> dict:
    """
    Generate SEO metadata for the given job and save to Supabase.

    Args:
        video_job_id: UUID of the video_jobs row.

    Returns:
        Dict with seo_title, seo_description, and seo_tags.

    Raises:
        ValueError: If required fields are missing or the response is invalid.
    """
    from utils.supabase_client import get_client
    supabase = get_client()

    # 1. Fetch job
    result = (
        supabase.table("video_jobs")
        .select("title_concept, script, niche, keyword_targets")
        .eq("id", video_job_id)
        .single()
        .execute()
    )
    job = result.data
    if not job:
        raise ValueError(f"Job {video_job_id} not found")

    title = job.get("title_concept") or ""
    niche = job.get("niche") or ""
    script_excerpt = (job.get("script") or "")[:2000]
    raw_kw = job.get("keyword_targets") or []
    keywords: list[str] = json.loads(raw_kw) if isinstance(raw_kw, str) else raw_kw

    print(f"SEO optimization: '{title}'")

    # 2. Generate and validate (retry is in _generate_seo via retry_call)
    raw_data = _generate_seo(title, niche, script_excerpt, keywords)
    seo_data = _validate(raw_data, title)

    print(f"Title ({len(seo_data['seo_title'])} chars): {seo_data['seo_title']}")
    print(f"Tags ({len(seo_data['seo_tags'])} chars): {seo_data['seo_tags'][:80]}...")

    # 3. Update DB

    supabase.table("video_jobs").update({
        "seo_title": seo_data["seo_title"],
        "seo_description": seo_data["seo_description"],
        "seo_tags": seo_data["seo_tags"],
        "status": "SEO_OPTIMIZED",
    }).eq("id", video_job_id).execute()

    print(f"Job {video_job_id} -> SEO_OPTIMIZED")
    return seo_data
