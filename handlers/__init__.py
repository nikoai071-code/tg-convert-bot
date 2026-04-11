from aiogram import Dispatcher

from .downloader_handler import router as downloader_router
from .speech_to_text import router as speech_router
from .start_handler import router as start_router
from .video_handler import router as video_router


def register_handlers(dp: Dispatcher) -> None:
    dp.include_router(start_router)
    dp.include_router(video_router)
    dp.include_router(speech_router)
    dp.include_router(downloader_router)
