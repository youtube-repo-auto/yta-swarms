# CLAUDE.md — yta-swarms/swarms-agents

## Stack
- Python 3.11, venv: C:\Users\MikeDonker\Projects\yta-swarms\venv
- Supabase: oixvgvsytuqpihnjbmwq.supabase.co
- LLM: Anthropic Claude via utils/llm_factory.py (NO OpenAI)
- Video: LTX-Video 0.9.1 via ltx_infer.py (CUDA)
- TTS: ElevenLabs via agents/voice_generation.py
- Images: Stability AI SDXL via agents/thumbnail.py
- YouTube: google-api-python-client via agents/publishing.py

## Pipeline (status flow)
IDEA → RESEARCHED → SCRIPTED → VOICE_GENERATED → VIDEO_GENERATED → MEDIA_GENERATED → SEO_OPTIMIZED → PUBLISHED

Scene generator sets scene_prompts (no status change) between SCRIPTED and VOICE_GENERATED.

## Agents (all complete)
- agents/research.py → run_research(job_id)
- agents/script_writer.py → run_scriptwriting(job_id)
- agents/scene_generator.py → generate_scenes_for_job(job_id)
- agents/voice_generation.py → generate_voice_for_job(job_id)
- agents/video_generation.py → generate_video_for_job(job_id)
- agents/thumbnail.py → generate_thumbnail_for_job(job_id)
- agents/seo_optimization.py → generate_seo_for_job(job_id)
- agents/publishing.py → publish_job(job_id)
- agents/pipeline.py → run_pipeline(job_id)

## Utils
- utils/supabase_client.py → get_client()
- utils/llm_factory.py → get_llm(model, temperature, max_tokens)
- utils/retry.py → retry_call(fn, max_attempts)
- utils/scheduler.py → start_scheduler(), run_once() [IN PROGRESS]

## LLM pattern (ALL agents use this)
```python
from utils.llm_factory import get_llm
llm = get_llm("claude-haiku-4-5-20251001", temperature=0.0, max_tokens=4096)
response = llm.invoke(prompt)
result = response.content  # strip markdown if needed
```

## DB columns (video_jobs key fields)
id, status, title_concept, niche, script, scene_prompts,
voice_url, video_url, thumbnail_url,
seo_title, seo_description, seo_tags,
youtube_video_id, youtube_url, error_message

## Rules
- NEVER use OpenAI SDK
- ALWAYS use retry_call for external API calls
- ALWAYS set status in DB after each agent completes
- Log with logger = logging.getLogger(__name__), not print()
- Temp files: use tempfile.mkdtemp(), cleanup in finally
```
