# agents/publishing.py
"""
Publishing Agent
================
1. Fetch job from video_jobs: video_url, thumbnail_url, seo_title,
   seo_description, seo_tags, channel_id.
2. Build YouTube Data API v3 service via OAuth2 refresh token.
3. Download video + thumbnail from Supabase Storage via SDK.
4. Upload video as private, then set thumbnail.
5. On success: update video_jobs youtube_video_id, youtube_url, status=PUBLISHED.
6. On failure: update video_jobs status=PUBLISH_FAILED, log error.

Exports: publish_job(video_job_id: str) -> dict
"""
import logging
import os
import tempfile
import uuid
from pathlib import Path

import requests as _requests
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from utils.retry import retry_call

load_dotenv()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
_STORAGE_AUDIO_MARKER = "/storage/v1/object/public/audio/"
_STORAGE_VIDEO_MARKER = "/storage/v1/object/public/videos/"
_STORAGE_THUMB_MARKER = "/storage/v1/object/public/thumbnails/"


def _get_youtube_service():
    """Build an authenticated YouTube Data API v3 service from env credentials."""
    client_id = os.getenv("YOUTUBE_CLIENT_ID")
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET")
    refresh_token = os.getenv("YOUTUBE_REFRESH_TOKEN")

    if not all([client_id, client_secret, refresh_token]):
        raise EnvironmentError(
            "YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET and YOUTUBE_REFRESH_TOKEN must be set."
        )

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=_YOUTUBE_SCOPES,
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)


# ---------------------------------------------------------------------------
# Storage download helper
# ---------------------------------------------------------------------------

def _download_from_storage(url: str, local_path: str, bucket: str, marker: str) -> None:
    """Download a file from Supabase Storage (SDK) or plain HTTP as fallback."""
    if marker in url:
        storage_path = url.split(marker, 1)[1].split("?")[0]
        from utils.supabase_client import get_client
        data = get_client().storage.from_(bucket).download(storage_path)
        with open(local_path, "wb") as f:
            f.write(data)
    else:
        resp = _requests.get(url, timeout=120)
        resp.raise_for_status()
        with open(local_path, "wb") as f:
            f.write(resp.content)


# ---------------------------------------------------------------------------
# Upload + thumbnail helpers
# ---------------------------------------------------------------------------

def _upload_video(youtube, video_path: str, title: str, description: str, tags: list[str]) -> str:
    """Upload video to YouTube as private. Returns youtube_video_id."""
    tag_list = [t.strip() for t in tags if t.strip()]

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tag_list,
            "categoryId": "22",  # People & Blogs — override in channel settings if needed
        },
        "status": {
            "privacyStatus": "private",
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True, chunksize=8 * 1024 * 1024)

    def _call() -> str:
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )
        response = None
        while response is None:
            _, response = request.next_chunk()
        return response["id"]

    return retry_call(_call, max_attempts=2, base_delay=5.0, exceptions=(Exception,))


def _set_thumbnail(youtube, video_id: str, thumbnail_path: str) -> None:
    """Upload thumbnail PNG for an existing YouTube video."""
    media = MediaFileUpload(thumbnail_path, mimetype="image/png", resumable=False)
    youtube.thumbnails().set(videoId=video_id, media_body=media).execute()


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def publish_job(video_job_id: str) -> dict:
    """
    Upload a completed video job to YouTube and update Supabase.

    Args:
        video_job_id: UUID of the video_jobs row.

    Returns:
        dict with youtube_video_id and youtube_url.

    Raises:
        ValueError:       If required fields are missing from the job.
        EnvironmentError: If YouTube API credentials are not set.
    """
    from utils.supabase_client import get_client
    supabase = get_client()

    # 1. Fetch job
    result = (
        supabase.table("video_jobs")
        .select("video_url, thumbnail_url, seo_title, seo_description, seo_tags, channel_id")
        .eq("id", video_job_id)
        .single()
        .execute()
    )
    job = result.data
    if not job:
        raise ValueError(f"Job {video_job_id} niet gevonden")

    for field in ("video_url", "seo_title", "seo_description"):
        if not job.get(field):
            raise ValueError(f"Verplicht veld '{field}' ontbreekt voor job {video_job_id}")

    seo_title = job["seo_title"]
    seo_description = job["seo_description"]
    raw_tags = job.get("seo_tags") or ""
    tags = [t.strip() for t in raw_tags.split(",") if t.strip()]

    print(f"Publishing: '{seo_title}'")

    tmpdir = tempfile.mkdtemp(prefix=f"publish_{video_job_id[:8]}_")
    try:
        # 2. Auth — not retried
        youtube = _get_youtube_service()

        # 3. Download video from Supabase Storage
        video_path = os.path.join(tmpdir, f"video_{uuid.uuid4().hex}.mp4")
        print("Video downloaden van Supabase Storage…")
        _download_from_storage(
            job["video_url"], video_path, "videos", _STORAGE_VIDEO_MARKER
        )
        size_mb = Path(video_path).stat().st_size // (1024 * 1024)
        print(f"Video gedownload: {size_mb} MB")

        # 4. Upload video (with retry)
        print("Video uploaden naar YouTube (privé)…")
        youtube_video_id = _upload_video(youtube, video_path, seo_title, seo_description, tags)
        youtube_url = f"https://www.youtube.com/watch?v={youtube_video_id}"
        print(f"Geüpload: {youtube_url}")

        # 5. Set thumbnail (best-effort — don't fail the whole job if it errors)
        if job.get("thumbnail_url"):
            try:
                thumb_path = os.path.join(tmpdir, f"thumb_{uuid.uuid4().hex}.png")
                _download_from_storage(
                    job["thumbnail_url"], thumb_path, "thumbnails", _STORAGE_THUMB_MARKER
                )
                _set_thumbnail(youtube, youtube_video_id, thumb_path)
                print("Thumbnail ingesteld")
            except Exception as exc:
                logger.warning("Thumbnail instellen mislukt (niet fataal): %s", exc)

        # 6. Update DB — success
        supabase.table("video_jobs").update({
            "youtube_video_id": youtube_video_id,
            "youtube_url": youtube_url,
            "status": "PUBLISHED",
        }).eq("id", video_job_id).execute()

        print(f"Job {video_job_id} -> PUBLISHED")
        return {"youtube_video_id": youtube_video_id, "youtube_url": youtube_url}

    except Exception as exc:
        logger.error("Publishing mislukt voor job %s: %s", video_job_id, exc)
        supabase.table("video_jobs").update({
            "status": "PUBLISH_FAILED",
            "error_message": str(exc)[:500],
        }).eq("id", video_job_id).execute()
        raise

    finally:
        Path(tmpdir).exists() and __import__("shutil").rmtree(tmpdir, ignore_errors=True)
