"""utils/supabase_upload.py — Centrale Supabase storage upload met retries."""
import logging
import time
from typing import Literal

from supabase import Client

logger = logging.getLogger(__name__)


def upload_to_bucket(
    supabase: Client,
    bucket: Literal["audio", "videos", "thumbnails"],
    local_path: str,
    storage_path: str,
    content_type: str,
    max_attempts: int = 3,
) -> str:
    """Robuuste upload met retries voor netwerk/SSL fouten. Gebruik voor ALLE buckets."""
    with open(local_path, "rb") as f:
        data = f.read()

    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            logger.info(
                "Upload %s: %s (attempt %d/%d)",
                bucket, storage_path, attempt, max_attempts,
            )

            try:
                supabase.storage.from_(bucket).remove([storage_path])
            except Exception as exc:
                logger.debug("Ignore remove error for %s: %r", storage_path, exc)

            supabase.storage.from_(bucket).upload(
                path=storage_path,
                file=data,
                file_options={"content-type": content_type},
            )

            url = supabase.storage.from_(bucket).get_public_url(storage_path)
            logger.info("%s uploaded OK: %s", bucket, url)
            return url

        except Exception as exc:
            last_exc = exc
            logger.warning(
                "Upload %s failed (attempt %d/%d): %r",
                bucket, attempt, max_attempts, exc,
            )
            time.sleep(2 * attempt)

    raise RuntimeError(
        f"{bucket} upload failed after {max_attempts} attempts: {last_exc!r}"
    )
