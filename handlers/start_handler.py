from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router(name="start")

_HELP_TEXT = (
    "Привет! Вот что я умею:\n\n"
    "📥 <b>Скачать видео</b> — отправь ссылку из Instagram, TikTok или Pinterest\n"
    "🔵 <b>Кружочек</b> — отправь видео, нажми «Кружочек»\n"
    "🎵 <b>Голосовое</b> — отправь видео, нажми «Голосовое»\n"
    "🎙 <b>Распознать речь</b> — отправь голосовое или аудио\n"
)


@router.message(Command("start", "help"))
async def on_start(message: Message) -> None:
    await message.answer(_HELP_TEXT)
