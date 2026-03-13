# utils/scheduler.py
"""
Pipeline Scheduler
==================
Polls video_jobs every SCHEDULER_INTERVAL_SECONDS (default 600).

Each cycle:
  1. Count IDEA jobs.
  2. If 0: call run_content_planning() to create 3 new ideas, then continue.
  3. Pick oldest IDEA job (created_at ASC).
  4. Run run_pipeline(job_id) for that job.

Lock: writes .pipeline_lock while a cycle is running.
      Skips the cycle if the lock already exists (previous run still active).

Exports:
  start_scheduler()  — runs forever (blocking)
  run_once()         — single cycle, no sleep, for testing
"""
import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_LOCK_FILE = Path(__file__).parent.parent / ".pipeline_lock"
_DEFAULT_INTERVAL = 600  # seconds


def _interval() -> int:
    try:
        return int(os.getenv("SCHEDULER_INTERVAL_SECONDS", _DEFAULT_INTERVAL))
    except ValueError:
        logger.warning("Invalid SCHEDULER_INTERVAL_SECONDS — using default %ds", _DEFAULT_INTERVAL)
        return _DEFAULT_INTERVAL


# ---------------------------------------------------------------------------
# Lock helpers
# ---------------------------------------------------------------------------

def _acquire_lock() -> bool:
    """Try to create the lock file. Returns True if acquired, False if already locked."""
    if _LOCK_FILE.exists():
        logger.warning("Lock file exists (%s) — skipping cycle", _LOCK_FILE)
        return False
    _LOCK_FILE.write_text(str(os.getpid()))
    return True


def _release_lock() -> None:
    try:
        _LOCK_FILE.unlink(missing_ok=True)
    except Exception as exc:
        logger.error("Failed to release lock: %s", exc)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _channel_id() -> str | None:
    return os.getenv("CHANNEL_ID")


def _count_idea_jobs() -> int:
    from utils.supabase_client import get_client
    q = get_client().table("video_jobs").select("id", count="exact").eq("status", "IDEA")
    channel_id = _channel_id()
    if channel_id:
        q = q.eq("channel_id", channel_id)
    return q.execute().count or 0


def _oldest_idea_job_id() -> str | None:
    from utils.supabase_client import get_client
    q = (
        get_client()
        .table("video_jobs")
        .select("id")
        .eq("status", "IDEA")
        .order("created_at", desc=False)
        .limit(1)
    )
    channel_id = _channel_id()
    if channel_id:
        q = q.eq("channel_id", channel_id)
    resp = q.execute()
    if resp.data:
        return resp.data[0]["id"]
    return None


# ---------------------------------------------------------------------------
# Idea generation
# ---------------------------------------------------------------------------

def _generate_ideas() -> None:
    """Call run_content_planning with niche from env and empty context."""
    niche = os.getenv("NICHE", os.getenv("CHANNEL_NICHE", "general YouTube channel"))
    logger.info("Geen IDEA jobs gevonden — genereer 3 nieuwe ideeën voor niche='%s'", niche)

    from agents.content_planning import run_content_planning
    job_ids = run_content_planning(
        niche=niche,
        trending_topics={},
        top_performers=[],
    )
    logger.info("Content planning: %d nieuwe jobs aangemaakt: %s", len(job_ids), job_ids)


# ---------------------------------------------------------------------------
# Single cycle
# ---------------------------------------------------------------------------

def _run_cycle() -> None:
    """Execute one scheduler cycle: maybe generate ideas, then run one pipeline job."""
    logger.info("Scheduler cycle gestart")

    # Step a: count IDEA jobs
    idea_count = _count_idea_jobs()
    logger.info("IDEA jobs in queue: %d", idea_count)

    # Step b: generate ideas if queue empty
    if idea_count == 0:
        _generate_ideas()

    # Step c: pick oldest IDEA job
    job_id = _oldest_idea_job_id()
    if not job_id:
        logger.warning("Geen IDEA job beschikbaar na idee-generatie — cycle overgeslagen")
        return

    # Step d: run pipeline
    logger.info("Pipeline starten voor job %s", job_id)
    from agents.pipeline import run_pipeline
    result = run_pipeline(job_id)
    logger.info("Pipeline klaar: job=%s status=%s youtube_url=%s",
                job_id, result.get("status"), result.get("youtube_url"))


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def run_once() -> None:
    """
    Execute a single scheduler cycle (no sleep). Useful for testing.
    Acquires and releases the lock; skips if already locked.
    """
    if not _acquire_lock():
        return
    try:
        _run_cycle()
    except Exception as exc:
        logger.error("Cycle mislukt: %s", exc, exc_info=True)
    finally:
        _release_lock()


def start_scheduler() -> None:
    """
    Run the scheduler loop forever, polling every SCHEDULER_INTERVAL_SECONDS.
    Never raises — all cycle exceptions are caught and logged.
    """
    interval = _interval()
    logger.info("Scheduler gestart (interval=%ds, lock=%s)", interval, _LOCK_FILE)

    while True:
        if not _acquire_lock():
            logger.info("Wacht %ds voor volgende poging", interval)
            time.sleep(interval)
            continue

        try:
            _run_cycle()
        except Exception as exc:
            logger.error("Cycle mislukt: %s", exc, exc_info=True)
        finally:
            _release_lock()

        logger.info("Cycle klaar — slaap %ds", interval)
        time.sleep(interval)
