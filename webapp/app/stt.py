"""STT (Speech-to-Text) via GigaAM Voice API (OpenAI-compatible)."""
import asyncio
import logging
import os

import httpx

log = logging.getLogger("stt")

STT_API_URL = os.environ.get("STT_API_URL", "").strip()
STT_API_KEY = os.environ.get("STT_API_KEY", "").strip()


async def transcribe(file_bytes: bytes, filename: str = "voice.oga") -> str | None:
    """Transcribe audio bytes via GigaAM API.

    Returns transcribed text or None if STT is not configured or fails.
    Uses wait_for_result=true for sync mode; falls back to polling on 202.
    """
    if not STT_API_URL or not STT_API_KEY:
        log.warning("STT not configured (STT_API_URL/STT_API_KEY missing)")
        return None

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            files = {"file": (filename, file_bytes, "audio/ogg")}
            data = {
                "model": "gigaam-e2e",
                "language": "ru",
                "response_format": "json",
                "wait_for_result": "true",
                "wait_timeout_sec": "60",
            }
            r = await client.post(
                STT_API_URL,
                files=files,
                data=data,
                headers={"Authorization": f"Bearer {STT_API_KEY}"},
            )
            if r.status_code == 200:
                result = r.json()
                text = result.get("text", "")
                log.info(f"STT: transcribed {len(file_bytes)} bytes → '{text[:80]}...'")
                return text
            elif r.status_code == 202:
                job_id = r.json().get("job_id")
                if not job_id:
                    log.error(f"STT: 202 without job_id: {r.text}")
                    return None
                log.info(f"STT: job {job_id} queued, polling...")
                return await _poll_job(job_id)
            else:
                log.error(f"STT error: {r.status_code} {r.text}")
                return None
    except httpx.HTTPError as e:
        log.exception(f"STT HTTP error: {e}")
        return None
    except Exception as e:
        log.exception(f"STT error: {e}")
        return None


async def _poll_job(job_id: str, max_attempts: int = 60, interval: float = 2.0) -> str | None:
    """Poll job status until completed or failed."""
    base_url = STT_API_URL.rsplit("/", 1)[0]
    for attempt in range(max_attempts):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(
                    f"{base_url}/jobs/{job_id}",
                    headers={"Authorization": f"Bearer {STT_API_KEY}"},
                )
                r.raise_for_status()
                data = r.json()
                status = data.get("status")
                if status == "completed":
                    text = data.get("text", "")
                    log.info(f"STT: job {job_id} completed → '{text[:80]}...'")
                    return text
                elif status == "failed":
                    log.error(f"STT job {job_id} failed: {data.get('error')}")
                    return None
                await asyncio.sleep(interval)
        except httpx.HTTPError as e:
            log.warning(f"STT poll attempt {attempt+1} error: {e}")
            await asyncio.sleep(interval)
    log.error(f"STT job {job_id} timed out after {max_attempts * interval}s")
    return None
