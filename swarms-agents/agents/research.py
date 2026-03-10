"""
Research Agent
==============
- Pakt de oudste job met status=IDEA
- Doet web research via DuckDuckGo (gratis) of SerpAPI (als key beschikbaar)
- Laat gpt-4-turbo de resultaten analyseren tot gestructureerde research_data
- Update de job: research_data (JSONB) + status → RESEARCHED
Model: gpt-4-turbo
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

import requests
from swarms import Agent

from utils.llm_factory import get_llm
from utils.retry import with_retry
from utils.supabase_client import VideoJob, get_next_job, update_job

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "research.txt"

# Number of search queries generated per job
NUM_QUERIES = 4
# Results fetched per query
RESULTS_PER_QUERY = 5


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def build_research_agent() -> Agent:
    """Build and return the Research Swarms Agent (gpt-4-turbo)."""
    llm = get_llm("gpt-4-turbo")
    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

    return Agent(
        agent_name="ResearchAgent",
        agent_description=(
            "Analyseert zoekresultaten en extraheert gestructureerde research-data "
            "voor Nederlandse vastgoed YouTube-video's."
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

def run_research(job_id: str | None = None) -> VideoJob | None:
    """
    Process one IDEA job through the Research Agent.

    Args:
        job_id: Optional specific job ID. If None, picks the next IDEA job.

    Returns:
        Updated VideoJob with status=RESEARCHED, or None if no job found.
    """
    job: VideoJob | None
    if job_id:
        from utils.supabase_client import get_client
        resp = get_client().table("video_jobs").select("*").eq("id", job_id).single().execute()
        if not resp.data:
            logger.warning("Job %s not found.", job_id)
            return None
        job = VideoJob(**resp.data)
    else:
        job = get_next_job("IDEA")

    if job is None:
        logger.info("No IDEA jobs available.")
        return None

    logger.info("Research Agent: processing job id=%s title='%s'", job.id, job.title_concept)

    try:
        search_results = _gather_search_results(job)
        research_data = _analyse_with_agent(job, search_results)
        updated = update_job(
            job.id,
            research_data=research_data,
            status="RESEARCHED",
        )
        logger.info("Job %s → RESEARCHED", job.id)
        return updated
    except Exception as exc:
        logger.error("Research failed for job %s: %s", job.id, exc, exc_info=True)
        update_job(job.id, error_message=str(exc))
        raise


# ---------------------------------------------------------------------------
# Search helpers
# ---------------------------------------------------------------------------

def _gather_search_results(job: VideoJob) -> list[dict[str, Any]]:
    """Build search queries from the job and fetch results."""
    queries = _build_queries(job)
    all_results: list[dict[str, Any]] = []

    for query in queries:
        results = _search(query)
        all_results.extend(results)
        logger.debug("Query '%s' → %d results", query, len(results))

    # Deduplicate by URL
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for r in all_results:
        url = r.get("url", "")
        if url and url not in seen:
            seen.add(url)
            unique.append(r)

    logger.info("Total unique search results: %d", len(unique))
    return unique


def _build_queries(job: VideoJob) -> list[str]:
    """Generate targeted Dutch-language search queries from the job data."""
    title = job.title_concept or ""
    keywords = job.keyword_targets or []

    queries = [
        f"{title} Nederland statistieken 2024",
        f"{title} Nederlandse vastgoedmarkt analyse",
    ]
    if keywords:
        queries.append(f"{keywords[0]} Nederland investeren tips")
        if len(keywords) > 1:
            queries.append(f"{keywords[1]} markttrend Nederland")

    return queries[:NUM_QUERIES]


@with_retry(max_attempts=3, base_delay=2.0, exceptions=(requests.RequestException,))
def _search(query: str) -> list[dict[str, Any]]:
    """
    Fetch search results via SerpAPI (if key set) or DuckDuckGo HTML fallback.
    Returns list of {title, url, snippet}.
    """
    serp_key = os.getenv("SERPAPI_KEY")
    if serp_key:
        return _serpapi_search(query, serp_key)
    return _duckduckgo_search(query)


def _serpapi_search(query: str, api_key: str) -> list[dict[str, Any]]:
    """Search via SerpAPI (Google results)."""
    resp = requests.get(
        "https://serpapi.com/search",
        params={
            "q": query,
            "hl": "nl",
            "gl": "nl",
            "num": RESULTS_PER_QUERY,
            "api_key": api_key,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    results = []
    for item in data.get("organic_results", []):
        results.append(
            {
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            }
        )
    return results


def _duckduckgo_search(query: str) -> list[dict[str, Any]]:
    """
    Lightweight DuckDuckGo instant-answer API fallback.
    Note: DDG's HTML search has rate limits; use SerpAPI for production.
    """
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        logger.warning("duckduckgo_search not installed; skipping DDG search.")
        return []

    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, region="nl-nl", max_results=RESULTS_PER_QUERY):
            results.append(
                {
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                }
            )
    return results


# ---------------------------------------------------------------------------
# LLM analysis
# ---------------------------------------------------------------------------

def _analyse_with_agent(job: VideoJob, search_results: list[dict[str, Any]]) -> dict:
    """Run the Research Agent over the gathered search results."""
    agent = build_research_agent()

    # Build full task message (system prompt holds the output format)
    task = (
        f"Titel: {job.title_concept}\n"
        f"Outline: {json.dumps(job.outline, ensure_ascii=False)}\n"
        f"Doelzoekwoorden: {json.dumps(job.keyword_targets, ensure_ascii=False)}\n\n"
        f"Zoekresultaten:\n{json.dumps(search_results, ensure_ascii=False, indent=2)}"
    )

    raw_output: str = agent.run(task)
    return _parse_research_json(raw_output)


def _parse_research_json(raw: str) -> dict:
    """Extract and validate the JSON object from agent output."""
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
            f"Research Agent output is not valid JSON:\n{raw[:500]}"
        ) from exc

    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object, got: {type(data).__name__}")

    required_keys = {"key_facts", "statistics", "market_trends", "sources"}
    missing = required_keys - data.keys()
    if missing:
        logger.warning("Research JSON missing expected keys: %s", missing)

    return data
