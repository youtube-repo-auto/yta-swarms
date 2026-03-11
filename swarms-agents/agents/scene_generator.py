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

Return ONLY valid JSON in this exact format:
{
  "scenes": [
    {
      "index": 1,
      "timestamp_hint": "0:00-0:30",
      "image_prompt": "Cinematic aerial view of Amsterdam canal houses at golden hour, warm light reflecting on water, ultra realistic, 4K",
      "motion_prompt": "Slow cinematic pan across the canal, gentle water ripples"
    }
  ]
}"""


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
        temperature=0.0,  # JSON output altijd op 0
    )

    raw = response.content[0].text.strip()

    # Strip markdown code blocks als Claude die toch stuurt
    if raw.startswith("```"):
        raw = raw[raw.index("\n")+1:]  # verwijder ```json regel
        raw = raw[:raw.rfind("```")].strip()  # verwijder sluitende ```

    if not raw:
        raise ValueError(f"Lege response van Claude voor job '{title}'")

    result = json.loads(raw)
    scenes = result.get("scenes", [])
    print(f"✓ {len(scenes)} scènes gegenereerd voor '{title}'")
    return scenes


def generate_scenes_for_job(video_job_id: str) -> list[dict]:
    from utils.supabase_client import get_client
    supabase = get_client()

    result = (
        supabase.table("video_jobs")
        .select("script, title_concept")
        .eq("id", video_job_id)
        .single()
        .execute()
    )
    job = result.data
    if not job or not job.get("script"):
        raise ValueError(f"Geen script voor job {video_job_id}")

    scenes = generate_scene_prompts(job["script"], job["title_concept"])

    # Sla scenes op in DB
    supabase.table("video_jobs").update({
        "scene_prompts": json.dumps(scenes),
    }).eq("id", video_job_id).execute()

    print(f"✓ Scenes opgeslagen voor job {video_job_id}")
    return scenes
