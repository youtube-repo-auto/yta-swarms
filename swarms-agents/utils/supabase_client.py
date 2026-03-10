"""
Singleton Supabase client + helper functions for video_jobs table.

Pipeline statuses:
  IDEA → RESEARCHED → SCRIPTED → SCRIPT_APPROVED →
  VOICE_GENERATED → VIDEO_GENERATED → MEDIA_GENERATED → SEO_OPTIMIZED → UPLOADED
"""

import os
import logging
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from supabase import create_client, Client
from pydantic import BaseModel, Field

load_dotenv()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Singleton client
# ---------------------------------------------------------------------------

_client: Client | None = None


def get_client() -> Client:
    """Return (or create) the singleton Supabase client."""
    global _client
    if _client is None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
        if not url or not key:
            raise EnvironmentError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set."
            )
        _client = create_client(url, key)
        logger.debug("Supabase client initialised for %s", url)
    return _client


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

PIPELINE_STATUSES = [
    "IDEA",
    "RESEARCHED",
    "SCRIPTED",
    "SCRIPT_APPROVED",
    "VOICE_GENERATED",
    "VIDEO_GENERATED",
    "MEDIA_GENERATED",
    "SEO_OPTIMIZED",
    "UPLOADED",
]


class VideoJob(BaseModel):
    """Mirrors the video_jobs table schema."""

    id: str
    status: str
    niche: str | None = None
    title_concept: str | None = None
    outline: str | None = None
    keyword_targets: list[str] | None = None
    estimated_appeal: int | None = Field(None, ge=1, le=10)
    research_data: dict | None = None
    script: str | None = None
    script_approved: bool = False
    voice_url: str | None = None
    video_url: str | None = None
    thumbnail_url: str | None = None
    seo_title: str | None = None
    seo_description: str | None = None
    seo_tags: list[str] | None = None
    youtube_video_id: str | None = None
    error_message: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

TABLE = "video_jobs"


def get_next_job(status: str) -> VideoJob | None:
    """
    Fetch the oldest video_job with the given status.

    Args:
        status: Pipeline status string (e.g. "IDEA", "RESEARCHED").

    Returns:
        VideoJob instance or None if no job is found.
    """
    if status not in PIPELINE_STATUSES:
        raise ValueError(
            f"Invalid status '{status}'. Valid values: {PIPELINE_STATUSES}"
        )

    client = get_client()
    response = (
        client.table(TABLE)
        .select("*")
        .eq("status", status)
        .order("created_at", desc=False)
        .limit(1)
        .execute()
    )

    if not response.data:
        logger.debug("No job found with status=%s", status)
        return None

    job_data = response.data[0]
    logger.info("Fetched job id=%s status=%s", job_data["id"], job_data["status"])
    return VideoJob(**job_data)


def update_job(job_id: str, **fields: Any) -> VideoJob:
    """
    Update arbitrary fields on a video_job row and return the updated job.

    Usage:
        update_job(job_id, status="RESEARCHED", research_data={...})

    Args:
        job_id: UUID of the video_job row.
        **fields: Column names and their new values.

    Returns:
        Updated VideoJob instance.

    Raises:
        RuntimeError: If the update returns no data (row not found).
    """
    if not fields:
        raise ValueError("At least one field must be provided to update_job.")

    fields["updated_at"] = datetime.now(timezone.utc).isoformat()

    client = get_client()
    response = (
        client.table(TABLE)
        .update(fields)
        .eq("id", job_id)
        .execute()
    )

    if not response.data:
        raise RuntimeError(
            f"update_job: no row returned for id={job_id}. "
            "Check that the job exists and RLS policies allow updates."
        )

    updated = response.data[0]
    logger.info(
        "Updated job id=%s → fields=%s", job_id, list(fields.keys())
    )
    return VideoJob(**updated)


def create_job(**fields: Any) -> VideoJob:
    """
    Insert a new row into video_jobs and return it.

    Args:
        **fields: Column names and their values (status is required).

    Returns:
        Created VideoJob instance.
    """
    if "status" not in fields:
        raise ValueError("'status' is required when creating a job.")

    now = datetime.now(timezone.utc).isoformat()
    fields.setdefault("created_at", now)
    fields["updated_at"] = now

    client = get_client()
    response = client.table(TABLE).insert(fields).execute()

    if not response.data:
        raise RuntimeError("create_job: insert returned no data.")

    created = response.data[0]
    logger.info("Created job id=%s status=%s", created["id"], created["status"])
    return VideoJob(**created)
