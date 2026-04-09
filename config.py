import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Отсутствует обязательная переменная окружения: {name}")
    return value


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    bot_token: str
    groq_api_key: str
    groq_whisper_model: str
    groq_transcription_url: str
    tmp_dir: Path
    video_note_size: int
    video_note_max_seconds: int
    max_download_bytes: int


def get_settings() -> Settings:
    root = Path(__file__).resolve().parent
    tmp_dir = root / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        bot_token=_require_env("BOT_TOKEN"),
        groq_api_key=_require_env("GROQ_API_KEY"),
        groq_whisper_model=os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo"),
        groq_transcription_url="https://api.groq.com/openai/v1/audio/transcriptions",
        tmp_dir=tmp_dir,
        video_note_size=_env_int("VIDEO_NOTE_SIZE", 640),
        video_note_max_seconds=_env_int("VIDEO_NOTE_MAX_SECONDS", 60),
        max_download_bytes=_env_int("MAX_DOWNLOAD_MB", 50) * 1024 * 1024,
    )


settings = get_settings()
