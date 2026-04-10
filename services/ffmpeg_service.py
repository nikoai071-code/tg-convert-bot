import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Optional

from aiogram import Bot

logger = logging.getLogger(__name__)


def _ffmpeg_bin() -> str:
    return os.environ.get("FFMPEG_BINARY") or shutil.which("ffmpeg") or "ffmpeg"


def _ffprobe_bin() -> str:
    return os.environ.get("FFPROBE_BINARY") or shutil.which("ffprobe") or "ffprobe"


def _ffmpeg_exists(bin_name: str) -> bool:
    if os.path.isfile(bin_name) and os.access(bin_name, os.X_OK):
        return True
    return shutil.which(bin_name) is not None


async def download_telegram_file(bot: Bot, file_id: str, destination: Path) -> None:
    tg_file = await bot.get_file(file_id)
    await bot.download_file(tg_file.file_path, destination=destination)


async def _run_ffmpeg(args: list[str], context: str) -> None:
    bin0 = args[0] if args else "ffmpeg"
    if not _ffmpeg_exists(bin0):
        logger.error("[%s] ffmpeg не найден: %s", context, bin0)
        raise RuntimeError(f"ffmpeg не найден: {bin0}")
    logger.info("[%s] запуск: %s ...", context, bin0)
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = (stderr or b"").decode(errors="replace")[:800]
        logger.error("[%s] ffmpeg rc=%s: %s", context, proc.returncode, err)
        raise RuntimeError(f"ffmpeg ошибка: {err[:400]}")


async def _ffprobe_all_streams(path: Path) -> dict[str, Any]:
    probe = _ffprobe_bin()
    if not _ffmpeg_exists(probe):
        logger.warning("ffprobe не найден, пропускаю анализ потоков")
        return {}
    proc = await asyncio.create_subprocess_exec(
        probe,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        logger.warning(
            "ffprobe rc=%s для %s: %s",
            proc.returncode,
            path.name,
            (err or b"").decode(errors="replace")[:300],
        )
        return {}
    try:
        return json.loads(out.decode())
    except json.JSONDecodeError:
        return {}


def _first_stream(probe: dict[str, Any], codec_type: str) -> Optional[dict[str, Any]]:
    for s in probe.get("streams") or []:
        if s.get("codec_type") == codec_type:
            return s
    return None


def _can_remux_h264_telegram(video: dict[str, Any]) -> bool:
    name = (video.get("codec_name") or "").lower()
    if name not in ("h264", "avc", "avc1"):
        return False
    pix = (video.get("pix_fmt") or "").lower()
    if not pix:
        return False
    if "10" in pix or "422" in pix or "444" in pix or pix.endswith("p10le"):
        return False
    return "420" in pix or pix in ("yuv420p", "yuvj420p") or pix.startswith("yuv420p")


async def prepare_video_for_telegram_mp4(input_path: Path, output_path: Path) -> None:
    """
    Telegram плохо играет HEVC/VP9/AV1 и часть H.264 — даёт звук и белый экран.
    Сначала remux если уже H.264 8-bit 420p, иначе перекод с явным map видео/аудио.
    """
    ctx = "telegram_mp4"
    probe = await _ffprobe_all_streams(input_path)
    video = _first_stream(probe, "video")
    audio = _first_stream(probe, "audio")
    has_audio = audio is not None

    ffmpeg = _ffmpeg_bin()

    if video:
        logger.info(
            "[%s] вход: vcodec=%s pix_fmt=%s audio=%s",
            ctx,
            video.get("codec_name"),
            video.get("pix_fmt"),
            has_audio,
        )

    if video and _can_remux_h264_telegram(video):
        cmd: list[str] = [
            ffmpeg,
            "-hide_banner",
            "-y",
            "-fflags",
            "+genpts",
            "-i",
            str(input_path),
            "-map",
            "0:v:0",
            "-c:v",
            "copy",
            "-movflags",
            "+faststart",
        ]
        if has_audio:
            cmd.extend(["-map", "0:a:0", "-c:a", "copy"])
        else:
            cmd.append("-an")
        cmd.append(str(output_path))
        try:
            await _run_ffmpeg(cmd, ctx + ":remux")
            logger.info("[%s] готово: remux H.264 без перекодирования", ctx)
            return
        except RuntimeError as exc:
            logger.warning("[%s] remux не удался, перекодирую: %s", ctx, exc)

    vf = "scale=trunc(iw*sar/2)*2:trunc(ih/2)*2,setsar=1,format=yuv420p"
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-y",
        "-fflags",
        "+genpts",
        "-i",
        str(input_path),
        "-map",
        "0:v:0",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-profile:v",
        "main",
        "-level",
        "4.0",
        "-preset",
        "fast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
    ]
    if has_audio:
        cmd.extend(["-map", "0:a:0", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"])
    else:
        cmd.append("-an")
    cmd.append(str(output_path))
    await _run_ffmpeg(cmd, ctx + ":transcode")
    logger.info("[%s] готово: перекод H.264 + AAC", ctx)


async def convert_video_to_note(input_path: Path, output_path: Path, max_seconds: int, size: int) -> None:
    ffmpeg = _ffmpeg_bin()
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(input_path),
        "-t",
        str(max_seconds),
        "-vf",
        f"scale={size}:{size}:force_original_aspect_ratio=increase,crop={size}:{size}",
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    await _run_ffmpeg(cmd, "video_note")


async def convert_video_to_voice(input_path: Path, output_path: Path) -> None:
    ffmpeg = _ffmpeg_bin()
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-acodec",
        "libopus",
        "-b:a",
        "64k",
        str(output_path),
    ]
    await _run_ffmpeg(cmd, "video_to_voice")
