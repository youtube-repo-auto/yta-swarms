import os
import uuid
import tempfile
import re
import logging
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client
import kokoro_onnx
import soundfile as sf

from utils.supabase_upload import upload_to_bucket

logger = logging.getLogger(__name__)
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

DEFAULT_VOICE = "am_michael"
KOKORO_MODEL = os.path.join(os.path.dirname(__file__), "../kokoro-v1.0.int8.onnx")
KOKORO_VOICES = os.path.join(os.path.dirname(__file__), "../voices-v1.0.bin")


def _clean_script_for_tts(text: str) -> str:
    text = re.sub(r"\[.*?\]", "", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^-{3,}\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*{1,3}(.*?)\*{1,3}", r"\1", text)
    text = text.replace("(PAUZE)", "...")
    text = text.replace("(PAUSE)", "...")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def generate_audio(script_text: str, voice: str = DEFAULT_VOICE) -> str:
    kokoro = kokoro_onnx.Kokoro(KOKORO_MODEL, KOKORO_VOICES)
    samples, sr = kokoro.create(script_text, voice=voice, speed=1.0, lang="en-us")
    output_path = os.path.join(tempfile.gettempdir(), f"voice_{uuid.uuid4().hex}.wav")
    sf.write(output_path, samples, sr)
    size_kb = Path(output_path).stat().st_size // 1024
    print(f"Audio generated: {output_path} ({size_kb} KB)")
    return output_path


def upload_to_supabase(local_path: str, storage_path: str, max_attempts: int = 3) -> str:
    return upload_to_bucket(supabase, "audio", local_path, storage_path, "audio/wav", max_attempts)


def generate_voice_for_job(video_job_id: str, voice: str = DEFAULT_VOICE) -> str:
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
    cleaned_script_text = _clean_script_for_tts(script_text)
    word_count = len(cleaned_script_text.split())
    print(f"Script: '{job['title_concept']}' — {word_count} words (after cleanup)")

    audio_file = generate_audio(cleaned_script_text, voice)

    storage_path = f"{video_job_id}.wav"
    voice_url = upload_to_supabase(audio_file, storage_path)

    supabase.table("video_jobs").update({
        "status": "VOICE_GENERATED",
        "voice_url": voice_url,
        "script_word_count": word_count,
    }).eq("id", video_job_id).execute()

    print(f"OK Job {video_job_id} -> VOICE_GENERATED")
    Path(audio_file).unlink(missing_ok=True)
    return voice_url
