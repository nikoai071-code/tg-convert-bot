import asyncio
from pathlib import Path

from aiogram import Bot


async def download_telegram_file(bot: Bot, file_id: str, destination: Path) -> None:
    tg_file = await bot.get_file(file_id)
    await bot.download_file(tg_file.file_path, destination=destination)


async def _run_ffmpeg(args: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = (stderr or b"").decode(errors="replace")[:500]
        raise RuntimeError(f"ffmpeg завершился с ошибкой: {err}")


async def convert_video_to_note(input_path: Path, output_path: Path, max_seconds: int, size: int) -> None:
    cmd = [
        "ffmpeg",
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
    await _run_ffmpeg(cmd)


async def convert_video_to_voice(input_path: Path, output_path: Path) -> None:
    cmd = [
        "ffmpeg",
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
    await _run_ffmpeg(cmd)
