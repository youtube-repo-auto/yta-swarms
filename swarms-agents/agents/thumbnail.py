# agents/thumbnail.py
"""
Thumbnail Agent
===============
1. Haal job op uit Supabase (title_concept, script, scene_prompts, niche).
2. Gebruik Claude Haiku om de visueel sterkste scene te kiezen en een
   DALL-E 3 prompt te genereren (geen tekst/letters, cinematisch, 16:9).
3. Genereer thumbnail via OpenAI DALL-E 3 (1792x1024, hd).
4. Download PNG, upload naar Supabase Storage bucket "thumbnails" als {job_id}.png.
5. Update video_jobs: thumbnail_url, status → MEDIA_GENERATED.

Exporteert: generate_thumbnail_for_job(video_job_id: str) -> str
"""
import base64
import json
import os
import tempfile
import uuid
from pathlib import Path

import requests
from anthropic import Anthropic
from dotenv import load_dotenv

from utils.retry import retry_call

load_dotenv()

_anthropic = Anthropic()

_STABILITY_API_KEY = os.getenv("STABILITY_API_KEY")
_STABILITY_URL = (
    "https://api.stability.ai/v1/generation"
    "/stable-diffusion-xl-1024-v1-0/text-to-image"
)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM = """You are a YouTube thumbnail art director.
Given a video title, niche, script excerpt and scene list, choose the most
visually powerful scene and craft a DALL-E 3 image generation prompt for it.

Rules for the prompt:
- NO text, letters, numbers, or words anywhere in the image
- Cinematic composition, high contrast, vivid colours
- 16:9 landscape orientation, photorealistic or hyper-realistic style
- Must immediately convey the video's core emotion or concept
- Mention lighting, camera angle, and mood explicitly

Return ONLY valid JSON in this exact format:
{
  "dalle_prompt": "...",
  "concept_rationale": "..."
}"""


# ---------------------------------------------------------------------------
# Step 1 – Claude Haiku generates DALL-E 3 prompt
# ---------------------------------------------------------------------------

def _build_dalle_prompt(title: str, niche: str, script_excerpt: str, scenes: list[dict]) -> dict:
    """Call Claude Haiku to select best scene and craft a DALL-E 3 prompt."""
    scene_list = "\n".join(
        f"  [{s['index']}] {s.get('image_prompt', s.get('motion_prompt', ''))[:120]}"
        for s in scenes
    )
    user_msg = (
        f"Video title: {title}\n"
        f"Niche: {niche or 'general'}\n\n"
        f"Script excerpt:\n{script_excerpt}\n\n"
        f"Available scenes:\n{scene_list}\n\n"
        "Generate the DALL-E 3 thumbnail prompt.\n"
        "Respond with ONLY the raw JSON object. No markdown, no code blocks."
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

    # Strip markdown code blocks indien aanwezig
    if raw.startswith("```"):
        raw = raw[raw.index("\n") + 1:]
        raw = raw[:raw.rfind("```")].strip()

    if not raw:
        raise ValueError(f"Lege response van Claude Haiku voor '{title}'")

    return json.loads(raw)


# ---------------------------------------------------------------------------
# Step 2 – Stability AI generates image
# ---------------------------------------------------------------------------

def _generate_dalle_image(dalle_prompt: str) -> str:
    """Call Stability AI SDXL, decode base64 PNG, save to temp file, return local path."""
    if not _STABILITY_API_KEY:
        raise EnvironmentError("STABILITY_API_KEY is not set in environment.")

    def _call() -> str:
        resp = requests.post(
            _STABILITY_URL,
            headers={
                "Authorization": f"Bearer {_STABILITY_API_KEY}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={
                "text_prompts": [{"text": dalle_prompt, "weight": 1.0}],
                "width": 1344,
                "height": 768,
                "cfg_scale": 7,
                "steps": 30,
                "samples": 1,
            },
            timeout=120,
        )
        resp.raise_for_status()
        artifact = resp.json()["artifacts"][0]
        if artifact.get("finishReason") not in ("SUCCESS", "success"):
            raise ValueError(f"Stability AI finishReason: {artifact.get('finishReason')}")
        img_bytes = base64.b64decode(artifact["base64"])
        local_path = os.path.join(
            tempfile.mkdtemp(prefix="stab_"), f"thumbnail_{uuid.uuid4().hex}.png"
        )
        with open(local_path, "wb") as f:
            f.write(img_bytes)
        size_kb = len(img_bytes) // 1024
        print(f"Stability AI afbeelding gegenereerd: {size_kb} KB")
        return local_path

    return retry_call(_call, max_attempts=2, base_delay=3.0, exceptions=(Exception,))


# ---------------------------------------------------------------------------
# Step 3 – Download + upload to Supabase
# ---------------------------------------------------------------------------

def _download_image(url: str, tmpdir: str) -> str:
    """Download image from URL to a temp PNG file."""
    local_path = os.path.join(tmpdir, f"thumbnail_{uuid.uuid4().hex}.png")
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    with open(local_path, "wb") as f:
        f.write(resp.content)
    size_kb = Path(local_path).stat().st_size // 1024
    print(f"Thumbnail gedownload: {size_kb} KB")
    return local_path


def _upload_thumbnail(local_path: str, storage_path: str) -> str:
    """Upload PNG naar Supabase Storage bucket 'thumbnails', geeft public URL terug."""
    from utils.supabase_client import get_client
    supabase = get_client()

    with open(local_path, "rb") as f:
        data = f.read()

    try:
        supabase.storage.from_("thumbnails").remove([storage_path])
    except Exception:
        pass

    supabase.storage.from_("thumbnails").upload(
        path=storage_path,
        file=data,
        file_options={"content-type": "image/png"},
    )

    return supabase.storage.from_("thumbnails").get_public_url(storage_path)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def generate_thumbnail_for_job(video_job_id: str) -> str:
    """
    Genereer en upload een YouTube thumbnail voor de gegeven job.

    Args:
        video_job_id: UUID van de video_jobs rij.

    Returns:
        Publieke URL van de thumbnail in Supabase Storage.
    """
    from utils.supabase_client import get_client
    supabase = get_client()

    # 1. Haal job op
    result = (
        supabase.table("video_jobs")
        .select("title_concept, script, scene_prompts, niche")
        .eq("id", video_job_id)
        .single()
        .execute()
    )
    job = result.data
    if not job:
        raise ValueError(f"Job {video_job_id} niet gevonden")
    if not job.get("scene_prompts"):
        raise ValueError(f"Geen scene_prompts voor job {video_job_id}")

    raw_scenes = job["scene_prompts"]
    scenes: list[dict] = json.loads(raw_scenes) if isinstance(raw_scenes, str) else raw_scenes
    scenes = sorted(scenes, key=lambda s: s.get("index", 0))

    script_excerpt = (job.get("script") or "")[:1000]
    print(f"Thumbnail generatie: '{job['title_concept']}' — {len(scenes)} scenes beschikbaar")

    local_png: str | None = None
    try:
        # 2. Claude Haiku → image prompt
        prompt_data = _build_dalle_prompt(
            title=job["title_concept"],
            niche=job.get("niche") or "",
            script_excerpt=script_excerpt,
            scenes=scenes,
        )
        dalle_prompt = prompt_data["dalle_prompt"]
        rationale = prompt_data.get("concept_rationale", "")
        print(f"Prompt gegenereerd: {dalle_prompt[:80]}...")
        print(f"Rationale: {rationale[:80]}")

        # 3. Stability AI → local PNG file
        local_png = _generate_dalle_image(dalle_prompt)
        print(f"Afbeelding opgeslagen: {local_png}")

        # 4. Upload naar Supabase
        storage_path = f"{video_job_id}.png"
        thumbnail_url = _upload_thumbnail(local_png, storage_path)
        print(f"Geüpload: {thumbnail_url}")

        # 5. Update DB
        supabase.table("video_jobs").update({
            "thumbnail_url": thumbnail_url,
            "status": "MEDIA_GENERATED",
        }).eq("id", video_job_id).execute()

        print(f"Job {video_job_id} -> MEDIA_GENERATED")
        return thumbnail_url

    finally:
        # Cleanup temp PNG and its parent dir
        if local_png:
            p = Path(local_png)
            p.unlink(missing_ok=True)
            try:
                p.parent.rmdir()
            except OSError:
                pass
