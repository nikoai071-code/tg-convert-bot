import logging
import os
import shutil
import uuid

from aiogram import F, Router
from aiogram.types import FSInputFile, Message

from config import settings
from services.downloader import (
    detect_platform,
    download_video_with_ytdlp,
    extract_first_url,
)

logger = logging.getLogger(__name__)
router = Router(name="downloader")


@router.message(F.text)
async def on_text_with_link(message: Message) -> None:
    raw = (message.text or "").strip()
    if raw.startswith("/"):
        return

    url = extract_first_url(raw)
    if not url:
        return

    platform = detect_platform(url)
    if platform == "unknown":
        await message.answer("Эта ссылка не поддерживается. Используйте Instagram, Pinterest или TikTok.")
        return

    status_msg = None
    work_dir = settings.tmp_dir / f"dl_{uuid.uuid4().hex}"
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        status_msg = await message.answer("⏳ Обрабатываю...")
        video_path = await download_video_with_ytdlp(url, str(work_dir))
        size = os.path.getsize(video_path)
        if size > settings.max_download_bytes:
            await message.answer("Файл больше 50MB, не могу отправить в Telegram.")
            return
        await message.answer_video(video=FSInputFile(video_path))
    except RuntimeError as exc:
        msg = str(exc)
        if msg == "VIDEO_TOO_LARGE":
            await message.answer("Файл больше 50MB, не могу отправить в Telegram.")
        elif msg == "UNSUPPORTED_URL":
            await message.answer("Ссылка не поддерживается или видео недоступно.")
        elif msg == "FFMPEG_FAILED":
            await message.answer(
                "Не удалось подготовить видео для Telegram (нужен ffmpeg на сервере). "
                "Проверьте деплой на Railway (nixpacks: ffmpeg)."
            )
        elif msg == "LOGIN_REQUIRED":
            await message.answer("Нужен вход в аккаунт или свежие cookies для этой ссылки.")
        elif msg == "COOKIES_INVALID":
            await message.answer(
                "Файл cookies не в формате Netscape (как требует yt-dlp). "
                "Экспортируйте cookies через расширение «Get cookies.txt LOCALLY» или "
                "«cookies.txt», первая строка должна быть: # Netscape HTTP Cookie File"
            )
        else:
            await message.answer("Не удалось скачать видео по ссылке.")
    except Exception:
        logger.exception("Ошибка скачивания по ссылке")
        await message.answer("Произошла ошибка при скачивании видео.")
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
