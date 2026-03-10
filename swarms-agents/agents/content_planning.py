"""
Content Planning Agent
======================
Input : niche (str), trending_topics (dict), top_performers (list)
Output: 3 video-ideeën als JSON → geschreven naar video_jobs (status=IDEA)
Model : claude-3-5-sonnet
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from swarms import Agent

from utils.llm_factory import get_llm
from utils.supabase_client import create_job

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "content_planning.txt"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class VideoIdea(BaseModel):
    title_concept: str
    outline: list[str]
    keyword_targets: list[str]
    estimated_appeal: int = Field(..., ge=1, le=10)


class ContentPlanningInput(BaseModel):
    niche: str
    trending_topics: dict[str, Any]
    top_performers: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def build_content_planning_agent() -> Agent:
    """Build and return a configured Content Planning Swarms Agent."""
    llm = get_llm("claude-3-5-sonnet")
    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

    return Agent(
        agent_name="ContentPlanningAgent",
        agent_description=(
            "Genereert 3 sterke YouTube video-ideeën voor de Nederlandse "
            "vastgoedmarkt op basis van trending topics en kanaaldata."
        ),
        llm=llm,
        system_prompt=system_prompt,
        max_loops=1,
        verbose=True,
        output_type="str",
    )


# ---------------------------------------------------------------------------
# Main callable
# ---------------------------------------------------------------------------

def run_content_planning(
    niche: str,
    trending_topics: dict[str, Any],
    top_performers: list[dict[str, Any]],
) -> list[str]:
    """
    Run the Content Planning Agent and persist the generated ideas to Supabase.

    Args:
        niche:            Channel niche description.
        trending_topics:  Dict of topic → metadata (views, growth, etc.).
        top_performers:   List of dicts with top video performance data.

    Returns:
        List of created job IDs (one per idea).
    """
    data = ContentPlanningInput(
        niche=niche,
        trending_topics=trending_topics,
        top_performers=top_performers,
    )

    agent = build_content_planning_agent()

    # Build the user-facing task message (template vars are filled here;
    # the system prompt carries the format instructions).
    task = (
        f"Kanaal niche: {data.niche}\n\n"
        f"Trending onderwerpen:\n{json.dumps(data.trending_topics, ensure_ascii=False, indent=2)}\n\n"
        f"Best presterende video's:\n{json.dumps(data.top_performers, ensure_ascii=False, indent=2)}\n\n"
        "Genereer nu precies 3 video-ideeën in het opgegeven JSON-formaat."
    )

    logger.info("Content Planning Agent: starting run for niche='%s'", niche)
    raw_output: str = agent.run(task)
    logger.debug("Raw agent output:\n%s", raw_output)

    ideas = _parse_ideas(raw_output)
    job_ids = _persist_ideas(ideas, niche)

    logger.info("Content Planning: %d idea(s) saved → job IDs %s", len(job_ids), job_ids)
    return job_ids


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_ideas(raw: str) -> list[VideoIdea]:
    """Extract and validate the JSON array from the agent output."""
    # Strip markdown code fences if present
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(
            line for line in lines if not line.startswith("```")
        ).strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Agent output is not valid JSON:\n{raw[:500]}"
        ) from exc

    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON array, got: {type(data).__name__}")

    ideas = []
    for i, item in enumerate(data):
        try:
            ideas.append(VideoIdea(**item))
        except Exception as exc:
            raise ValueError(f"Idea #{i} failed validation: {exc}") from exc

    if len(ideas) != 3:
        logger.warning("Expected 3 ideas, got %d", len(ideas))

    return ideas


def _persist_ideas(ideas: list[VideoIdea], niche: str) -> list[str]:
    """Write each idea as a new video_job row with status=IDEA."""
    job_ids: list[str] = []
    for idea in ideas:
        job = create_job(
            status="IDEA",
            niche=niche,
            title_concept=idea.title_concept,
            outline=idea.outline,
            keyword_targets=idea.keyword_targets,
            estimated_appeal=idea.estimated_appeal,
        )
        job_ids.append(job.id)
    return job_ids
