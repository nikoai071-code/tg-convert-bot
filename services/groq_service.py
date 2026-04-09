from pathlib import Path

import aiohttp

from config import settings


async def transcribe_audio_with_groq(audio_path: Path) -> str:
    headers = {
        "Authorization": f"Bearer {settings.groq_api_key}",
    }
    form = aiohttp.FormData()
    form.add_field("model", settings.groq_whisper_model)
    form.add_field(
        "file",
        audio_path.read_bytes(),
        filename=audio_path.name,
        content_type="audio/ogg",
    )

    timeout = aiohttp.ClientTimeout(total=180)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            settings.groq_transcription_url,
            headers=headers,
            data=form,
        ) as resp:
            if resp.status >= 400:
                body = await resp.text()
                raise RuntimeError(f"Groq API error {resp.status}: {body[:500]}")
            data = await resp.json()

    text = data.get("text")
    if not isinstance(text, str):
        return ""
    return text
