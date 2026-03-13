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
    """Download audio naar tijdelijk bestand via Supabase SDK of HTTP GET.

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
    logger.info("Audio gedownload: %d KB", size_kb)
    return audio_path


def _concatenate_clips(clip_paths: list[str], tmpdir: str) -> str:
    """Plak alle clips aaneen via FFmpeg concat demuxer (geen hercodering)."""
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
    logger.info("%d clips aaneengesloten: %s (%d MB)", len(clip_paths), output_path, size_mb)
    return output_path


def _mux_audio(video_path: str, audio_path: str, tmpdir: str) -> str:
    """
    Combineer video en audio met FFmpeg.
    Audio-lengte bepaalt de eindlengte (-shortest).
    Audio wordt gecodeerd naar AAC 192 kbps.
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
    logger.info("Audio gemixt: %s (%d MB)", output_path, size_mb)
    return output_path


def _upload_video(local_path: str, storage_path: str) -> str:
    """Upload MP4 naar Supabase Storage bucket 'videos', geeft public URL terug."""
    from utils.supabase_client import get_client
    supabase = get_client()

    with open(local_path, "rb") as f:
        data = f.read()

    try:
        supabase.storage.from_("videos").remove([storage_path])
    except Exception:
        pass

    supabase.storage.from_("videos").upload(
        path=storage_path,
        file=data,
        file_options={"content-type": "video/mp4"},
    )

    return supabase.storage.from_("videos").get_public_url(storage_path)


# ---------------------------------------------------------------------------
# LTX mode
# ---------------------------------------------------------------------------

def _generate_clips_ltx(scenes: list[dict], tmpdir: str) -> list[str]:
    """
    Roep ltx_infer.py (Wan2.2 venv) aan voor alle scenes in één subprocess.
    Het model wordt één keer geladen; per clip max 2 pogingen (in ltx_infer.py).
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
            f"ltx_infer.py sloot af met exitcode {result.returncode}. "
            "Controleer bovenstaande output voor details."
        )

    missing = [p for p in clip_paths if not Path(p).exists()]
    if missing:
        raise RuntimeError(f"Verwachte clips ontbreken na generatie: {missing}")

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


def _scene_to_query(scene: dict) -> str:
    """Extract a short Pexels search query (max 4 words) from a scene dict."""
    text = scene.get("image_prompt") or scene.get("motion_prompt") or ""
    words = [w for w in text.split() if w.isalpha()][:4]
    return " ".join(words) if words else _FALLBACK_QUERY


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
        logger.warning("Pexels: geen resultaten voor '%s', fallback naar '%s'", query, _FALLBACK_QUERY)
        video_file = _search(_FALLBACK_QUERY)
    if not video_file:
        raise RuntimeError(
            f"Pexels: geen resultaten voor query '{query}' of fallback '{_FALLBACK_QUERY}'"
        )

    out_path = os.path.join(tmpdir, f"stock_{index:03d}_raw.mp4")
    resp = requests.get(video_file["link"], timeout=120, stream=True)
    resp.raise_for_status()
    with open(out_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8 * 1024 * 1024):
            f.write(chunk)

    size_mb = Path(out_path).stat().st_size // (1024 * 1024)
    logger.info("Pexels clip %d gedownload: %d MB (query: %s)", index, size_mb, query)
    return out_path


def _trim_clip(src: str, duration: float, dst: str) -> None:
    """Trim and re-encode clip to H.264 1080p so all clips are concat-compatible."""
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", src,
            "-t", str(duration),
            "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
                   "pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-an",  # strip audio from stock clip; voice-over added in _mux_audio
            dst,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


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
    Voer de volledige video-generatie uit voor één job.

    Reads VIDEO_MODE from env: "stock" (default) or "ltx".

    Args:
        video_job_id: UUID van de video_jobs rij.

    Returns:
        Publieke URL van de geüploade video in Supabase Storage.

    Raises:
        ValueError:   Als verplichte velden ontbreken in de job.
        RuntimeError: Als clip-generatie of FFmpeg mislukt.
    """
    from utils.supabase_client import get_client
    supabase = get_client()

    video_mode = os.getenv("VIDEO_MODE", "stock").lower()

    # 1. Haal job op
    result = (
        supabase.table("video_jobs")
        .select("title_concept, scene_prompts, voice_url")
        .eq("id", video_job_id)
        .single()
        .execute()
    )
    job = result.data
    if not job:
        raise ValueError(f"Job {video_job_id} niet gevonden in Supabase")

    if not job.get("scene_prompts"):
        raise ValueError(
            f"Geen scene_prompts voor job {video_job_id} — "
            "voer eerst de Scene Generator uit"
        )
    if not job.get("voice_url"):
        raise ValueError(
            f"Geen voice_url voor job {video_job_id} — "
            "voer eerst Voice Generation uit"
        )

    # 2. Parse en sorteer scenes
    raw = job["scene_prompts"]
    scenes: list[dict] = json.loads(raw) if isinstance(raw, str) else raw
    scenes = sorted(scenes, key=lambda s: s.get("index", 0))

    logger.info(
        "Video generatie gestart (mode=%s): '%s' — %d scenes",
        video_mode, job["title_concept"], len(scenes),
    )
    print(
        f"Video generatie gestart (mode={video_mode}): "
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

        # 7. Upload naar Supabase Storage
        storage_path = f"{video_job_id}.mp4"
        video_url = _upload_video(final_video, storage_path)
        logger.info("Geüpload: %s", video_url)

        # 8. Update DB
        supabase.table("video_jobs").update({
            "status": "VIDEO_GENERATED",
            "video_url": video_url,
        }).eq("id", video_job_id).execute()

        logger.info("Job %s -> VIDEO_GENERATED", video_job_id)
        return video_url

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
