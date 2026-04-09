import asyncio
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

from config import settings

URL_RE = re.compile(r"(https?://[^\s<>\[\]()\"']+)", re.IGNORECASE)
DEBUG_LOG_PATH = Path("/Users/uvaysjunaydov/Documents/tg bot/.cursor/debug-96a2ba.log")
DEBUG_SESSION_ID = "96a2ba"


# region agent log
def _agent_log(run_id: str, hypothesis_id: str, location: str, message: str, data: dict) -> None:
    payload = {
        "sessionId": DEBUG_SESSION_ID,
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    try:
        with DEBUG_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass


# endregion


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


def _looks_like_netscape_cookies(raw: str) -> bool:
    text = raw.strip()
    if not text:
        return False
    if text.startswith("{") or text.startswith("["):
        return False
    if text.startswith("# Netscape HTTP Cookie File"):
        return True
    return "\t" in text and len(text.splitlines()) >= 1


def _cookies_file(run_id: str) -> Optional[str]:
    local_path = Path(__file__).parent.parent / "cookies.txt"
    if local_path.is_file():
        raw = local_path.read_text(encoding="utf-8", errors="ignore")
        ok = _looks_like_netscape_cookies(raw)
        # region agent log
        _agent_log(
            run_id,
            "H3",
            "downloader.py:_cookies_file",
            "local_cookies_checked",
            {"path": str(local_path), "valid_netscape": ok},
        )
        # endregion
        if ok:
            return str(local_path)
    return None


async def _run_ytdlp(args: list[str], timeout_sec: float) -> tuple[int, str]:
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
    run_id = uuid.uuid4().hex
    platform = detect_platform(url)
    if platform == "unknown":
        raise RuntimeError("UNSUPPORTED_URL")

    os.makedirs(work_dir, exist_ok=True)
    token = uuid.uuid4().hex
    out_template = os.path.join(work_dir, f"{token}.%(ext)s")

    base_args = [
        sys.executable,
        "-m",
        "yt_dlp",
        "-f",
        "bestvideo*+bestaudio/best/b",
        "--merge-output-format",
        "mp4",
        "--no-playlist",
        "--no-warnings",
        "-o",
        out_template,
        url,
    ]

    attempts: list[list[str]] = [base_args]
    cookies = _cookies_file(run_id)
    if cookies:
        attempts.append(base_args[:-1] + ["--cookies", cookies, url])

    # region agent log
    _agent_log(
        run_id,
        "H4",
        "downloader.py:download_video_with_ytdlp",
        "attempts_ready",
        {"platform": platform, "attempts": len(attempts), "has_cookies": bool(cookies)},
    )
    # endregion

    last_err = ""
    for args in attempts:
        rc, err = await _run_ytdlp(args, timeout_sec=120.0)
        if rc == 0:
            # region agent log
            _agent_log(run_id, "H4", "downloader.py:download_video_with_ytdlp", "ytdlp_ok", {})
            # endregion
            break
        last_err = err
    else:
        if "Unsupported URL" in last_err:
            raise RuntimeError("UNSUPPORTED_URL")
        raise RuntimeError("DOWNLOAD_FAILED")

    candidates = [p for p in Path(work_dir).glob(f"{token}.*") if p.suffix.lower() != ".part"]
    if not candidates:
        raise RuntimeError("DOWNLOAD_FAILED")
    chosen = max(candidates, key=lambda p: p.stat().st_mtime)
    size = chosen.stat().st_size
    if size > settings.max_download_bytes:
        raise RuntimeError("VIDEO_TOO_LARGE")
    return str(chosen)
