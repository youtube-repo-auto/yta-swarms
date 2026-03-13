# agents/pipeline.py
"""
Pipeline Orchestrator
=====================
Runs a single video job end-to-end by calling each agent in order,
skipping steps whose prerequisite status has already been reached.

Step order:
  IDEA          -> research        -> RESEARCHED
  RESEARCHED    -> script_writer   -> SCRIPTED
  SCRIPTED      -> scene_generator -> scene_prompts populated (no status change)
  SCRIPTED      -> voice_generation -> VOICE_GENERATED
  VOICE_GENERATED -> video_generation -> VIDEO_GENERATED
  VIDEO_GENERATED -> thumbnail     -> MEDIA_GENERATED
  MEDIA_GENERATED -> seo_optimization -> SEO_OPTIMIZED
  SEO_OPTIMIZED -> publishing      -> PUBLISHED

Exports: run_pipeline(video_job_id: str) -> dict
"""
import logging

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# Status order — used to determine which steps to skip
_STATUS_ORDER = [
    "IDEA",
    "RESEARCHED",
    "SCRIPTED",
    "SCRIPT_APPROVED",   # manual gate; voice_generation accepts SCRIPTED
    "VOICE_GENERATED",
    "VIDEO_GENERATED",
    "MEDIA_GENERATED",
    "SEO_OPTIMIZED",
    "PUBLISHED",
]


def _status_index(status: str) -> int:
    try:
        return _STATUS_ORDER.index(status)
    except ValueError:
        return -1


def _fetch_status(video_job_id: str) -> str:
    from utils.supabase_client import get_client
    resp = (
        get_client()
        .table("video_jobs")
        .select("status")
        .eq("id", video_job_id)
        .single()
        .execute()
    )
    if not resp.data:
        raise ValueError(f"Job {video_job_id} niet gevonden")
    return resp.data["status"]


def _fetch_field(video_job_id: str, field: str):
    """Fetch a single field from video_jobs. Returns None if missing."""
    from utils.supabase_client import get_client
    resp = (
        get_client()
        .table("video_jobs")
        .select(field)
        .eq("id", video_job_id)
        .single()
        .execute()
    )
    return resp.data.get(field) if resp.data else None


def _run_step(name: str, fn, *args, **kwargs):
    """Call fn(*args, **kwargs), logging start/end. Re-raises on failure."""
    logger.info("[pipeline] START %s", name)
    print(f"\n{'='*60}\n[pipeline] START {name}\n{'='*60}")
    result = fn(*args, **kwargs)
    logger.info("[pipeline] DONE  %s", name)
    print(f"[pipeline] DONE  {name}")
    return result


def run_pipeline(video_job_id: str) -> dict:
    """
    Run all pending pipeline steps for a single job.

    Checks current status before each step and skips steps that are
    already complete. Stops immediately on any agent failure.

    Args:
        video_job_id: UUID of the video_jobs row.

    Returns:
        dict with 'status' and 'youtube_url' (None if not yet published).

    Raises:
        ValueError:   If job not found.
        RuntimeError: If any agent step fails (status details in message).
    """
    print(f"\n[pipeline] Starting pipeline for job {video_job_id}")

    # ------------------------------------------------------------------ #
    # Step 1: Research  (IDEA → RESEARCHED)
    # ------------------------------------------------------------------ #
    status = _fetch_status(video_job_id)
    if _status_index(status) < _status_index("RESEARCHED"):
        try:
            from agents.research import run_research
            _run_step("research", run_research, job_id=video_job_id)
        except Exception as exc:
            raise RuntimeError(f"[research] mislukt: {exc}") from exc
    else:
        print(f"[pipeline] SKIP  research (status={status})")

    # ------------------------------------------------------------------ #
    # Step 2: Script writing  (RESEARCHED → SCRIPTED)
    # ------------------------------------------------------------------ #
    status = _fetch_status(video_job_id)
    if _status_index(status) < _status_index("SCRIPTED"):
        try:
            from agents.scriptwriting import run_scriptwriting
            _run_step("script_writer", run_scriptwriting, job_id=video_job_id)
        except Exception as exc:
            raise RuntimeError(f"[script_writer] mislukt: {exc}") from exc
    else:
        print(f"[pipeline] SKIP  script_writer (status={status})")

    # ------------------------------------------------------------------ #
    # Step 3: Scene generator  (SCRIPTED → scene_prompts populated)
    # scene_generator sets no status; skip if scene_prompts already present
    # ------------------------------------------------------------------ #
    if not _fetch_field(video_job_id, "scene_prompts"):
        try:
            from agents.scene_generator import generate_scenes_for_job
            _run_step("scene_generator", generate_scenes_for_job, video_job_id)
        except Exception as exc:
            raise RuntimeError(f"[scene_generator] mislukt: {exc}") from exc
    else:
        print("[pipeline] SKIP  scene_generator (scene_prompts already set)")

    # ------------------------------------------------------------------ #
    # Step 4: Voice generation  (SCRIPTED → VOICE_GENERATED)
    # ------------------------------------------------------------------ #
    status = _fetch_status(video_job_id)
    if _status_index(status) < _status_index("VOICE_GENERATED"):
        try:
            from agents.voice_generation import generate_voice_for_job
            _run_step("voice_generation", generate_voice_for_job, video_job_id)
        except Exception as exc:
            raise RuntimeError(f"[voice_generation] mislukt: {exc}") from exc
    else:
        print(f"[pipeline] SKIP  voice_generation (status={status})")

    # ------------------------------------------------------------------ #
    # Step 5: Video generation  (VOICE_GENERATED → VIDEO_GENERATED)
    # ------------------------------------------------------------------ #
    status = _fetch_status(video_job_id)
    if _status_index(status) < _status_index("VIDEO_GENERATED"):
        try:
            from agents.video_generation import generate_video_for_job
            _run_step("video_generation", generate_video_for_job, video_job_id)
        except Exception as exc:
            raise RuntimeError(f"[video_generation] mislukt: {exc}") from exc
    else:
        print(f"[pipeline] SKIP  video_generation (status={status})")

    # ------------------------------------------------------------------ #
    # Step 6: Thumbnail  (VIDEO_GENERATED → MEDIA_GENERATED)
    # ------------------------------------------------------------------ #
    status = _fetch_status(video_job_id)
    if _status_index(status) < _status_index("MEDIA_GENERATED"):
        try:
            from agents.thumbnail import generate_thumbnail_for_job
            _run_step("thumbnail", generate_thumbnail_for_job, video_job_id)
        except Exception as exc:
            raise RuntimeError(f"[thumbnail] mislukt: {exc}") from exc
    else:
        print(f"[pipeline] SKIP  thumbnail (status={status})")

    # ------------------------------------------------------------------ #
    # Step 7: SEO optimization  (MEDIA_GENERATED → SEO_OPTIMIZED)
    # ------------------------------------------------------------------ #
    status = _fetch_status(video_job_id)
    if _status_index(status) < _status_index("SEO_OPTIMIZED"):
        try:
            from agents.seo_optimization import generate_seo_for_job
            _run_step("seo_optimization", generate_seo_for_job, video_job_id)
        except Exception as exc:
            raise RuntimeError(f"[seo_optimization] mislukt: {exc}") from exc
    else:
        print(f"[pipeline] SKIP  seo_optimization (status={status})")

    # ------------------------------------------------------------------ #
    # Step 8: Publishing  (SEO_OPTIMIZED → PUBLISHED)
    # ------------------------------------------------------------------ #
    status = _fetch_status(video_job_id)
    youtube_url = None
    if _status_index(status) < _status_index("PUBLISHED"):
        try:
            from agents.publishing import publish_job
            result = _run_step("publishing", publish_job, video_job_id)
            youtube_url = result.get("youtube_url")
        except Exception as exc:
            raise RuntimeError(f"[publishing] mislukt: {exc}") from exc
    else:
        print(f"[pipeline] SKIP  publishing (status={status})")

    # ------------------------------------------------------------------ #
    # Done
    # ------------------------------------------------------------------ #
    final_status = _fetch_status(video_job_id)
    print(f"\n[pipeline] Klaar. Job {video_job_id} -> {final_status}")
    if youtube_url:
        print(f"[pipeline] YouTube URL: {youtube_url}")

    return {"status": final_status, "youtube_url": youtube_url}
