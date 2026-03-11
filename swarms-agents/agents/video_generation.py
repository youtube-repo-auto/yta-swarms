# agents/video_generation.py
"""
Video Generation Agent
======================
1. Haal job op uit Supabase (scene_prompts + voice_url).
2. Genereer per scene een korte MP4-clip (~4 s) via LTX-Video (Wan2.2 venv).
3. Concateneer alle clips met FFmpeg tot één stille video.
4. Download de audio (voice_url) en mux over de video met FFmpeg.
5. Upload eindvideo naar Supabase Storage bucket "videos" als {job_id}.mp4.
6. Update video_jobs: video_url, status → VIDEO_GENERATED.

Exporteert: generate_video_for_job(video_job_id: str) -> str
"""
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Wan2.2 venv + inference script paths
# ---------------------------------------------------------------------------

_WAN2_PYTHON = r"C:\Users\MikeDonker\Projects\Wan2.2\venv-wan\Scripts\python.exe"
_LTX_INFER_SCRIPT = r"C:\Users\MikeDonker\Projects\Wan2.2\ltx_infer.py"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _generate_clips(scenes: list[dict], tmpdir: str) -> list[str]:
    """
    Roep ltx_infer.py (Wan2.2 venv) aan voor alle scenes in één subprocess.
    Het model wordt één keer geladen; per clip max 2 pogingen (in ltx_infer.py).
    Geeft gesorteerde lijst van clip-paden terug.
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

    print(f"Genereer {len(scenes)} video clips via LTX-Video …")

    result = subprocess.run(
        [_WAN2_PYTHON, _LTX_INFER_SCRIPT, "--scenes_json", scenes_json_path],
        # stdout/stderr stromen naar console zodat voortgang zichtbaar is
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


def _concatenate_clips(clip_paths: list[str], tmpdir: str) -> str:
    """Plak alle clips aaneen via FFmpeg concat demuxer (geen hercodering)."""
    concat_list_path = os.path.join(tmpdir, "concat.txt")
    with open(concat_list_path, "w", encoding="utf-8") as f:
        for p in clip_paths:
            # FFmpeg wil forward slashes in het concat-bestand
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
    )

    size_mb = Path(output_path).stat().st_size // (1024 * 1024)
    print(f"✓ {len(clip_paths)} clips aaneengesloten → {output_path} ({size_mb} MB)")
    return output_path


def _download_audio(audio_url: str, tmpdir: str) -> str:
    """Download audio van URL naar tijdelijk bestand."""
    audio_path = os.path.join(tmpdir, f"audio_{uuid.uuid4().hex}.mp3")
    resp = requests.get(audio_url, timeout=60)
    resp.raise_for_status()
    with open(audio_path, "wb") as f:
        f.write(resp.content)
    size_kb = Path(audio_path).stat().st_size // 1024
    print(f"Audio gedownload: {size_kb} KB → {audio_path}")
    return audio_path


def _mux_audio(video_path: str, audio_path: str, tmpdir: str) -> str:
    """
    Combineer video en audio met FFmpeg.
    Video-lengte bepaalt de eindlengte (-shortest).
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
    )

    size_mb = Path(output_path).stat().st_size // (1024 * 1024)
    print(f"✓ Audio gemixt → {output_path} ({size_mb} MB)")
    return output_path


def _upload_video(local_path: str, storage_path: str) -> str:
    """Upload MP4 naar Supabase Storage bucket 'videos', geeft public URL terug."""
    from utils.supabase_client import get_client
    supabase = get_client()

    with open(local_path, "rb") as f:
        data = f.read()

    # Verwijder eventueel bestaand bestand (voorkomt upload-conflict)
    try:
        supabase.storage.from_("videos").remove([storage_path])
    except Exception:
        pass

    supabase.storage.from_("videos").upload(
        path=storage_path,
        file=data,
        file_options={"content-type": "video/mp4"},
    )

    url = supabase.storage.from_("videos").get_public_url(storage_path)
    return url


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def generate_video_for_job(video_job_id: str) -> str:
    """
    Voer de volledige video-generatie uit voor één job.

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

    print(f"Video generatie gestart: '{job['title_concept']}' — {len(scenes)} scènes")

    tmpdir = tempfile.mkdtemp(prefix=f"videogen_{video_job_id[:8]}_")

    try:
        # 3. Genereer video clips (LTX-Video via Wan2.2 venv)
        clip_paths = _generate_clips(scenes, tmpdir)

        # 4. Concateneer clips
        silent_video = _concatenate_clips(clip_paths, tmpdir)

        # 5. Download audio + mux
        audio_path = _download_audio(job["voice_url"], tmpdir)
        final_video = _mux_audio(silent_video, audio_path, tmpdir)

        # 6. Upload naar Supabase Storage
        storage_path = f"{video_job_id}.mp4"
        video_url = _upload_video(final_video, storage_path)
        print(f"Geüpload: {video_url}")

        # 7. Update DB
        supabase.table("video_jobs").update({
            "status": "VIDEO_GENERATED",
            "video_url": video_url,
        }).eq("id", video_job_id).execute()

        print(f"✓ Job {video_job_id} → VIDEO_GENERATED")
        return video_url

    finally:
        # Ruim de volledige tmpdir op (clips, concat-list, audio, finale video)
        shutil.rmtree(tmpdir, ignore_errors=True)
