"""
Jarvis Memory Integration
==========================
Saves pipeline job metadata after each PUBLISHED video.
Stores entries as JSON in a local memory file that Jarvis can index.

If Jarvis SDK is not available, falls back to a local JSON log file.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Default memory directory — override via JARVIS_MEMORY_DIR env var
_DEFAULT_MEMORY_DIR = os.path.expanduser("~/.jarvis/memory/yta_pipeline")
MEMORY_DIR = os.getenv("JARVIS_MEMORY_DIR", _DEFAULT_MEMORY_DIR)
MEMORY_FILE = os.path.join(MEMORY_DIR, "published_videos.jsonl")


def save_to_memory(
    job_id: str,
    title: str,
    niche: str,
    youtube_url: str,
    script_length: int = 0,
    voice_duration: float = 0.0,
    extra: dict | None = None,
) -> bool:
    """
    Append a memory entry for a published video.

    Returns True on success, False on failure (never raises).
    """
    entry = {
        "job_id": job_id,
        "title": title,
        "niche": niche,
        "youtube_url": youtube_url,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "script_length": script_length,
        "voice_duration": voice_duration,
    }
    if extra:
        entry["extra"] = extra

    try:
        Path(MEMORY_DIR).mkdir(parents=True, exist_ok=True)
        with open(MEMORY_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
        logger.info("Saved memory entry for job %s: %s", job_id, title)
        return True
    except Exception:
        logger.exception("Failed to save memory entry for job %s", job_id)
        return False


def get_recent_entries(limit: int = 20) -> list[dict]:
    """Read the most recent memory entries."""
    if not os.path.exists(MEMORY_FILE):
        return []
    try:
        with open(MEMORY_FILE) as f:
            lines = f.readlines()
        entries = []
        for line in lines[-limit:]:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
        return entries
    except Exception:
        logger.exception("Failed to read memory entries")
        return []
