# agents/scene_generator.py
import json
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

client = Anthropic()

SCENE_PROMPT_SYSTEM = """You are a visual director for YouTube videos.
Given a video script, extract 6-8 distinct visual scenes.
For each scene, create:
1. A DALL-E 3 image prompt (vivid, cinematic, no text/letters in image)
2. A short motion description for video generation
3. A Pexels stock search query (search_query): 2–5 concrete English keywords, nouns only, no adjectives, no sentences.

STRICT RULES for search_query:
- Must contain 2–5 English keywords only (nouns, verbs if needed)
- NO adjectives like cinematic, dramatic, elegant, abstract, aerial, beautiful, stunning
- NO sentences or phrases — keywords only
- Must be concrete and searchable on Pexels stock video

GOOD search_query examples:
  "investment app smartphone"
  "person budgeting laptop"
  "ETF chart up"
  "euro banknotes counting"
  "snowball rolling downhill"

BAD search_query examples (do NOT do this):
  "cinematic aerial view city" — starts with banned adjective
  "dramatic close-up of hands counting money" — sentence with adjective
  "elegant financial visualization abstract" — multiple banned adjectives

Return ONLY valid JSON in this exact format:
{
  "scenes": [
    {
      "index": 1,
      "timestamp_hint": "0:00-0:30",
      "image_prompt": "Cinematic aerial view of Amsterdam canal houses at golden hour, warm light reflecting on water, ultra realistic, 4K",
      "motion_prompt": "Slow cinematic pan across the canal, gentle water ripples",
      "search_query": "canal houses water reflection",
      "pexels_query": "canal houses water reflection"
    }
  ]
}

Note: pexels_query must equal search_query (kept for backwards compatibility)."""


def generate_scene_prompts(script: str, title: str, num_scenes: int = 8) -> list[dict]:
    user_msg = f"""Video title: {title}

Script:
{script[:4000]}

Generate exactly {num_scenes} visual scenes for this video.
Respond with ONLY the raw JSON object. No markdown, no code blocks, no explanation."""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[
            {"role": "user", "content": f"{SCENE_PROMPT_SYSTEM}\n\n{user_msg}"},
        ],
        temperature=0.0,  # JSON output always 0
    )

    raw = response.content[0].text.strip()

    # Strip markdown code blocks if Claude sends them anyway
    if raw.startswith("```"):
        raw = raw[raw.index("\n")+1:]  # remove ```json line
        raw = raw[:raw.rfind("```")].strip()  # remove closing ```

    if not raw:
        raise ValueError(f"Empty response from Claude for job '{title}'")

    result = json.loads(raw)
    scenes = result.get("scenes", [])
    print(f"OK {len(scenes)} scenes generated for '{title}'")
    return scenes


def generate_scenes_for_job(video_job_id: str) -> list[dict]:
    from utils.supabase_client import get_client
    supabase = get_client()

    result = (
        supabase.table("video_jobs")
        .select("script, title_concept, niche, hook_data")
        .eq("id", video_job_id)
        .single()
        .execute()
    )
    job = result.data
    if not job or not job.get("script"):
        raise ValueError(f"No script for job {video_job_id}")

    scenes = generate_scene_prompts(job["script"], job["title_concept"])

    # Override scene 1 image_prompt with viral hook_visual when available
    hook_data = job.get("hook_data")
    if hook_data and scenes:
        if isinstance(hook_data, str):
            hook_data = json.loads(hook_data)
        hook_visual = hook_data.get("hook_visual", "")
        if hook_visual:
            scenes[0]["image_prompt"] = hook_visual
            print(f"OK hook_visual applied to scene 1 for job {video_job_id}")

    # Ensure pexels_query mirrors search_query for backwards compatibility
    for scene in scenes:
        sq = scene.get("search_query", "").strip()
        if sq:
            scene["pexels_query"] = sq
        elif scene.get("pexels_query"):
            scene["search_query"] = scene["pexels_query"]

    # Save scenes to DB
    supabase.table("video_jobs").update({
        "scene_prompts": json.dumps(scenes),
    }).eq("id", video_job_id).execute()

    print(f"OK Scenes saved for job {video_job_id}")
    return scenes
