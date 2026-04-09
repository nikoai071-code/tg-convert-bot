import logging
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile

from config import settings
from services.ffmpeg_service import convert_video_to_note, download_telegram_file

logger = logging.getLogger(__name__)


async def process_video_to_note(bot: Bot, chat_id: int, video_file_id: str, work_dir: Path) -> None:
    input_path = work_dir / "input.mp4"
    output_path = work_dir / "video_note.mp4"
    await download_telegram_file(bot, video_file_id, input_path)
    await convert_video_to_note(
        input_path=input_path,
        output_path=output_path,
        max_seconds=settings.video_note_max_seconds,
        size=settings.video_note_size,
    )
    await bot.send_video_note(chat_id=chat_id, video_note=FSInputFile(output_path))
    logger.info("Отправлен video note")
