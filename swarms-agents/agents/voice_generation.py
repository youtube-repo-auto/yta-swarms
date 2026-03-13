import os
import uuid
import asyncio
import tempfile
import edge_tts
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client
import re
import logging

from utils.supabase_upload import upload_to_bucket

logger = logging.getLogger(__name__)

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

DEFAULT_VOICE = "en-US-ChristopherNeural"


def _clean_script_for_tts(text: str) -> str:
    """Strip markdown/structure so TTS does not read out #, **, --- or [HOOK] labels."""
    # Remove labels like [HOOK], [INTRO], [POINT 1], etc.
    text = re.sub(r"\[.*?\]", "", text)

    # Remove markdown headers (#, ##, ### at start of line)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)

    # Remove horizontal rules (---)
    text = re.sub(r"^-{3,}\s*$", "", text, flags=re.MULTILINE)

    # Remove bold/italic markers (*, **, *** around text)
    text = re.sub(r"\*{1,3}(.*?)\*{1,3}", r"\1", text)

    # (PAUZE) and (PAUSE) → ellipsis for a natural pause
    text = text.replace("(PAUZE)", "...")
    text = text.replace("(PAUSE)", "...")

    # Max 2 consecutive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


async def _tts_to_file(text: str, voice: str, output_path: str) -> None:
    communicate = edge_tts.Communicate(text=text, voice=voice)
    await communicate.save(output_path)


def generate_audio(script_text: str, voice: str = DEFAULT_VOICE) -> str:
    output_path = os.path.join(tempfile.gettempdir(), f"voice_{uuid.uuid4().hex}.mp3")
    asyncio.run(_tts_to_file(script_text, voice, output_path))
    size_kb = Path(output_path).stat().st_size // 1024
    print(f"Audio generated: {output_path} ({size_kb} KB)")
    return output_path


def upload_to_supabase(local_path: str, storage_path: str, max_attempts: int = 3) -> str:
    """Upload audio-bestand naar Supabase met retries rond netwerk/SSL fouten."""
    return upload_to_bucket(supabase, "audio", local_path, storage_path, "audio/mpeg", max_attempts)


def generate_voice_for_job(video_job_id: str, voice: str = DEFAULT_VOICE) -> str:
    # 1. Fetch script
    result = (
        supabase.table("video_jobs")
        .select("script, title_concept")
        .eq("id", video_job_id)
        .single()
        .execute()
    )
    job = result.data
    if not job or not job.get("script"):
        raise ValueError(f"No script found for job {video_job_id}")

    script_text = job["script"]

    # Clean script for TTS (remove #, **, --- and [HOOK]-style labels)
    cleaned_script_text = _clean_script_for_tts(script_text)

    # Word count based on the text that will actually be spoken
    word_count = len(cleaned_script_text.split())
    print(f"Script: '{job['title_concept']}' — {word_count} words (after cleanup)")

    # 2. Generate audio
    audio_file = generate_audio(cleaned_script_text, voice)

    # 3. Upload to Supabase Storage
    storage_path = f"{video_job_id}.mp3"
    try:
        voice_url = upload_to_supabase(audio_file, storage_path)
    except UnboundLocalError as exc:
        # defensive: bug in storage3 die 'response' niet zet
        raise RuntimeError(
            "Supabase storage upload bug (UnboundLocalError); "
            "waarschijnlijk een onderliggende SSL/connection fout"
        ) from exc

    # 4. Update DB: status + script_word_count
    supabase.table("video_jobs").update({
        "status": "VOICE_GENERATED",
        "voice_url": voice_url,
        "script_word_count": word_count,
    }).eq("id", video_job_id).execute()

    print(f"✓ Job {video_job_id} → VOICE_GENERATED")

    # 5. Cleanup
    Path(audio_file).unlink(missing_ok=True)

    return voice_url
