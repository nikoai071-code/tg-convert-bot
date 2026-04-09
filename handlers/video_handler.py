import logging
import shutil
import uuid
from pathlib import Path

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import settings
from .video_note import process_video_to_note
from .video_to_voice import process_video_to_voice

logger = logging.getLogger(__name__)
router = Router(name="video")

ACTION_NOTE = "video_action:note"
ACTION_VOICE = "video_action:voice"
STATE_KEY_VIDEO_ID = "pending_video_file_id"


def _video_actions_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔵 Кружочек", callback_data=ACTION_NOTE),
                InlineKeyboardButton(text="🎵 Голосовое", callback_data=ACTION_VOICE),
            ]
        ]
    )


@router.message(F.video)
async def on_video_received(message: Message, state: FSMContext) -> None:
    if not message.video:
        return
    try:
        await state.update_data(**{STATE_KEY_VIDEO_ID: message.video.file_id})
        await message.answer(
            "Что сделать с видео?",
            reply_markup=_video_actions_keyboard(),
        )
    except Exception:
        logger.exception("Ошибка при подготовке действий для видео")
        await message.answer("Не удалось обработать видео. Попробуйте ещё раз.")


@router.callback_query(F.data.in_({ACTION_NOTE, ACTION_VOICE}))
async def on_video_action_selected(query: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    video_file_id = data.get(STATE_KEY_VIDEO_ID)
    if not video_file_id:
        await query.answer("Сначала отправьте видео", show_alert=True)
        return

    await query.answer()
    chat_id = query.message.chat.id if query.message else query.from_user.id
    status_msg = None
    work_dir = settings.tmp_dir / f"video_{uuid.uuid4().hex}"
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        if query.message:
            status_msg = await query.message.answer("⏳ Обрабатываю...")

        if query.data == ACTION_NOTE:
            await process_video_to_note(query.bot, chat_id, video_file_id, work_dir)
        elif query.data == ACTION_VOICE:
            await process_video_to_voice(query.bot, chat_id, video_file_id, work_dir)
    except Exception:
        logger.exception("Ошибка обработки видео действия")
        await query.bot.send_message(chat_id, "Произошла ошибка при обработке видео.")
    finally:
        await state.update_data(**{STATE_KEY_VIDEO_ID: None})
        if status_msg is not None:
            try:
                await status_msg.delete()
            except Exception:
                pass
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass
