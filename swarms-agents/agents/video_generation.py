# agents/video_generation.py
"""
Video Generation Agent
======================
Supports two modes (VIDEO_MODE env var):

  stock (default)
    1. Download audio from Supabase.
    2. Per scene: search Pexels for stock footage, download best HD clip.
    3. Trim each clip to audio_duration / num_scenes (re-encode H.264 1080p).
    4. Concat trimmed clips, mux with audio.

  ltx
    1. Generate clips via LTX-Video (Wan2.2 venv) — original behaviour.
    2. Concat clips, download audio, mux.

Both modes share _concatenate_clips, _mux_audio, _upload_video, _download_audio.
Final result: video_url in Supabase, status → VIDEO_GENERATED.

Exporteert: generate_video_for_job(video_job_id: str) -> str
"""
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_WAN2_PYTHON = r"C:\Users\MikeDonker\Projects\Wan2.2\venv-wan\Scripts\python.exe"
_LTX_INFER_SCRIPT = str(Path(__file__).parent.parent / "ltx_infer.py")

_PEXELS_SEARCH_URL = "https://api.pexels.com/videos/search"
_FALLBACK_QUERY = "business finance"


# ---------------------------------------------------------------------------
# Shared helpers (both modes)
# ---------------------------------------------------------------------------

def _download_audio(audio_url: str, tmpdir: str) -> str:
    """Download audio to a temp file via Supabase SDK or HTTP GET.

    Supabase Storage public URLs return HTTP 400 when fetched directly.
    Detect the storage URL pattern and use the SDK instead.
    """
    audio_path = os.path.join(tmpdir, f"audio_{uuid.uuid4().hex}.mp3")

    _STORAGE_MARKER = "/storage/v1/object/public/audio/"
    if _STORAGE_MARKER in audio_url:
        storage_path = audio_url.split(_STORAGE_MARKER, 1)[1].split("?")[0]
        from utils.supabase_client import get_client
        data = get_client().storage.from_("audio").download(storage_path)
        with open(audio_path, "wb") as f:
            f.write(data)
    else:
        resp = requests.get(audio_url, timeout=60)
        resp.raise_for_status()
        with open(audio_path, "wb") as f:
            f.write(resp.content)

    size_kb = Path(audio_path).stat().st_size // 1024
    logger.info("Audio downloaded: %d KB", size_kb)
    return audio_path


def _concatenate_clips(clip_paths: list[str], tmpdir: str) -> str:
    """Concatenate all clips via FFmpeg concat demuxer (no re-encode)."""
    concat_list_path = os.path.join(tmpdir, "concat.txt")
    with open(concat_list_path, "w", encoding="utf-8") as f:
        for p in clip_paths:
            f.write(f"file '{p.replace(chr(92), '/')}'\n")

    output_path = os.path.join(tmpdir, "video_silent.mp4")
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", concat_list_path,
            "-c", "copy",
            output_path,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    size_mb = Path(output_path).stat().st_size // (1024 * 1024)
    logger.info("%d clips concatenated: %s (%d MB)", len(clip_paths), output_path, size_mb)
    return output_path


def _mux_audio(video_path: str, audio_path: str, tmpdir: str) -> str:
    """
    Combine video and audio with FFmpeg.
    Audio length determines final length (-shortest).
    Audio is encoded to AAC 192 kbps.
    """
    output_path = os.path.join(tmpdir, f"final_{uuid.uuid4().hex}.mp4")
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", audio_path,
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            output_path,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    size_mb = Path(output_path).stat().st_size // (1024 * 1024)
    logger.info("Audio muxed: %s (%d MB)", output_path, size_mb)
    return output_path


def _upload_video(local_path: str, storage_path: str, max_attempts: int = 3) -> str:
    """Upload MP4 to Supabase Storage bucket 'videos', returns public URL."""
    from utils.supabase_client import get_client
    supabase = get_client()

    with open(local_path, "rb") as f:
        data = f.read()

    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            logger.info(
                "Uploading video to Supabase: %s (attempt %d/%d)",
                storage_path, attempt, max_attempts,
            )

            try:
                supabase.storage.from_("videos").remove([storage_path])
            except Exception as exc:
                logger.debug("Ignore remove error for %s: %r", storage_path, exc)

            supabase.storage.from_("videos").upload(
                path=storage_path,
                file=data,
                file_options={"content-type": "video/mp4"},
            )

            url = supabase.storage.from_("videos").get_public_url(storage_path)
            logger.info("Video uploaded OK: %s", url)
            return url

        except Exception as exc:
            last_exc = exc
            logger.warning(
                "Video upload failed (attempt %d/%d): %r",
                attempt, max_attempts, exc,
            )
            time.sleep(2 * attempt)

    raise RuntimeError(
        f"Supabase video upload failed after {max_attempts} attempts: {last_exc!r}"
    )


# ---------------------------------------------------------------------------
# LTX mode
# ---------------------------------------------------------------------------

def _generate_clips_ltx(scenes: list[dict], tmpdir: str) -> list[str]:
    """
    Call ltx_infer.py (Wan2.2 venv) for all scenes in one subprocess.
    The model is loaded once; max 2 attempts per clip (in ltx_infer.py).
    """
    clip_paths = [
        os.path.join(tmpdir, f"clip_{s['index']:03d}.mp4")
        for s in scenes
    ]

    scenes_payload = [
        {
            "index": s["index"],
            "prompt": s["motion_prompt"],
            "output_path": clip_paths[i],
        }
        for i, s in enumerate(scenes)
    ]

    scenes_json_path = os.path.join(tmpdir, "scenes.json")
    with open(scenes_json_path, "w", encoding="utf-8") as f:
        json.dump(scenes_payload, f, ensure_ascii=False, indent=2)

    logger.info("Genereer %d video clips via LTX-Video", len(scenes))

    result = subprocess.run(
        [_WAN2_PYTHON, _LTX_INFER_SCRIPT, "--scenes_json", scenes_json_path],
        stdout=None,
        stderr=None,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"ltx_infer.py exited with code {result.returncode}. "
            "Check the output above for details."
        )

    missing = [p for p in clip_paths if not Path(p).exists()]
    if missing:
        raise RuntimeError(f"Expected clips missing after generation: {missing}")

    return clip_paths


# ---------------------------------------------------------------------------
# Stock mode (Pexels)
# ---------------------------------------------------------------------------

def _audio_duration(audio_path: str) -> float:
    """Return duration of audio file in seconds using ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            audio_path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


_BANNED_QUERY_WORDS = {
    "cinematic", "dramatic", "elegant", "abstract", "aerial",
    "beautiful", "stunning", "vivid", "sleek", "dynamic",
}


def _normalize_query(text: str) -> str:
    """Lowercase, strip punctuation, remove banned words, collapse whitespace."""
    import re
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)           # remove punctuation
    words = text.split()
    words = [w for w in words if w not in _BANNED_QUERY_WORDS and w.isalpha()]
    return " ".join(words[:5])                      # max 5 words


def _scene_to_query(scene: dict) -> str:
    """Return the best Pexels search query for a scene.

    Priority:
      1. scene['search_query']  — generated with strict noun-only rules
      2. scene['pexels_query']  — legacy field
      3. derive from image_prompt / motion_prompt by stripping banned words
    """
    for key in ("search_query", "pexels_query"):
        raw = scene.get(key, "").strip()
        if raw:
            normalized = _normalize_query(raw)
            if normalized:
                return normalized

    # Fallback: derive from visual prompt
    text = scene.get("image_prompt") or scene.get("motion_prompt") or ""
    derived = _normalize_query(text)
    return derived if derived else _FALLBACK_QUERY


def _pick_video_file(video_files: list[dict]) -> dict | None:
    """Pick best HD MP4 file (width >= 1280) from Pexels video_files list."""
    hd = [
        f for f in video_files
        if f.get("width", 0) >= 1280 and f.get("file_type") == "video/mp4"
    ]
    if hd:
        return max(hd, key=lambda f: f.get("width", 0))
    mp4 = [f for f in video_files if f.get("file_type") == "video/mp4"]
    return mp4[0] if mp4 else None


def _fetch_pexels_video(query: str, tmpdir: str, index: int) -> str:
    """
    Search Pexels for query, download best HD clip to tmpdir.
    Falls back to _FALLBACK_QUERY if no results found.
    Returns local raw file path.
    """
    api_key = os.getenv("PEXELS_API_KEY")
    if not api_key:
        raise EnvironmentError("PEXELS_API_KEY not set in .env")

    def _search(q: str) -> dict | None:
        resp = requests.get(
            _PEXELS_SEARCH_URL,
            headers={"Authorization": api_key},
            params={"query": q, "per_page": 3, "orientation": "landscape"},
            timeout=30,
        )
        resp.raise_for_status()
        for video in resp.json().get("videos", []):
            f = _pick_video_file(video.get("video_files", []))
            if f:
                return f
        return None

    video_file = _search(query)
    if not video_file:
        logger.warning("Pexels: no results for '%s', falling back to '%s'", query, _FALLBACK_QUERY)
        video_file = _search(_FALLBACK_QUERY)
    if not video_file:
        raise RuntimeError(
            f"Pexels: no results for query '{query}' or fallback '{_FALLBACK_QUERY}'"
        )

    out_path = os.path.join(tmpdir, f"stock_{index:03d}_raw.mp4")
    resp = requests.get(video_file["link"], timeout=120, stream=True)
    resp.raise_for_status()
    with open(out_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8 * 1024 * 1024):
            f.write(chunk)

    size_mb = Path(out_path).stat().st_size // (1024 * 1024)
    logger.info("Pexels clip %d downloaded: %d MB (query: %s)", index, size_mb, query)
    return out_path


def _trim_clip(src: str, duration: float, dst: str) -> None:
    """Trim and re-encode clip to H.264 720p so all clips are concat-compatible."""
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", src,
            "-t", str(duration),
            "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,"
                   "pad=1280:720:(ow-iw)/2:(oh-ih)/2",
            "-c:v", "libx264", "-preset", "faster", "-crf", "28",
            "-an",  # strip audio from stock clip; voice-over added in _mux_audio
            dst,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


_COMPRESS_THRESHOLD_BYTES = 45 * 1024 * 1024  # 45 MB


def _compress_final(video_path: str, tmpdir: str) -> str:
    """Re-encode video if larger than 45 MB. Returns path to use (original or compressed)."""
    size = Path(video_path).stat().st_size
    if size <= _COMPRESS_THRESHOLD_BYTES:
        return video_path

    size_mb = size / (1024 * 1024)
    compressed = os.path.join(tmpdir, f"compressed_{uuid.uuid4().hex}.mp4")
    logger.info("Video %.1f MB > 45 MB — compressing to %s", size_mb, compressed)

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", video_path,
            "-c:v", "libx264", "-crf", "30", "-preset", "fast",
            "-c:a", "aac", "-b:a", "128k",
            compressed,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    compressed_mb = Path(compressed).stat().st_size / (1024 * 1024)
    logger.info("Compression complete: %.1f MB -> %.1f MB", size_mb, compressed_mb)
    return compressed


def _generate_clips_stock(scenes: list[dict], audio_path: str, tmpdir: str) -> list[str]:
    """
    Download one Pexels clip per scene, trim to proportional audio duration.
    Returns list of trimmed clip paths in scene order.
    """
    total_duration = _audio_duration(audio_path)
    clip_duration = total_duration / len(scenes)
    logger.info(
        "Stock mode: %d scenes, audio %.1fs, %.1fs per clip",
        len(scenes), total_duration, clip_duration,
    )
    print(
        f"Stock mode: {len(scenes)} scenes, "
        f"audio {total_duration:.1f}s, {clip_duration:.1f}s per clip"
    )

    trimmed_paths = []
    for scene in scenes:
        idx = scene.get("index", scenes.index(scene) + 1)
        query = _scene_to_query(scene)
        print(f"  Scene {idx}: Pexels query='{query}'")
        raw = _fetch_pexels_video(query, tmpdir, idx)
        trimmed = os.path.join(tmpdir, f"clip_{idx:03d}.mp4")
        _trim_clip(raw, clip_duration, trimmed)
        trimmed_paths.append(trimmed)

    return trimmed_paths


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def generate_video_for_job(video_job_id: str) -> str:
    """
    Run the full video generation pipeline for one job.

    Reads VIDEO_MODE from env: "stock" (default) or "ltx".

    Args:
        video_job_id: UUID of the video_jobs row.

    Returns:
        Public URL of the uploaded video in Supabase Storage.

    Raises:
        ValueError:   If required fields are missing from the job.
        RuntimeError: If clip generation or FFmpeg fails.
    """
    from utils.supabase_client import get_client
    supabase = get_client()

    video_mode = os.getenv("VIDEO_MODE", "stock").lower()

    # 1. Fetch job
    result = (
        supabase.table("video_jobs")
        .select("title_concept, scene_prompts, voice_url")
        .eq("id", video_job_id)
        .single()
        .execute()
    )
    job = result.data
    if not job:
        raise ValueError(f"Job {video_job_id} not found in Supabase")

    if not job.get("scene_prompts"):
        raise ValueError(
            f"No scene_prompts for job {video_job_id} — "
            "run Scene Generator first"
        )
    if not job.get("voice_url"):
        raise ValueError(
            f"No voice_url for job {video_job_id} — "
            "run Voice Generation first"
        )

    # 2. Parse en sorteer scenes
    raw = job["scene_prompts"]
    scenes: list[dict] = json.loads(raw) if isinstance(raw, str) else raw
    scenes = sorted(scenes, key=lambda s: s.get("index", 0))

    logger.info(
        "Video generation started (mode=%s): '%s' — %d scenes",
        video_mode, job["title_concept"], len(scenes),
    )
    print(
        f"Video generation started (mode={video_mode}): "
        f"'{job['title_concept']}' — {len(scenes)} scenes"
    )

    tmpdir = tempfile.mkdtemp(prefix=f"videogen_{video_job_id[:8]}_")

    try:
        # 3. Download audio first (stock mode needs duration; ltx mode needs it later anyway)
        audio_path = _download_audio(job["voice_url"], tmpdir)

        # 4. Generate clips
        if video_mode == "ltx":
            clip_paths = _generate_clips_ltx(scenes, tmpdir)
        else:
            clip_paths = _generate_clips_stock(scenes, audio_path, tmpdir)

        # 5. Concat clips
        silent_video = _concatenate_clips(clip_paths, tmpdir)

        # 6. Mux audio
        final_video = _mux_audio(silent_video, audio_path, tmpdir)

        # 7. Compress if > 45 MB
        final_video = _compress_final(final_video, tmpdir)

        # 8. Upload naar Supabase Storage
        storage_path = f"{video_job_id}.mp4"
        video_url = _upload_video(final_video, storage_path)
        logger.info("Uploaded: %s", video_url)

        # 9. Update DB
        supabase.table("video_jobs").update({
            "status": "VIDEO_GENERATED",
            "video_url": video_url,
        }).eq("id", video_job_id).execute()

        logger.info("Job %s -> VIDEO_GENERATED", video_job_id)
        return video_url

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
