import asyncio
import codecs
import logging
import os
import re
import sys
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

from config import settings
from services.ffmpeg_service import prepare_video_for_telegram_mp4

logger = logging.getLogger(__name__)

URL_RE = re.compile(r"(https?://[^\s<>\[\]()\"']+)", re.IGNORECASE)
# http.cookiejar.MozillaCookieJar._really_load — первая непустая строка файла (на диске)
_MOZILLA_COOKIE_MAGIC = re.compile(r"#( Netscape)? HTTP Cookie File")


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


def _netscape_header_present(lines: list[str]) -> bool:
    for ln in lines[:25]:
        s = ln.strip()
        low = s.lower()
        if s == "# Netscape HTTP Cookie File" or low == "# netscape http cookie file":
            return True
        if s == "# HTTP Cookie File" or low == "# http cookie file":
            return True
    return False


def _netscape_cookie_line_valid(line: str) -> bool:
    """
    Как YoutubeDLCookieJar.prepare_line (yt-dlp): ровно 7 полей через TAB, expires — цифры или пусто.
    """
    s = line.rstrip("\r\n")
    if not s.strip():
        return False
    if s.startswith("#HttpOnly_"):
        s = s[len("#HttpOnly_") :]
    elif s.lstrip().startswith("#"):
        return False
    parts = s.split("\t")
    if len(parts) != 7:
        return False
    _domain, include_sub, _path, secure, expires, _name, _value = parts
    if include_sub.upper() not in ("TRUE", "FALSE") or secure.upper() not in ("TRUE", "FALSE"):
        return False
    if expires and not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", expires):
        return False
    return True


def _file_fully_valid_netscape(raw: str) -> tuple[bool, str]:
    text = raw.lstrip("\ufeff")
    if not text.strip():
        return False, "empty"
    if text.strip().startswith("{") or text.strip().startswith("["):
        return False, "json_like"
    lines = text.splitlines()
    if not _netscape_header_present(lines):
        return False, "missing_netscape_header"
    seen_cookie = False
    for ln in lines:
        if not ln.strip():
            continue
        if ln.lstrip().startswith("#") and not ln.startswith("#HttpOnly_"):
            continue
        if _netscape_cookie_line_valid(ln):
            seen_cookie = True
            continue
        if ln.startswith("#HttpOnly_") or (not ln.lstrip().startswith("#")):
            return False, "invalid_cookie_row"
    if not seen_cookie:
        return False, "no_valid_cookie_rows"
    return True, "ok"


def _safe_to_pass_original_cookie_file(path: Path) -> bool:
    """
    MozillaCookieJar читает первую строку файла с диска: должна совпасть с magic.
    BOM в начале файла / пустая первая строка → первая строка заголовка отбрасывается yt-dlp
    и jar падает с «does not look like a Netscape format cookies file».
    """
    b = path.read_bytes()
    if b.startswith(codecs.BOM_UTF8):
        return False
    idx = 0
    while idx < len(b) and b[idx] in (0x0A, 0x0D):
        idx += 1
    if idx >= len(b):
        return False
    end = b.find(b"\n", idx)
    first = b[idx:end] if end != -1 else b[idx:]
    first = first.strip()
    if not first:
        return False
    try:
        line = first.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return bool(_MOZILLA_COOKIE_MAGIC.search(line))


def _write_sanitized_netscape(raw: str, dest: Path) -> int:
    """Пишет новый файл только с валидными строками cookie (как ожидает yt-dlp)."""
    kept: list[str] = [
        "# Netscape HTTP Cookie File",
        "# Sanitized for yt-dlp (invalid lines removed)",
        "",
    ]
    n = 0
    for ln in raw.splitlines():
        if _netscape_cookie_line_valid(ln):
            kept.append(ln.rstrip("\r\n"))
            n += 1
    if n == 0:
        return 0
    dest.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return n


def _cookies_path_for_ytdlp(work_dir: Path) -> Optional[str]:
    """Railway: runtime_cookies_path; иначе bot/cookies.txt. При «битых» строках — очищенная копия в work_dir."""
    candidates = [
        settings.runtime_cookies_path,
        Path(__file__).resolve().parent.parent / "cookies.txt",
    ]
    clean_path = work_dir / "_ytdlp_cookies.txt"
    for path in candidates:
        if not path.is_file():
            continue
        raw = path.read_text(encoding="utf-8-sig", errors="ignore")
        ok, reason = _file_fully_valid_netscape(raw)
        direct_ok = _safe_to_pass_original_cookie_file(path)
        if ok and direct_ok:
            logger.info("yt-dlp cookies (файл целиком валиден): %s", path)
            return str(path)
        repack_only = ok and not direct_ok
        if repack_only:
            logger.warning(
                "Cookies %s: BOM или первая строка не для MozillaCookieJar — пересобираю → %s",
                path.name,
                clean_path.name,
            )
        n = _write_sanitized_netscape(raw, clean_path)
        if n > 0:
            if repack_only:
                logger.info(
                    "yt-dlp cookies: пересобрано %s строк (без изменения содержимого cookie) → %s",
                    n,
                    clean_path.name,
                )
            else:
                logger.warning(
                    "Файл %s частично невалиден для yt-dlp; оставлены только валидные строки (%s) → %s",
                    path.name,
                    n,
                    clean_path.name,
                )
            return str(clean_path)
        logger.warning("Файл %s: нет валидных строк cookie (reason=%s)", path, reason)
    return None


def _stderr_cookie_rejected(err: str) -> bool:
    low = err.lower()
    return any(
        x in low
        for x in (
            "does not look like a netscape",
            "not netscape formatted",
            "must be netscape",
            "failed to load cookies",
            "invalid length",
            "cookieloaderror",
        )
    )


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

    # ext=mp4 не гарантирует H.264 — часто HEVC. Сначала избегаем hev/av01/vp09, затем общий fallback.
    fmt = (
        "bestvideo[vcodec!^=hev][vcodec!^=hvc][vcodec!^=av01][vcodec!^=vp09]+bestaudio/"
        "bestvideo*+bestaudio/best/b"
    )
    base_args = [
        sys.executable,
        "-m",
        "yt_dlp",
        "-f",
        fmt,
        "--merge-output-format",
        "mp4",
        "--no-playlist",
        "--no-warnings",
        "--retries",
        "3",
        "--fragment-retries",
        "3",
        "--socket-timeout",
        "30",
        "--add-headers",
        "User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "-o",
        out_template,
        url,
    ]

    cookies = _cookies_path_for_ytdlp(Path(work_dir))
    no_cookie = base_args
    if cookies:
        with_cookie = base_args[:-1] + ["--cookies", cookies, url]
        attempts = [with_cookie, no_cookie]
    else:
        attempts = [no_cookie]

    last_err = ""
    saw_cookie_file_reject = False
    for args in attempts:
        uses_cookies = "--cookies" in args
        logger.info("yt-dlp: попытка (cookies=%s)", "да" if uses_cookies else "нет")
        rc, err = await _run_ytdlp(args, timeout_sec=120.0)
        if rc == 0:
            logger.info("yt-dlp ok platform=%s", platform)
            break
        logger.warning("yt-dlp fail rc=%s: %s", rc, err[:600])
        last_err = err
        if _stderr_cookie_rejected(err):
            saw_cookie_file_reject = True
    else:
        logger.error("yt-dlp все попытки провалились: %s", last_err[:800])
        if saw_cookie_file_reject:
            raise RuntimeError("COOKIES_INVALID")
        if _stderr_cookie_rejected(last_err):
            raise RuntimeError("COOKIES_INVALID")
        if "Unsupported URL" in last_err or "unsupported url" in last_err.lower():
            raise RuntimeError("UNSUPPORTED_URL")
        low = last_err.lower()
        if (
            "login required" in low
            or "rate-limit" in low
            or "login_required" in low
            or "checkpoint_required" in low
            or "http error 401" in low
            or "private video" in low
            or "this content is not available" in low
            or "you need to log in" in low
        ):
            raise RuntimeError("LOGIN_REQUIRED")
        raise RuntimeError("DOWNLOAD_FAILED")

    candidates = [
        p for p in Path(work_dir).glob(f"{token}.*")
        if p.suffix.lower() != ".part"
    ]
    if not candidates:
        raise RuntimeError("DOWNLOAD_FAILED")

    chosen = max(candidates, key=lambda p: p.stat().st_mtime)
    out_path = Path(work_dir) / f"{token}_telegram.mp4"
    try:
        await prepare_video_for_telegram_mp4(chosen, out_path)
    except RuntimeError:
        logger.exception("prepare_video_for_telegram_mp4: %s → %s", chosen, out_path)
        raise RuntimeError("FFMPEG_FAILED")
    try:
        chosen.unlink(missing_ok=True)
    except OSError:
        pass

    size = out_path.stat().st_size
    if size > settings.max_download_bytes:
        try:
            out_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError("VIDEO_TOO_LARGE")

    return str(out_path)
