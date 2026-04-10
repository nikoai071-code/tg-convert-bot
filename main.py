import asyncio
import logging
import os
import shutil
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramConflictError
from aiogram.fsm.storage.memory import MemoryStorage

from config import settings
from handlers import register_handlers


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )


def _prepare_runtime_cookies() -> None:
    log = logging.getLogger(__name__)
    env_cookies = (settings.instagram_cookies or "").strip()
    if env_cookies:
        settings.runtime_cookies_path.write_text(env_cookies, encoding="utf-8")
        log.info("Cookies из INSTAGRAM_COOKIES записаны в %s", settings.runtime_cookies_path)
    else:
        try:
            settings.runtime_cookies_path.unlink(missing_ok=True)
        except OSError:
            pass


def _check_ffmpeg_available() -> None:
    log = logging.getLogger(__name__)
    ffmpeg_bin = os.environ.get("FFMPEG_BINARY") or shutil.which("ffmpeg") or "ffmpeg"
    if os.path.isfile(ffmpeg_bin) and os.access(ffmpeg_bin, os.X_OK):
        log.info("ffmpeg: %s", ffmpeg_bin)
        return
    if shutil.which(ffmpeg_bin):
        log.info("ffmpeg в PATH: %s", ffmpeg_bin)
        return
    log.warning("ffmpeg не найден (укажите FFMPEG_BINARY или установите ffmpeg в PATH): %s", ffmpeg_bin)


async def run_bot() -> None:
    setup_logging()
    log = logging.getLogger(__name__)
    log.info("Старт бота, cwd=%s", os.getcwd())
    _prepare_runtime_cookies()
    _check_ffmpeg_available()
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    register_handlers(dp)
    await dp.start_polling(bot)


def main() -> None:
    try:
        asyncio.run(run_bot())
    except TelegramConflictError:
        logging.getLogger(__name__).error(
            "TelegramConflictError: с этим BOT_TOKEN уже запущен другой polling "
            "(второй деплой, локальный процесс или другой сервис). Остановите лишние экземпляры."
        )
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Бот остановлен")


if __name__ == "__main__":
    main()
