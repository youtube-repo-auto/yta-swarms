import logging
import os
import shutil
import tempfile

import httpx
from googleapiclient.errors import HttpError, ResumableUploadError

from utils.retry import retry_call
from utils.supabase_client import get_client, update_job

logger = logging.getLogger(__name__)

QUOTA_REASON = "uploadLimitExceeded"


def _is_quota_error(exc: Exception) -> bool:
    if QUOTA_REASON in str(exc):
        return True
    if isinstance(exc, (HttpError, ResumableUploadError)):
        try:
            for d in (exc.error_details if hasattr(exc, "error_details") else []) or []:
                if d.get("reason") == QUOTA_REASON:
                    return True
        except Exception:
            pass
    return False


def upload_video(youtube, video_path: str, title: str, description: str, tags: list) -> str:
    from googleapiclient.http import MediaFileUpload

    body = {
        "snippet": {"title": title, "description": description, "tags": tags, "categoryId": "27"},
        "status": {"privacyStatus": "private"},
    }
    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True, chunksize=5 * 1024 * 1024)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    def _execute():
        response = None
        while response is None:
            _, response = request.next_chunk()
        return response["id"]

    return retry_call(_execute, max_attempts=2, base_delay=5.0, exceptions=(Exception,))


def publish_job(job_id: str) -> None:
    supabase = get_client()
    job = supabase.table("video_jobs").select(
        "video_url, thumbnail_url, seo_title, seo_description, seo_tags, channel_id"
    ).eq("id", job_id).single().execute().data

    if not job:
        raise ValueError(f"Job {job_id} niet gevonden")

    seo_tags = job["seo_tags"]
    if isinstance(seo_tags, str):
        import json
        try:
            seo_tags = json.loads(seo_tags)
        except Exception:
            seo_tags = [t.strip() for t in seo_tags.split(",") if t.strip()]

    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=os.environ["YOUTUBE_ACCESS_TOKEN"],
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
    )
    youtube = build("youtube", "v3", credentials=creds)

    tmpdir = tempfile.mkdtemp()
    try:
        logger.info("Publishing: %s", job["seo_title"])

        video_path = os.path.join(tmpdir, f"{job_id}.mp4")
        logger.info("Downloading video from Supabase Storage...")
        with httpx.Client(timeout=300.0) as client:
            r = client.get(job["video_url"])
            r.raise_for_status()
            with open(video_path, "wb") as f:
                f.write(r.content)
        logger.info("Video downloaded: %d MB", os.path.getsize(video_path) // (1024 * 1024))

        try:
            logger.info("Uploading to YouTube (private)...")
            youtube_video_id = upload_video(
                youtube, video_path, job["seo_title"], job["seo_description"], seo_tags
            )
        except Exception as exc:
            if _is_quota_error(exc):
                logger.warning(
                    "YouTube uploadLimitExceeded job %s — status -> SEO_OPTIMIZED, retry morgen", job_id
                )
                update_job(
                    job_id,
                    status="SEO_OPTIMIZED",
                    error_message="YouTube quota exceeded — auto-retry volgende scheduler run",
                )
                return  # Geen crash
            raise

        youtube_url = f"https://youtube.com/watch?v={youtube_video_id}"
        logger.info("Uploaded: %s", youtube_url)

        if job.get("thumbnail_url"):
            thumb_path = os.path.join(tmpdir, f"{job_id}.png")
            with httpx.Client(timeout=120.0) as client:
                r = client.get(job["thumbnail_url"])
                r.raise_for_status()
                with open(thumb_path, "wb") as f:
                    f.write(r.content)
            from googleapiclient.http import MediaFileUpload as MFU
            retry_call(
                lambda: youtube.thumbnails().set(
                    videoId=youtube_video_id,
                    media_body=MFU(thumb_path, mimetype="image/png"),
                ).execute(),
                max_attempts=3, base_delay=2.0, exceptions=(Exception,),
            )
            logger.info("Thumbnail set voor %s", youtube_video_id)

        update_job(
            job_id,
            status="PUBLISHED",
            youtube_video_id=youtube_video_id,
            youtube_url=youtube_url,
            error_message=None,
        )
        logger.info("Job %s — PUBLISHED: %s", job_id, youtube_url)

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
