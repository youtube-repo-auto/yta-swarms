"""
Scriptwriting Agent
===================
- Pakt de oudste job met status=RESEARCHED
- Schrijft een ~2000-woorden Nederlandstalig script (educatief, transformatief)
- Update de job: script (text) + status → SCRIPTED
Model: claude-3-5-sonnet (beste voor long-form NL content)
"""

import json
import logging
from pathlib import Path

from swarms import Agent

from utils.llm_factory import get_llm
from utils.supabase_client import VideoJob, get_next_job, update_job

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "scriptwriting.txt"

# Minimum acceptable word count; warn if script falls short
MIN_WORDS = 1500


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def build_scriptwriting_agent() -> Agent:
    """Build and return the Scriptwriting Swarms Agent (claude-3-5-sonnet)."""
    llm = get_llm("claude-3-5-sonnet")
    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

    return Agent(
        agent_name="ScriptwritingAgent",
        agent_description=(
            "Schrijft uitgebreide, transformatieve YouTube-scripts in het Nederlands "
            "voor de vastgoedinvesteerder doelgroep (25-45 jaar)."
        ),
        llm=llm,
        system_prompt=system_prompt,
        max_loops=1,
        # Allow more tokens for a ~2000-word script
        max_tokens=6000,
        verbose=True,
        output_type="str",
    )


# ---------------------------------------------------------------------------
# Main callable
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
# Core generation
# ---------------------------------------------------------------------------

def _generate_script(job: VideoJob) -> str:
    """Build the agent task message and run the Scriptwriting Agent."""
    agent = build_scriptwriting_agent()

    research_str = (
        json.dumps(job.research_data, ensure_ascii=False, indent=2)
        if job.research_data
        else "Geen research data beschikbaar."
    )

    outline_str = (
        json.dumps(job.outline, ensure_ascii=False, indent=2)
        if job.outline
        else "Geen outline beschikbaar."
    )

    task = (
        f"VIDEO CONCEPT\n"
        f"Titel: {job.title_concept}\n"
        f"Outline:\n{outline_str}\n"
        f"Doelzoekwoorden: {json.dumps(job.keyword_targets, ensure_ascii=False)}\n\n"
        f"RESEARCH DATA:\n{research_str}\n\n"
        "Schrijf nu het volledige video-script volgens de instructies in het systeem-prompt. "
        "Het script moet 1800-2200 woorden bevatten en volledig klaar zijn om uitgesproken te worden."
    )

    raw_output: str = agent.run(task)
    script = raw_output.strip()

    if not script:
        raise ValueError("Scriptwriting Agent returned an empty response.")

    return script
