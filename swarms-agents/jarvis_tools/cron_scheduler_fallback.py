"""
Cron Scheduler Fallback
========================
If Jarvis doesn't support cron agents natively, run this script as a daemon.
It uses the schedule library to trigger the YTA pipeline tools at set intervals.

Usage:
    python jarvis_tools/cron_scheduler_fallback.py

Install: pip install schedule
"""

import logging
import time

import schedule

from yta_run_pending_job import run as run_pending
from yta_create_daily_jobs import run as create_jobs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("yta_cron")


def hourly_pipeline():
    logger.info("=== Hourly pipeline run ===")
    result = run_pending()
    if result["success"]:
        logger.info("Pipeline OK: %s", result["output"][:200])
    else:
        logger.error("Pipeline failed: %s", result.get("errors", "unknown"))


def daily_job_creator():
    logger.info("=== Daily job creation ===")
    result = create_jobs()
    if result["success"]:
        logger.info("Jobs created: %s", result["output"][:200])
    else:
        logger.error("Job creation failed: %s", result.get("errors", "unknown"))


schedule.every().hour.at(":00").do(hourly_pipeline)
schedule.every().day.at("08:00").do(daily_job_creator)

if __name__ == "__main__":
    logger.info("YTA Cron Scheduler started")
    while True:
        schedule.run_pending()
        time.sleep(30)
