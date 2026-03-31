"""
CTR Tracker Agent
=================
Haalt YouTube Analytics op voor gepubliceerde video's en bepaalt
welke thumbnail variant de hoogste CTR heeft.

Flow:
  1. Vind alle PUBLISHED jobs met youtube_video_id die >24u oud zijn
     en niet recent gecheckt (ctr_last_checked_at > 24u geleden of NULL)
  2. Haal impressions + CTR op via YouTube Analytics API
  3. Sla CTR op in video_jobs (ctr, views, ctr_last_checked_at)
  4. Bepaal best_thumbnail_variant (hoogste CTR van de varianten)

Exports: run_ctr_check()
"""

import logging
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from utils.retry import retry_call
from utils.supabase_client import get_client, update_job

load_dotenv()
logger = logging.getLogger(__name__)


def _build_youtube_clients() -> tuple:
    """Bouw YouTube Data API v3 + YouTube Analytics API clients."""
    creds = Credentials(
        token=os.environ["YOUTUBE_ACCESS_TOKEN"],
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
    )
    yt_data = build("youtube", "v3", credentials=creds)
    yt_analytics = build("youtubeAnalytics", "v2", credentials=creds)
    return yt_data, yt_analytics


def _get_video_analytics(yt_analytics, video_id: str) -> dict | None:
    """
    Haal impressions, clicks en CTR op via YouTube Analytics API.
    Vraagt data op over de laatste 28 dagen.
    """
    end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    start_date = (datetime.now(timezone.utc) - timedelta(days=28)).strftime("%Y-%m-%d")

    try:
        response = retry_call(
            yt_analytics.reports().query(
                ids="channel==MINE",
                startDate=start_date,
                endDate=end_date,
                metrics="views,impressions,impressionClickThroughRate",
                filters=f"video=={video_id}",
            ).execute,
            max_attempts=2,
            base_delay=3.0,
        )

        rows = response.get("rows", [])
        if not rows:
            logger.info("Geen analytics data voor video %s", video_id)
            return None

        row = rows[0]
        return {
            "views": int(row[0]),
            "impressions": int(row[1]),
            "ctr": round(float(row[2]) * 100, 2),  # percentage
        }
    except Exception as e:
        logger.error("Analytics ophalen mislukt voor %s: %s", video_id, e)
        return None


def _get_video_stats_fallback(yt_data, video_id: str) -> dict | None:
    """
    Fallback: haal basisstatistieken op via YouTube Data API v3
    als Analytics API niet beschikbaar is.
    """
    try:
        response = retry_call(
            yt_data.videos().list(
                part="statistics",
                id=video_id,
            ).execute,
            max_attempts=2,
            base_delay=3.0,
        )
        items = response.get("items", [])
        if not items:
            return None
        stats = items[0]["statistics"]
        return {
            "views": int(stats.get("viewCount", 0)),
            "likes": int(stats.get("likeCount", 0)),
        }
    except Exception as e:
        logger.error("Video stats fallback mislukt voor %s: %s", video_id, e)
        return None


def _update_best_variant(supabase, job_id: str, ctr: float) -> None:
    """
    Bepaal en sla de beste thumbnail variant op.
    Omdat we per job maar 1 thumbnail tegelijk live hebben op YouTube,
    slaan we de huidige actieve variant's CTR op en markeren die als beste
    als het de hoogste CTR tot nu toe heeft.
    """
    try:
        # Haal alle varianten op
        res = supabase.table("thumbnail_variants") \
            .select("variant_nr, ctr, is_active") \
            .eq("job_id", job_id) \
            .execute()
        variants = res.data or []

        if not variants:
            logger.info("Geen varianten gevonden voor job %s", job_id)
            return

        # Update CTR van de actieve variant
        active = [v for v in variants if v["is_active"]]
        if active:
            active_nr = active[0]["variant_nr"]
            supabase.table("thumbnail_variants") \
                .update({"ctr": ctr}) \
                .eq("job_id", job_id) \
                .eq("variant_nr", active_nr) \
                .execute()
            logger.info("Variant %d CTR bijgewerkt: %.2f%%", active_nr, ctr)

            # Herlaad met bijgewerkte CTR
            res = supabase.table("thumbnail_variants") \
                .select("variant_nr, ctr") \
                .eq("job_id", job_id) \
                .execute()
            variants = res.data or []

        # Bepaal beste variant (hoogste CTR)
        tested = [v for v in variants if v["ctr"] and v["ctr"] > 0]
        if tested:
            best = max(tested, key=lambda v: v["ctr"])
            update_job(job_id, best_thumbnail_variant=best["variant_nr"])
            logger.info("Beste variant voor job %s: variant %d (CTR %.2f%%)",
                        job_id, best["variant_nr"], best["ctr"])

    except Exception as e:
        # thumbnail_variants tabel bestaat mogelijk nog niet
        logger.warning("Variant CTR update mislukt voor job %s: %s", job_id, e)


def _get_stale_published_jobs(supabase) -> list[dict]:
    """
    Vind PUBLISHED jobs met youtube_video_id die:
    - ctr_last_checked_at is NULL, of
    - ctr_last_checked_at > 24 uur geleden
    Valt terug op alle PUBLISHED jobs als ctr_last_checked_at kolom nog niet bestaat.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    try:
        # Jobs die nog nooit gecheckt zijn
        never_checked = supabase.table("video_jobs") \
            .select("id, youtube_video_id, title_concept") \
            .eq("status", "PUBLISHED") \
            .not_.is_("youtube_video_id", "null") \
            .is_("ctr_last_checked_at", "null") \
            .limit(50) \
            .execute().data or []

        # Jobs die >24u geleden gecheckt zijn
        stale = supabase.table("video_jobs") \
            .select("id, youtube_video_id, title_concept") \
            .eq("status", "PUBLISHED") \
            .not_.is_("youtube_video_id", "null") \
            .lt("ctr_last_checked_at", cutoff) \
            .limit(50) \
            .execute().data or []

        # Dedup op id
        seen = set()
        result = []
        for job in never_checked + stale:
            if job["id"] not in seen:
                seen.add(job["id"])
                result.append(job)
        return result

    except Exception as e:
        if "ctr_last_checked_at" in str(e):
            logger.warning("ctr_last_checked_at kolom bestaat nog niet — "
                           "fallback naar alle PUBLISHED jobs")
            return supabase.table("video_jobs") \
                .select("id, youtube_video_id, title_concept") \
                .eq("status", "PUBLISHED") \
                .not_.is_("youtube_video_id", "null") \
                .limit(50) \
                .execute().data or []
        raise


def run_ctr_check() -> dict:
    """
    Check CTR voor alle gepubliceerde video's die >24u niet gecheckt zijn.

    Returns:
        dict met {checked: int, updated: int, errors: int}
    """
    supabase = get_client()
    jobs = _get_stale_published_jobs(supabase)

    if not jobs:
        logger.info("Geen jobs om te checken — alles is recent bijgewerkt")
        return {"checked": 0, "updated": 0, "errors": 0}

    logger.info("CTR check voor %d jobs", len(jobs))

    yt_data, yt_analytics = _build_youtube_clients()
    stats = {"checked": 0, "updated": 0, "errors": 0}

    for job in jobs:
        job_id = job["id"]
        video_id = job["youtube_video_id"]
        title = (job.get("title_concept") or "")[:50]
        stats["checked"] += 1

        logger.info("Checking %s — %s", video_id, title)

        # Probeer Analytics API (heeft impressions + CTR)
        analytics = _get_video_analytics(yt_analytics, video_id)

        update_fields = {"ctr_last_checked_at": datetime.now(timezone.utc).isoformat()}

        if analytics:
            update_fields["ctr"] = analytics["ctr"]
            update_fields["views"] = analytics["views"]
            logger.info("  CTR: %.2f%% | Views: %d | Impressions: %d",
                        analytics["ctr"], analytics["views"], analytics["impressions"])
            stats["updated"] += 1
        else:
            # Fallback naar Data API (alleen views/likes, geen CTR)
            basic = _get_video_stats_fallback(yt_data, video_id)
            if basic:
                update_fields["views"] = basic["views"]
                update_fields["likes"] = basic.get("likes", 0)
                logger.info("  Views: %d (geen CTR data)", basic["views"])
                stats["updated"] += 1
            else:
                logger.warning("  Geen data beschikbaar")
                stats["errors"] += 1

        try:
            update_job(job_id, **update_fields)
        except Exception as e:
            if "ctr_last_checked_at" in str(e) or "best_thumbnail_variant" in str(e):
                # Kolom bestaat nog niet — update zonder die velden
                update_fields.pop("ctr_last_checked_at", None)
                update_fields.pop("best_thumbnail_variant", None)
                if update_fields:
                    try:
                        update_job(job_id, **update_fields)
                    except Exception as e2:
                        logger.error("  DB update fallback mislukt: %s", e2)
                        stats["errors"] += 1
                        continue
            else:
                logger.error("  DB update mislukt: %s", e)
                stats["errors"] += 1
                continue

        # Update beste variant als we CTR hebben
        if analytics and analytics.get("ctr"):
            _update_best_variant(supabase, job_id, analytics["ctr"])

    logger.info("CTR check klaar: %d gecheckt, %d bijgewerkt, %d fouten",
                stats["checked"], stats["updated"], stats["errors"])
    return stats


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    result = run_ctr_check()
    print(f"\nResultaat: {result}")
