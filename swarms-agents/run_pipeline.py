#!/usr/bin/env python3
"""
run_pipeline.py – Entry point for the YouTube Automation Pipeline.

Usage:
    # Run a specific job through all available steps (1-3):
    python run_pipeline.py --job-id <uuid>

    # Generate new ideas (step 1) and immediately process the first one:
    python run_pipeline.py --generate-ideas

    # Process the next available job in a given step:
    python run_pipeline.py --step research
    python run_pipeline.py --step scriptwriting

Pipeline steps handled here:
    1. content_planning  → status: IDEA
    2. research          → status: RESEARCHED
    3. scriptwriting     → status: SCRIPTED

Environment:
    Copy .env.example to .env and fill in all keys before running.
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from utils.supabase_client import supabase

from agents.voice_generation import generate_voice_for_job

from dotenv import load_dotenv

# Ensure the package root is on sys.path regardless of invocation directory
sys.path.insert(0, str(Path(__file__).parent))

load_dotenv()

# ── Logging setup ────────────────────────────────────────────────────────────

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("run_pipeline")


# ── Step runners ─────────────────────────────────────────────────────────────

def step_content_planning() -> list[str]:
    """
    Run the Content Planning Agent.
    Reads CHANNEL_NICHE from env; uses placeholder trend/performer data
    unless you pass real data through the API or a separate fetch step.
    """
    from agents.content_planning import run_content_planning

    niche = os.getenv("CHANNEL_NICHE", "Personal Finance & Wealth Building")

    # In production, fetch these from a YouTube Analytics API call or a
    # dedicated TrendingAgent. Here we use sensible defaults for a dry-run.
    trending_topics: dict = {
        "How to invest with little money 2026": {"search_volume": 18000, "trend": "stijgend"},
        "Passive income ideas from home": {"search_volume": 22000, "trend": "stijgend"},
        "Stock market for beginners": {"search_volume": 35000, "trend": "stabiel"},
        "Real estate investing 2026": {"search_volume": 14000, "trend": "stijgend"},
        "How to save money fast": {"search_volume": 28000, "trend": "stabiel"},
    }

    top_performers: list = [
        {
            "title": "I Invested $1000 Every Month for 5 Years (Results)",
            "views": 2400000,
            "likes": 87000,
            "comments": 4200,
            "avg_watch_pct": 71,
        },
        {
            "title": "7 Passive Income Ideas That Actually Work in 2026",
            "views": 1800000,
            "likes": 64000,
            "comments": 3100,
            "avg_watch_pct": 68,
        },
    ]

    logger.info("── Step 1: Content Planning ──────────────────────────────")
    job_ids = run_content_planning(niche, trending_topics, top_performers)
    logger.info("Content Planning complete. Created %d job(s): %s", len(job_ids), job_ids)
    return job_ids


def step_research(job_id: str | None = None):
    """Run the Research Agent on the next IDEA job (or a specific job_id)."""
    from agents.research import run_research

    logger.info("── Step 2: Research ──────────────────────────────────────")
    job = run_research(job_id=job_id)
    if job is None:
        logger.warning("Research: no job processed (nothing available).")
    else:
        logger.info("Research complete. Job %s → %s", job.id, job.status)
    return job


def step_scriptwriting(job_id: str | None = None):
    """Run the Scriptwriting Agent on the next RESEARCHED job (or a specific job_id)."""
    from agents.scriptwriting import run_scriptwriting
    from utils.viral_hooks import generate_viral_hook

    logger.info("── Step 3: Scriptwriting ─────────────────────────────────")
    if job_id:
        jobs = [job_id]
    else:
        # Next available RESEARCHED job
        result = supabase.table('video_jobs').select('id').eq('status', 'RESEARCHED').limit(1).execute()
        jobs = [row['id'] for row in (result.data or [])]
    
    if not jobs:
        logger.warning("Geen RESEARCHED jobs beschikbaar")
        return None
    
    # Viral hook voor SHORTS (vóór script schrijven)
    for job_id in job_ids:
        job_data = supabase.table('video_jobs').select('format,title_concept,niche').eq('id', job_id).single().execute().data
        if job_data.get('format') == 'SHORT':
            hook = generate_viral_hook(job_data['title_concept'], job_data.get('niche', 'snowball_wealth'))
            supabase.table('video_jobs').update({
                'hook_data': hook,
                'hook_visual_prompt': hook['hook_visual']
            }).eq('id', job_id).execute()
            logger.info("✅ Viral hook toegevoegd job %s: %s", job_id, hook['hook_text'])
    
    job = run_scriptwriting(job_id=job_id)
    if job is None:
        logger.warning("Scriptwriting: no job processed (nothing available).")
    else:
        word_count = len((job.script or "").split())
        logger.info(
            "Scriptwriting complete. Job %s → %s (%d words)",
            job.id, job.status, word_count,
        )
    return job


# — Voice generation ————————————————————————————————————————

def run_voice_generation():
    from utils.supabase_client import get_client
    supabase = get_client()

    result = (
        supabase.table("video_jobs")
        .select("id, title_concept")
        .eq("status", "SCRIPT_APPROVED")
        .execute()
    )
    jobs = result.data
    print(f"Gevonden: {len(jobs)} SCRIPT_APPROVED job(s)")

    for job in jobs:
        print(f"\n→ {job['title_concept']}")
        try:
            generate_voice_for_job(job["id"])
        except Exception as e:
            print(f"❌ Fout: {e}")
            supabase.table("video_jobs").update({
                "error_message": str(e)
            }).eq("id", job["id"]).execute()

# ── Full pipeline for a single job ───────────────────────────────────────────

def run_full_pipeline(job_id: str):
    """
    Drive an existing job through steps 2-3 (research + scriptwriting).
    If the job is still at IDEA it is also researched first.
    """
    from utils.supabase_client import get_client, VideoJob

    resp = (
        get_client()
        .table("video_jobs")
        .select("*")
        .eq("id", job_id)
        .single()
        .execute()
    )
    if not resp.data:
        logger.error("Job %s not found in database.", job_id)
        sys.exit(1)

    job = VideoJob(**resp.data)
    logger.info(
        "Starting full pipeline for job %s (current status: %s)", job_id, job.status
    )

    if job.status == "IDEA":
        job = step_research(job_id=job_id)
        if job is None:
            logger.error("Research step failed for job %s", job_id)
            sys.exit(1)

    if job.status == "RESEARCHED":
        job = step_scriptwriting(job_id=job_id)
        if job is None:
            logger.error("Scriptwriting step failed for job %s", job_id)
            sys.exit(1)

    logger.info("Pipeline complete. Final job status: %s", job.status if job else "unknown")
    return job


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="YouTube Automation Pipeline – runs one video job end-to-end.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--job-id",
        metavar="UUID",
        help="Process a specific job through all steps (research + scriptwriting).",
    )
    mode.add_argument(
        "--generate-ideas",
        action="store_true",
        help=(
            "Run the Content Planning Agent to generate 3 new video ideas, "
            "then immediately run research on the first idea."
        ),
    )
    mode.add_argument(
        "--voice",
        action="store_true",
        help="Run voice generation on all SCRIPT_APPROVED jobs.",
    )
    mode.add_argument(
        "--step",
        choices=["content_planning", "research", "scriptwriting", "voice"],
        help="Run a single pipeline step on the next available job.",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    logger.info("═══════════════════════════════════════════════════════════")
    logger.info(" YouTube Automation Pipeline – Real Estate NL")
    logger.info("═══════════════════════════════════════════════════════════")

    if args.job_id:
        run_full_pipeline(args.job_id)

    elif args.generate_ideas:
        job_ids = step_content_planning()
        if job_ids:
            # Immediately process the first idea through research + scripting
            first_id = job_ids[0]
            logger.info("Auto-processing first idea: job %s", first_id)
            run_full_pipeline(first_id)
        else:
            logger.warning("No ideas were generated.")

    elif args.voice:
        run_voice_generation()
        
    elif args.step == "content_planning":
        step_content_planning()

    elif args.step == "research":
        step_research()

    elif args.step == "scriptwriting":
        step_scriptwriting()

    elif args.step == "voice":
        run_voice_generation()
    
    logger.info("═══════════════════════════════════════════════════════════")
    logger.info(" Done.")
    logger.info("═══════════════════════════════════════════════════════════")


if __name__ == "__main__":
    main()
