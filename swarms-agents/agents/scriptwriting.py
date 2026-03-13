"""
Scriptwriting Agent
===================
- Picks the oldest job with status=RESEARCHED
- Writes a ~2000-word English script (educational, transformative)
- Updates the job: script (text) + status → SCRIPTED
Model: claude-3-5-sonnet  (→ claude-sonnet-4-5 via llm_factory)

No swarms dependency — uses the Anthropic SDK directly via LLMClient.
"""

import json
import logging
from pathlib import Path

from utils.llm_factory import LLMClient, get_llm
from utils.supabase_client import VideoJob, get_next_job, update_job

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "scriptwriting.txt"

MIN_WORDS = 1500
# ~2000-word English script; 6000 tokens gives comfortable headroom
_SCRIPT_MAX_TOKENS = 6000


# ---------------------------------------------------------------------------
# Minimal agent shim  (replaces swarms.Agent)
# ---------------------------------------------------------------------------

class _Agent:
    """
    Lightweight agent that wraps an LLMClient + system prompt.
    Exposes .run(task) → str, matching the swarms Agent interface.
    """

    def __init__(self, llm: LLMClient, system_prompt: str):
        self._llm = llm
        self._system_prompt = system_prompt

    def run(self, task: str) -> str:
        return self._llm.run(task=task, system=self._system_prompt)


# ---------------------------------------------------------------------------
# Agent factory  (signature unchanged; return type changed from swarms.Agent)
# ---------------------------------------------------------------------------

def build_scriptwriting_agent() -> _Agent:
    """Build and return the Scriptwriting Agent (claude-3-5-sonnet)."""
    # Larger token budget for ~2000-word scripts; passed through to the SDK
    llm = get_llm("claude-3-5-sonnet", max_tokens=_SCRIPT_MAX_TOKENS)
    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    return _Agent(llm=llm, system_prompt=system_prompt)


# ---------------------------------------------------------------------------
# Main callable  (signature and behaviour unchanged)
# ---------------------------------------------------------------------------

def run_scriptwriting(job_id: str | None = None) -> VideoJob | None:
    """
    Process one RESEARCHED job through the Scriptwriting Agent.

    Args:
        job_id: Optional specific job ID. If None, picks the next RESEARCHED job.

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
            logger.warning("Job %s not found.", job_id)
            return None
        job = VideoJob(**resp.data)
    else:
        job = get_next_job("RESEARCHED")

    if job is None:
        logger.info("No RESEARCHED jobs available.")
        return None

    logger.info(
        "Scriptwriting Agent: processing job id=%s title='%s'",
        job.id,
        job.title_concept,
    )

    try:
        script = _generate_script(job)
        word_count = len(script.split())
        logger.info("Script generated: %d words for job %s", word_count, job.id)

        if word_count < MIN_WORDS:
            logger.warning(
                "Script for job %s is only %d words (minimum: %d). "
                "Consider reviewing the output.",
                job.id,
                word_count,
                MIN_WORDS,
            )

        updated = update_job(
            job.id,
            script=script,
            status="SCRIPTED",
        )
        logger.info("Job %s → SCRIPTED (%d words)", job.id, word_count)
        return updated

    except Exception as exc:
        logger.error(
            "Scriptwriting failed for job %s: %s", job.id, exc, exc_info=True
        )
        update_job(job.id, error_message=str(exc))
        raise


# ---------------------------------------------------------------------------
# Core generation  (unchanged except agent is now _Agent instead of swarms.Agent)
# ---------------------------------------------------------------------------

def _generate_script(job: VideoJob) -> str:
    """Build the agent task message and run the Scriptwriting Agent."""
    agent = build_scriptwriting_agent()

    research_str = (
        json.dumps(job.research_data, ensure_ascii=False, indent=2)
        if job.research_data
        else "No research data available."
    )

    outline_str = (
        json.dumps(job.outline, ensure_ascii=False, indent=2)
        if job.outline
        else "No outline available."
    )

    task = (
        "VIDEO CONTEXT\n"
        f"Title: {job.title_concept}\n"
        f"Outline:\n{outline_str}\n"
        f"Target keywords: {json.dumps(job.keyword_targets, ensure_ascii=False)}\n\n"
        f"RESEARCH DATA:\n{research_str}\n\n"
        "Write the full YouTube video script in English, following the system prompt instructions. "
        "The script must be 1,900–2,100 words and ready to be spoken by a narrator. "
        "Output plain text only — no markdown, no labels, no timestamps, no stage directions."
    )

    raw_output: str = agent.run(task)
    script = raw_output.strip()

    if not script:
        raise ValueError("Scriptwriting Agent returned an empty response.")

    return script
