"""utils/supabase_upload.py — Centrale Supabase storage upload met retries."""
import logging
import os
import time
from pathlib import Path

import httpx
from supabase import Client

logger = logging.getLogger(__name__)

_SUPABASE_URL = os.getenv("SUPABASE_URL", "")
_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

_DIRECT_HTTP_THRESHOLD = 3 * 1024 * 1024  # 3 MB


def _direct_upload_http(bucket: str, local_path: str, storage_path: str, content_type: str) -> str:
    """BYPASS storage3: direct HTTP PUT naar Supabase storage API.

    Leest bestand in memory zodat Content-Length bekend is — voorkomt TCP-reset
    bij grote bestanden op Windows.
    """
    url = f"{_SUPABASE_URL}/storage/v1/object/{bucket}/{storage_path}"
    with open(local_path, "rb") as f:
        data = f.read()
    headers = {
        "Authorization": f"Bearer {_SERVICE_KEY}",
        "Content-Type": content_type,
        "Content-Length": str(len(data)),
    }
    with httpx.Client(
        timeout=300.0,
        http2=False,
        limits=httpx.Limits(max_keepalive_connections=0, max_connections=1),
    ) as client:
        resp = client.put(url, headers=headers, content=data)
        resp.raise_for_status()

    public_url = f"{_SUPABASE_URL}/storage/v1/object/public/{bucket}/{storage_path}"
    logger.info("Direct HTTP %s OK: %s", bucket, public_url)
    return public_url


def upload_to_bucket(
    supabase: Client,
    bucket: str,
    local_path: str,
    storage_path: str,
    content_type: str,
    max_attempts: int = 3,
) -> str:
    """Robuuste upload met retries voor netwerk/SSL fouten. Gebruik voor ALLE buckets.

    Bestanden >3 MB: direct HTTP PUT (bypast storage3 UnboundLocalError bug).
    Kleinere bestanden: storage3 SDK met exponential backoff.
    """
    file_size = Path(local_path).stat().st_size
    logger.info("Upload %s (%d KB): %s", bucket, file_size // 1024, storage_path)

    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            # Verwijder oude versie (negeer fouten)
            try:
                supabase.storage.from_(bucket).remove([storage_path])
            except Exception as exc:
                logger.debug("Ignore remove error for %s: %r", storage_path, exc)

            if file_size > _DIRECT_HTTP_THRESHOLD:
                # Grote bestanden: direct HTTP om storage3 bug te bypassen
                logger.info("Direct HTTP upload (attempt %d/%d)", attempt, max_attempts)
                return _direct_upload_http(bucket, local_path, storage_path, content_type)
            else:
                # Kleine bestanden: storage3 SDK
                logger.info("SDK upload (attempt %d/%d)", attempt, max_attempts)
                with open(local_path, "rb") as f:
                    data = f.read()
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
            time.sleep(5 * attempt)

    raise RuntimeError(
        f"{bucket} upload failed after {max_attempts} attempts: {last_exc!r}"
    )
