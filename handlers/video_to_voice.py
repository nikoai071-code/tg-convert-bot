import logging
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile

from services.ffmpeg_service import convert_video_to_voice, download_telegram_file

logger = logging.getLogger(__name__)


async def process_video_to_voice(bot: Bot, chat_id: int, video_file_id: str, work_dir: Path) -> None:
    input_path = work_dir / "input.mp4"
    output_path = work_dir / "voice.ogg"
    await download_telegram_file(bot, video_file_id, input_path)
    await convert_video_to_voice(input_path=input_path, output_path=output_path)
    await bot.send_voice(chat_id=chat_id, voice=FSInputFile(output_path))
    logger.info("Отправлено голосовое сообщение")
