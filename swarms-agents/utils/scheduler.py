import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

LOCK_FILE = Path(__file__).parent.parent / ".pipeline.lock"
INTERVAL = int(os.getenv("SCHEDULER_INTERVAL_SECONDS", 600))
CHANNEL_ID = os.getenv("CHANNEL_ID", "5400b43e-73ae-428b-b72d-a02e3d986cf1")
NICHE = os.getenv("NICHE", "snowballwealth")


def _count_idea_jobs() -> int:
    from utils.supabase_client import get_client
    result = get_client().table("video_jobs").select("id", count="exact").eq("status", "IDEA").eq("channel_id", CHANNEL_ID).execute()
    return result.count or 0


def _oldest_idea_job_id() -> str | None:
    from utils.supabase_client import get_client
    rows = get_client().table("video_jobs").select("id").eq("status", "IDEA").eq("channel_id", CHANNEL_ID).order("created_at", desc=False).limit(1).execute().data or []
    return rows[0]["id"] if rows else None


def _oldest_seo_optimized_job_id() -> str | None:
    from utils.supabase_client import get_client
    rows = get_client().table("video_jobs").select("id").eq("status", "SEO_OPTIMIZED").eq("channel_id", CHANNEL_ID).order("updated_at", desc=False).limit(1).execute().data or []
    return rows[0]["id"] if rows else None


def run_once() -> None:
    if LOCK_FILE.exists():
        logger.warning("Lock file aanwezig — vorige run nog bezig, skip cyclus")
        return

    LOCK_FILE.write_text(str(os.getpid()))
    try:
        # 1. Quota-retry: SEO_OPTIMIZED jobs eerst (van gisteren)
        retry_id = _oldest_seo_optimized_job_id()
        if retry_id:
            logger.info("Quota-retry publishing voor job %s", retry_id)
            from agents.pipeline import run_pipeline
            run_pipeline(retry_id)
            return

        # 2. Genereer nieuwe IDEA jobs als buffer < 2
        if _count_idea_jobs() < 2:
            logger.info("Minder dan 2 IDEA jobs — content planning runnen")
            from agents.content_planning import run_content_planning
            run_content_planning(NICHE, {}, [], count=3)

        # 3. Run pipeline op oudste IDEA job
        job_id = _oldest_idea_job_id()
        if job_id:
            logger.info("Pipeline starten voor job %s", job_id)
            from agents.pipeline import run_pipeline
            run_pipeline(job_id)
        else:
            logger.info("Geen IDEA jobs beschikbaar — wacht op volgende cyclus")

    except Exception as exc:
        logger.error("Scheduler cycle fout: %s", exc, exc_info=True)
    finally:
        LOCK_FILE.unlink(missing_ok=True)


def start_scheduler() -> None:
    Path("logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("logs/scheduler.log", encoding="utf-8"),
        ],
    )
    logger.info("Scheduler gestart — interval %ds, channel %s", INTERVAL, CHANNEL_ID)
    while True:
        run_once()
        logger.info("Scheduler slaapt %ds...", INTERVAL)
        time.sleep(INTERVAL)
