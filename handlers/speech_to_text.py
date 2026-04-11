import logging
import shutil
import uuid
from pathlib import Path

from aiogram import F, Router
from aiogram.types import Message

from config import settings
from services.ffmpeg_service import download_telegram_file
from services.groq_service import transcribe_audio_with_groq

logger = logging.getLogger(__name__)
router = Router(name="speech_to_text")


@router.message(F.voice | F.audio)
async def on_voice_or_audio(message: Message) -> None:
    file_id = None
    ext = "ogg"
    if message.voice:
        file_id = message.voice.file_id
        ext = "ogg"
    elif message.audio:
        file_id = message.audio.file_id
        ext = "mp3"

    if not file_id:
        return

    if not settings.groq_api_key:
        await message.answer("Функция распознавания речи не настроена (GROQ_API_KEY не задан).")
        return

    status_msg = None
    work_dir = settings.tmp_dir / f"stt_{uuid.uuid4().hex}"
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        status_msg = await message.answer("⏳ Обрабатываю...")
        input_path = work_dir / f"input.{ext}"
        await download_telegram_file(message.bot, file_id, input_path)
        text = await transcribe_audio_with_groq(input_path)
        if not text.strip():
            await message.answer("Не удалось распознать речь. Попробуйте другой файл.")
            return
        await message.answer(text.strip())
    except Exception:
        logger.exception("Ошибка транскрибации")
        await message.answer("Произошла ошибка при распознавании речи.")
    finally:
        if status_msg is not None:
            try:
                await status_msg.delete()
            except Exception:
                pass
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass
