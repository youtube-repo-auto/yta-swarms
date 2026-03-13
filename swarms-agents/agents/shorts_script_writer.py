# agents/shorts_script_writer.py
"""
Shorts Script Writer Agent
==========================
Writes a 45–60 second YouTube Shorts script as strict JSON.
Mirrors agents/scriptwriting.py patterns exactly.

Model: claude-haiku (fast, cheap — short output)
Prompt: prompts/shorts_script.txt

Public API:
  write_shorts_script(topic, angle, target_audience) -> dict
    Returns parsed JSON + full_script string.

  run_shorts_scriptwriting(job_id) -> VideoJob | None
    Loads job from DB, generates script, saves to DB, returns updated job.
"""
import json
import logging
from pathlib import Path

from utils.llm_factory import LLMClient, get_llm
from utils.supabase_client import VideoJob, get_next_job, update_job

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "shorts_script.txt"

_DEFAULT_TARGET_AUDIENCE = (
    "Dutch people aged 25–45 who are curious about long-term investing "
    "but have no finance background"
)

# Shorts output is small; 1024 tokens is more than enough
_SHORTS_MAX_TOKENS = 1024


# ---------------------------------------------------------------------------
# Minimal agent shim — identical to scriptwriting.py
# ---------------------------------------------------------------------------

class _Agent:
    def __init__(self, llm: LLMClient, system_prompt: str):
        self._llm = llm
        self._system_prompt = system_prompt

    def run(self, task: str) -> str:
        return self._llm.run(task=task, system=self._system_prompt)


def _build_agent() -> _Agent:
    llm = get_llm("claude-haiku", max_tokens=_SHORTS_MAX_TOKENS)
    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    return _Agent(llm=llm, system_prompt=system_prompt)


# ---------------------------------------------------------------------------
# Core generation
# ---------------------------------------------------------------------------

def _parse_response(raw: str) -> dict:
    """Strip optional markdown fences and parse JSON."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(
            line for line in lines if not line.startswith("```")
        ).strip()

    data = json.loads(cleaned)

    required = {"hook", "body", "question_cta", "on_screen_text", "estimated_duration_seconds"}
    missing = required - data.keys()
    if missing:
        raise ValueError(f"Shorts JSON missing required keys: {missing}")

    if not isinstance(data["body"], list) or not (2 <= len(data["body"]) <= 3):
        raise ValueError(f"body must be a list of 2–3 items, got: {data['body']}")

    return data


def _build_full_script(data: dict) -> str:
    """Concatenate hook + body items + question_cta into a single spoken string."""
    parts = [data["hook"]] + data["body"] + [data["question_cta"]]
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Public: write_shorts_script
# ---------------------------------------------------------------------------

def write_shorts_script(
    topic: str,
    angle: str,
    target_audience: str = _DEFAULT_TARGET_AUDIENCE,
) -> dict:
    """
    Generate a YouTube Shorts script for the given topic and angle.

    Args:
        topic:           Video subject (e.g. "Investing €500/month for 20 years").
        angle:           Specific story angle or hook idea.
        target_audience: Who the video is for (default: Snowball Wealth audience).

    Returns:
        Parsed JSON dict with keys: hook, body, question_cta, on_screen_text,
        estimated_duration_seconds, full_script.

    Raises:
        ValueError: If the model output is missing required keys or malformed.
        json.JSONDecodeError: If the model output is not valid JSON.
    """
    agent = _build_agent()

    task = (
        f"Topic: {topic}\n"
        f"Angle: {angle}\n"
        f"Target audience: {target_audience}\n\n"
        "Write the YouTube Shorts script following the system prompt instructions. "
        "Return ONLY the raw JSON object. No markdown, no code blocks."
    )

    logger.info("Shorts script generation: topic='%s' angle='%s'", topic, angle)
    raw = agent.run(task)

    if not raw.strip():
        raise ValueError("Shorts Script Agent returned an empty response.")

    data = _parse_response(raw)
    data["full_script"] = _build_full_script(data)

    duration = data["estimated_duration_seconds"]
    logger.info(
        "Shorts script generated: %d words, ~%ds",
        len(data["full_script"].split()),
        duration,
    )
    return data


# ---------------------------------------------------------------------------
# Public: run_shorts_scriptwriting  (pipeline entry point)
# ---------------------------------------------------------------------------

def run_shorts_scriptwriting(job_id: str | None = None) -> VideoJob | None:
    """
    Process one RESEARCHED SHORT job through the Shorts Script Writer.

    Args:
        job_id: Specific job UUID, or None to pick the next RESEARCHED SHORT job.

    Returns:
        Updated VideoJob with status=SCRIPTED, or None if no job found.
    """
    job: VideoJob | None

    if job_id:
        from utils.supabase_client import get_client
        resp = (
            get_client()
            .table("video_jobs")
            .select("*")
            .eq("id", job_id)
            .single()
            .execute()
        )
        if not resp.data:
            logger.warning("Shorts scriptwriting: job %s not found.", job_id)
            return None
        job = VideoJob(**resp.data)
    else:
        # Fetch next RESEARCHED job that is a SHORT
        from utils.supabase_client import get_client
        resp = (
            get_client()
            .table("video_jobs")
            .select("*")
            .eq("status", "RESEARCHED")
            .eq("format", "SHORT")
            .order("created_at", desc=False)
            .limit(1)
            .execute()
        )
        if not resp.data:
            logger.info("No RESEARCHED SHORT jobs available.")
            return None
        job = VideoJob(**resp.data[0])

    if job is None:
        return None

    logger.info(
        "Shorts Script Writer: processing job id=%s title='%s'",
        job.id,
        job.title_concept,
    )

    try:
        # Derive topic and angle from job fields
        topic = job.title_concept or "Personal finance investing"
        angle = (
            json.dumps(job.outline, ensure_ascii=False)
            if job.outline
            else "General long-term investing benefits"
        )

        result = write_shorts_script(topic=topic, angle=angle)

        full_script = result["full_script"]
        word_count = len(full_script.split())
        logger.info("Shorts script: %d words for job %s", word_count, job.id)

        updated = update_job(
            job.id,
            script=full_script,
            status="SCRIPTED",
        )
        logger.info("Job %s → SCRIPTED (SHORT, %d words)", job.id, word_count)
        return updated

    except Exception as exc:
        logger.error(
            "Shorts scriptwriting failed for job %s: %s", job.id, exc, exc_info=True
        )
        update_job(job.id, error_message=str(exc))
        raise
