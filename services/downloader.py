import asyncio
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Optional

from config import settings

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
    return str(path) if path.is_file() else None


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
    cookies = _cookies_file()
    if cookies:
        attempts.append(base_args[:-1] + ["--cookies", cookies, url])

    last_err = ""
    for args in attempts:
        rc, err = await _run_ytdlp(args, timeout_sec=120.0)
        if rc == 0:
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
import asyncio
import json
import logging
import os
import re
import shutil
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse
from typing import Any, Dict, List, Optional, Tuple

from config import settings

logger = logging.getLogger(__name__)

# region agent log
_AGENT_DEBUG_LOG = Path("/Users/uvaysjunaydov/Documents/tg bot/.cursor/debug-96a2ba.log")
_AGENT_SESSION = "96a2ba"


def _agent_ndjson(hypothesis_id: str, location: str, message: str, data: Optional[Dict[str, Any]] = None) -> None:
    payload = {
        "sessionId": _AGENT_SESSION,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data or {},
        "timestamp": int(time.time() * 1000),
    }
    try:
        with _AGENT_DEBUG_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass


# endregion

_URL_RE = re.compile(r"(https?://[^\s<>\[\]()\"']+)", re.IGNORECASE)

# Сложные селекторы вроде [width>=height] на Instagram часто ломают merge (нет отдельного audio) — оставляем стабильную цепочку.
INSTAGRAM_YTDLP_FORMAT = "bestvideo*+bestaudio/best/b"
INSTAGRAM_PINTEREST_TIMEOUT_SEC = 120.0
DEFAULT_YTDLP_TIMEOUT_SEC = 45.0


def _telegram_video_crf() -> str:
    raw = (os.getenv("TELEGRAM_VIDEO_CRF") or "16").strip()
    if raw.isdigit() and 0 <= int(raw) <= 51:
        return raw
    return "16"


_PRESETS_FFMPEG = frozenset(
    ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow")
)


def _telegram_video_preset() -> str:
    v = (os.getenv("TELEGRAM_VIDEO_PRESET") or "medium").strip().lower()
    return v if v in _PRESETS_FFMPEG else "medium"


def _ffprobe_bin() -> str:
    return os.environ.get("FFPROBE_BINARY") or shutil.which("ffprobe") or "ffprobe"


async def _ffprobe_video_stream(path: str) -> Optional[Dict[str, str]]:
    probe = _ffprobe_bin()
    try:
        proc = await asyncio.create_subprocess_exec(
            probe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,pix_fmt",
            "-of",
            "json",
            path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return None
    out, _ = await proc.communicate()
    if proc.returncode != 0 or not out:
        return None
    try:
        data = json.loads(out.decode())
        streams = data.get("streams") or []
        if not streams:
            return None
        s0 = streams[0]
        cn = s0.get("codec_name")
        pf = s0.get("pix_fmt")
        if not cn:
            return None
        return {"codec_name": str(cn), "pix_fmt": str(pf or "")}
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


def _h264_8bit_remuxable(codec_name: str, pix_fmt: str) -> bool:
    c = (codec_name or "").lower()
    p = (pix_fmt or "").lower()
    if c not in ("h264", "avc", "avc1"):
        return False
    if not p:
        return False
    if p.endswith("p10le") or p.endswith("p12le") or "yuv422" in p or "yuv444" in p:
        return False
    return p in ("yuv420p", "yuvj420p") or p.startswith("yuv420p")


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
    match = _URL_RE.search(text.strip())
    if not match:
        return None
    return match.group(1).rstrip(").,;]")


def _cookies_file() -> Optional[str]:
    path = Path(__file__).parent.parent / "cookies.txt"
    return str(path) if path.is_file() else None


def _looks_like_tiktok_video_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if "tiktok.com" not in host:
        return True
    if host.startswith("vm.tiktok.com") or host.startswith("vt.tiktok.com"):
        return True
    return "/video/" in path or path.startswith("/t/")


def _ytdlp_launchers() -> List[Tuple[str, List[str]]]:
    """Сначала отдельный бинарник (если есть в PATH), иначе только python -m yt_dlp."""
    out: List[Tuple[str, List[str]]] = []
    for name in ("yt-dlp", "yt-dlp.exe"):
        found = shutil.which(name)
        if found:
            out.append(("bin", [found]))
            break
    out.append(("module", [sys.executable, "-m", "yt_dlp"]))
    return out


def _format_for_platform(platform: str) -> str:
    if platform == "instagram":
        return INSTAGRAM_YTDLP_FORMAT
    return "bv*+ba/b"


def _build_attempts(platform: str, url: str, out_template: str, cookies: Optional[str]) -> List[Tuple[str, List[str]]]:
    fmt = _format_for_platform(platform)
    common_args = [
        "-f", fmt,
        "--format-sort-force",
        "-S",
        "res,size,br",
        "--merge-output-format", "mp4",
        "-o", out_template,
        "--no-playlist",
        "--no-warnings",
        url,
    ]
    attempts: List[Tuple[str, List[str]]] = []
    for launcher_name, launcher in _ytdlp_launchers():
        base = launcher + common_args
        if platform == "tiktok":
            attempts.append((f"{launcher_name}:no_cookies", base))
            if cookies:
                attempts.append((f"{launcher_name}:with_cookies", base + ["--cookies", cookies]))
        elif platform in ("instagram", "pinterest"):
            attempts.append((f"{launcher_name}:no_cookies", base))
            if cookies:
                attempts.append((f"{launcher_name}:with_cookies", base + ["--cookies", cookies]))
        else:
            if cookies:
                attempts.append((f"{launcher_name}:with_cookies", base + ["--cookies", cookies]))
            attempts.append((f"{launcher_name}:no_cookies", base))
    return attempts


def _classify_stderr(platform: str, stderr_text: str) -> Optional[str]:
    low = stderr_text.lower()
    if "python version 3.9" in low and "deprecated" in low and (
        "3.10" in low or "3.11" in low or "above" in low
    ):
        return "YTDLP_PYTHON_TOO_OLD"
    if "unsupported url" in low:
        return "UNSUPPORTED_URL"
    if platform == "tiktok" and "unable to extract webpage video data" in low:
        return "TIKTOK_EXTRACTOR_FAILED"
    if platform == "instagram" and (
        "login required" in low
        or "log in" in low
        or "please log in" in low
        or "rate limit" in low
    ):
        return "INSTAGRAM_LOGIN_OR_LIMIT"
    return None


async def _run_ytdlp_attempts(
    platform: str,
    attempts: List[Tuple[str, List[str]]],
    timeout_sec: float,
) -> None:
    last_err = ""
    for mode, args in attempts:
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            logger.warning("исполняемый файл yt-dlp недоступен mode=%s err=%s", mode, exc)
            # region agent log
            _agent_ndjson(
                "H5",
                "downloader.py:_run_ytdlp_attempts",
                "exec_not_found",
                {"mode": mode, "argv0": args[0] if args else ""},
            )
            # endregion
            last_err = f"not_found:{mode}"
            continue
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            logger.warning("yt-dlp таймаут mode=%s timeout=%s", mode, timeout_sec)
            proc.kill()
            await proc.communicate()
            last_err = f"timeout:{mode}"
            continue
        err = (stderr or b"").decode(errors="replace")
        if proc.returncode == 0:
            logger.info("yt-dlp ok mode=%s", mode)
            # region agent log
            _agent_ndjson("H3", "downloader.py:_run_ytdlp_attempts", "ytdlp_ok", {"mode": mode})
            # endregion
            return
        logger.warning("yt-dlp fail mode=%s rc=%s stderr=%s", mode, proc.returncode, err[:800])
        last_err = err

    code = _classify_stderr(platform, last_err)
    # region agent log
    _agent_ndjson(
        "H3",
        "downloader.py:_run_ytdlp_attempts",
        "ytdlp_all_failed",
        {"classified": code, "last_err_head": (last_err or "")[:400]},
    )
    # endregion
    if code:
        raise RuntimeError(code)
    raise RuntimeError("Не удалось скачать видео по ссылке.")


async def _to_telegram_mp4(input_path: str, output_path: str) -> None:
    """Гибрид: H.264 8-bit 420p — remux без потерь; иначе перекод (CRF и preset из env, по умолчанию CRF 16 + preset medium)."""
    ffmpeg_bin = os.environ.get("FFMPEG_BINARY", "ffmpeg")
    crf = _telegram_video_crf()
    preset = _telegram_video_preset()
    vf_square = "scale=trunc(iw*sar/2)*2:trunc(ih/2)*2,setsar=1,format=yuv420p"

    meta = await _ffprobe_video_stream(input_path)
    if meta and _h264_8bit_remuxable(meta["codec_name"], meta["pix_fmt"]):
        remux_cmd = [
            ffmpeg_bin,
            "-hide_banner",
            "-y",
            "-fflags",
            "+genpts",
            "-i",
            input_path,
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            output_path,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *remux_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            logger.error("ffmpeg не найден (%s): %s", ffmpeg_bin, exc)
            _agent_ndjson("H2", "downloader.py:_to_telegram_mp4", "ffmpeg_exec_missing", {"ffmpeg_bin": ffmpeg_bin})
            raise RuntimeError("FFMPEG_FAILED") from exc

        _, stderr_rm = await proc.communicate()
        if proc.returncode == 0:
            logger.info("ffmpeg: remux H.264 8-bit без перекодирования")
            _agent_ndjson(
                "H2",
                "downloader.py:_to_telegram_mp4",
                "telegram_remux_ok",
                {"codec": meta["codec_name"], "pix_fmt": meta["pix_fmt"]},
            )
            return
        logger.warning(
            "ffmpeg remux не прошёл, перекод: %s",
            (stderr_rm or b"").decode(errors="replace")[:350],
        )

    transcode_cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-y",
        "-fflags",
        "+genpts",
        "-i",
        input_path,
        "-vf",
        vf_square,
        "-c:v",
        "libx264",
        "-profile:v",
        "main",
        "-level",
        "4.1",
        "-preset",
        preset,
        "-crf",
        crf,
        "-c:a",
        "aac",
        "-b:a",
        "256k",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        output_path,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *transcode_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        logger.error("ffmpeg не найден (%s): %s", ffmpeg_bin, exc)
        _agent_ndjson("H2", "downloader.py:_to_telegram_mp4", "ffmpeg_exec_missing", {"ffmpeg_bin": ffmpeg_bin})
        raise RuntimeError("FFMPEG_FAILED") from exc

    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        msg = (stderr or b"").decode(errors="replace")[:500]
        logger.error("ffmpeg ошибка: %s", msg)
        _agent_ndjson("H2", "downloader.py:_to_telegram_mp4", "ffmpeg_failed", {"head": msg[:200]})
        raise RuntimeError("FFMPEG_FAILED")

    logger.info("ffmpeg: перекод H.264 Main + AAC (CRF=%s preset=%s)", crf, preset)
    _agent_ndjson(
        "H2",
        "downloader.py:_to_telegram_mp4",
        "telegram_h264_transcode_ok",
        {"crf": crf, "preset": preset},
    )


async def download_video_with_ytdlp(url: str, work_dir: str) -> str:
    platform = detect_platform(url)
    if platform == "tiktok" and not _looks_like_tiktok_video_url(url):
        raise RuntimeError("TIKTOK_INVALID_URL")

    timeout_sec = (
        INSTAGRAM_PINTEREST_TIMEOUT_SEC if platform in ("instagram", "pinterest") else DEFAULT_YTDLP_TIMEOUT_SEC
    )
    os.makedirs(work_dir, exist_ok=True)
    token = uuid.uuid4().hex
    out_template = os.path.join(work_dir, f"{token}.%(ext)s")
    attempts = _build_attempts(platform, url, out_template, _cookies_file())
    await _run_ytdlp_attempts(platform, attempts, timeout_sec)

    candidates = [p for p in Path(work_dir).glob(f"{token}.*") if p.suffix.lower() != ".part"]
    if not candidates:
        # region agent log
        _agent_ndjson("H2", "downloader.py:download_video_with_ytdlp", "no_output_files", {"token": token})
        # endregion
        raise RuntimeError("DOWNLOAD_OUTPUT_MISSING")

    chosen = max(candidates, key=lambda p: p.stat().st_mtime)
    converted = Path(work_dir) / f"{token}_tg.mp4"
    await _to_telegram_mp4(str(chosen), str(converted))
    try:
        chosen.unlink(missing_ok=True)
    except OSError:
        pass

    size = converted.stat().st_size
    if size > settings.max_download_bytes:
        try:
            converted.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError("VIDEO_TOO_LARGE")

    # region agent log
    _agent_ndjson(
        "H2",
        "downloader.py:download_video_with_ytdlp",
        "download_ok",
        {"size": size, "platform": platform},
    )
    # endregion
    return str(converted)
