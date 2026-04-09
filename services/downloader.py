import asyncio
import logging
import os
import re
import sys
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

from config import settings

logger = logging.getLogger(__name__)

URL_RE = re.compile(r"(https?://[^\s<>\[\]()\"']+)", re.IGNORECASE)


def detect_platform(url: str) -> str:
    u = url.lower()
    if "instagram.com" in u or "instagr.am" in u:
        return "instagram"
    if "pinterest.com" in u or "pin.it" in u:
        return "pinterest"
    if "tiktok.com" in u:
        return "tiktok"
    return "unknown"


def extract_first_url(text: str) -> Optional[str]:
    if not text:
        return None
    match = URL_RE.search(text.strip())
    if not match:
        return None
    return match.group(1).rstrip(").,;]")


def _cookies_file() -> Optional[str]:
    path = Path(__file__).parent.parent / "cookies.txt"
    if not path.is_file():
        return None
    raw = path.read_text(encoding="utf-8", errors="ignore").strip()
    if not raw or raw.startswith("{") or raw.startswith("["):
        return None
    return str(path)


async def _run_ytdlp(args: List[str], timeout_sec: float) -> Tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return 124, "timeout"
    err = (stderr or b"").decode(errors="replace")
    return proc.returncode, err


async def download_video_with_ytdlp(url: str, work_dir: str) -> str:
    platform = detect_platform(url)
    if platform == "unknown":
        raise RuntimeError("UNSUPPORTED_URL")

    os.makedirs(work_dir, exist_ok=True)
    token = uuid.uuid4().hex
    out_template = os.path.join(work_dir, f"{token}.%(ext)s")

    base_args = [
        sys.executable, "-m", "yt_dlp",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--no-playlist",
        "--no-warnings",
        "-o", out_template,
        url,
    ]

    cookies = _cookies_file()
    attempts = [base_args]
    if cookies:
        attempts.append(base_args[:-1] + ["--cookies", cookies, url])

    last_err = ""
    for args in attempts:
        rc, err = await _run_ytdlp(args, timeout_sec=120.0)
        if rc == 0:
            logger.info("yt-dlp ok platform=%s", platform)
            break
        logger.warning("yt-dlp fail rc=%s err=%s", rc, err[:300])
        last_err = err
    else:
        if "Unsupported URL" in last_err or "unsupported url" in last_err.lower():
            raise RuntimeError("UNSUPPORTED_URL")
        if "login required" in last_err.lower() or "rate-limit" in last_err.lower():
            raise RuntimeError("LOGIN_REQUIRED")
        raise RuntimeError("DOWNLOAD_FAILED")

    candidates = [
        p for p in Path(work_dir).glob(f"{token}.*")
        if p.suffix.lower() != ".part"
    ]
    if not candidates:
        raise RuntimeError("DOWNLOAD_FAILED")

    chosen = max(candidates, key=lambda p: p.stat().st_mtime)
    size = chosen.stat().st_size
    if size > settings.max_download_bytes:
        try:
            chosen.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError("VIDEO_TOO_LARGE")

    return str(chosen)
