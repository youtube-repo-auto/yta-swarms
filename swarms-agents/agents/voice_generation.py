import os
import uuid
import asyncio
import tempfile
import edge_tts
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

DEFAULT_VOICE = "en-US-ChristopherNeural"


async def _tts_to_file(text: str, voice: str, output_path: str) -> None:
    communicate = edge_tts.Communicate(text=text, voice=voice)
    await communicate.save(output_path)


def generate_audio(script_text: str, voice: str = DEFAULT_VOICE) -> str:
    output_path = os.path.join(tempfile.gettempdir(), f"voice_{uuid.uuid4().hex}.mp3")
    asyncio.run(_tts_to_file(script_text, voice, output_path))
    size_kb = Path(output_path).stat().st_size // 1024
    print(f"Audio gegenereerd: {output_path} ({size_kb} KB)")
    return output_path


def upload_to_supabase(local_path: str, storage_path: str) -> str:
    with open(local_path, "rb") as f:
        data = f.read()
    try:
        supabase.storage.from_("audio").remove([storage_path])
    except Exception:
        pass
    supabase.storage.from_("audio").upload(
        path=storage_path,
        file=data,
        file_options={"content-type": "audio/mpeg"},
    )
    url = supabase.storage.from_("audio").get_public_url(storage_path)
    return url


def generate_voice_for_job(video_job_id: str, voice: str = DEFAULT_VOICE) -> str:
    # 1. Haal script op
    result = (
        supabase.table("video_jobs")
        .select("script, title_concept")
        .eq("id", video_job_id)
        .single()
        .execute()
    )
    job = result.data
    if not job or not job.get("script"):
        raise ValueError(f"Geen script gevonden voor job {video_job_id}")

    script_text = job["script"]
    word_count = len(script_text.split())
    print(f"Script: '{job['title_concept']}' — {word_count} woorden")

    # 2. Genereer audio
    audio_file = generate_audio(script_text, voice)

    # 3. Upload naar Supabase Storage
    storage_path = f"{video_job_id}.mp3"
    voice_url = upload_to_supabase(audio_file, storage_path)
    print(f"Geüpload: {voice_url}")

    # 4. Update DB: status + fix script_word_count NULL bug
    supabase.table("video_jobs").update({
        "status": "VOICE_GENERATED",
        "voice_url": voice_url,
        "script_word_count": word_count,
    }).eq("id", video_job_id).execute()

    print(f"✓ Job {video_job_id} → VOICE_GENERATED")

    # 5. Cleanup
    Path(audio_file).unlink(missing_ok=True)

    return voice_url
