# agents/seo_optimization.py
"""
SEO Optimization Agent
======================
1. Haal job op uit Supabase (title_concept, script[:2000], niche, keyword_targets).
2. Gebruik Claude Haiku om SEO-metadata te genereren:
   - seo_title:       max 70 tekens, pakkend, keyword-rijk
   - seo_description: max 5000 tekens, Nederlandstalig, CTA onderaan
   - seo_tags:        max 500 tekens totaal, kommagescheiden
3. Update video_jobs: seo_title, seo_description, seo_tags, status → SEO_OPTIMIZED.

Exporteert: generate_seo_for_job(video_job_id: str) -> dict
"""
import json

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

_anthropic = Anthropic()

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM = """Je bent een YouTube SEO-specialist voor Nederlandstalige financiële content.
Op basis van de videotitel, niche, script en doelzoekwoorden genereer je optimale metadata.

Regels:
- seo_title:       max 70 tekens, pakkend, bevat het hoofdzoekwoord
- seo_description: max 5000 tekens, Nederlandstalig, beschrijvend, eindig met een duidelijke CTA
- seo_tags:        kommagescheiden, max 500 tekens totaal, mix van breed en specifiek

Geef ALLEEN een geldig JSON-object terug in dit exacte formaat:
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
    kw_str = ", ".join(keywords) if keywords else "geen opgegeven"
    user_msg = (
        f"Videotitel: {title}\n"
        f"Niche: {niche or 'algemeen'}\n"
        f"Doelzoekwoorden: {kw_str}\n\n"
        f"Script (eerste 2000 tekens):\n{script_excerpt}\n\n"
        "Genereer de SEO-metadata.\n"
        "Stuur ALLEEN het ruwe JSON-object terug. Geen markdown, geen uitleg."
    )

    response = _anthropic.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        temperature=0.0,
        messages=[{"role": "user", "content": f"{_SYSTEM}\n\n{user_msg}"}],
    )

    raw = response.content[0].text.strip()

    # Strip markdown code blocks indien aanwezig
    if raw.startswith("```"):
        raw = raw[raw.index("\n") + 1:]
        raw = raw[:raw.rfind("```")].strip()

    if not raw:
        raise ValueError(f"Lege response van Claude Haiku voor '{title}'")

    return json.loads(raw)


def _validate(data: dict, title: str) -> dict:
    """Trim velden die te lang zijn en valideer aanwezigheid."""
    required = {"seo_title", "seo_description", "seo_tags"}
    missing = required - data.keys()
    if missing:
        raise ValueError(f"SEO JSON mist velden {missing} voor '{title}'")

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
    Genereer SEO-metadata voor de gegeven job en sla op in Supabase.

    Args:
        video_job_id: UUID van de video_jobs rij.

    Returns:
        Dict met seo_title, seo_description en seo_tags.

    Raises:
        ValueError: Als verplichte velden ontbreken of de response ongeldig is.
    """
    from utils.supabase_client import get_client
    supabase = get_client()

    # 1. Haal job op
    result = (
        supabase.table("video_jobs")
        .select("title_concept, script, niche, keyword_targets")
        .eq("id", video_job_id)
        .single()
        .execute()
    )
    job = result.data
    if not job:
        raise ValueError(f"Job {video_job_id} niet gevonden")

    title = job.get("title_concept") or ""
    niche = job.get("niche") or ""
    script_excerpt = (job.get("script") or "")[:2000]
    raw_kw = job.get("keyword_targets") or []
    keywords: list[str] = json.loads(raw_kw) if isinstance(raw_kw, str) else raw_kw

    print(f"SEO optimalisatie: '{title}'")

    # 2. Genereer met retry (max 2 pogingen)
    last_exc: Exception | None = None
    seo_data: dict | None = None
    for attempt in range(1, 3):
        try:
            raw_data = _generate_seo(title, niche, script_excerpt, keywords)
            seo_data = _validate(raw_data, title)
            break
        except Exception as exc:
            last_exc = exc
            print(f"Poging {attempt}/2 mislukt: {exc}")

    if seo_data is None:
        raise ValueError(f"SEO generatie mislukt na 2 pogingen: {last_exc}") from last_exc

    print(f"Titel ({len(seo_data['seo_title'])} tekens): {seo_data['seo_title']}")
    print(f"Tags ({len(seo_data['seo_tags'])} tekens): {seo_data['seo_tags'][:80]}...")

    # 3. Update DB
    supabase.table("video_jobs").update({
        "seo_title": seo_data["seo_title"],
        "seo_description": seo_data["seo_description"],
        "seo_tags": json.dumps(
            [t.strip() for t in seo_data["seo_tags"].split(",") if t.strip()]
        ),
        "status": "SEO_OPTIMIZED",
    }).eq("id", video_job_id).execute()

    print(f"Job {video_job_id} -> SEO_OPTIMIZED")
    return seo_data
