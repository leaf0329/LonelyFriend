from __future__ import annotations

import asyncio
import io
import json
import random
import re
import tempfile
import time
import wave
from pathlib import Path

import httpx

from astrbot.api import logger
from astrbot.api.message_components import Plain, Record
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path


class SiyinSpeech:
    def __init__(self, config: dict):
        self.config = config
        if not 0 <= config["probability"] <= 1 or config["max_chars"] < 1:
            raise ValueError("Invalid Siyin reply policy")
        if config["cooldown_seconds"] < 0 or config["timeout_seconds"] <= 0:
            raise ValueError("Invalid Siyin timing policy")
        self.client = httpx.AsyncClient(
            base_url=config["api_base"], trust_env=False,
            headers={"Authorization": "Bearer " + config["api_key"]},
            timeout=httpx.Timeout(config["timeout_seconds"], connect=1),
        )
        self.next_attempt = {}

    async def close(self):
        await self.client.aclose()

    async def synthesize(self, text: str) -> Path | None:
        try:
            async with asyncio.timeout(self.config["timeout_seconds"]):
                health = await self.client.get("/v1/health", timeout=2)
                health.raise_for_status()
                state = health.json()
                if not state["ok"] or state["active_requests"] or state["engine"].get("queue") or state["engine"].get("reloading"):
                    return None
                data = bytearray()
                async with self.client.stream("POST", "/v1/tts", json={"text": text, "voice": self.config["voice"]}) as response:
                    response.raise_for_status()
                    if response.headers.get("content-type", "").split(";")[0] != "audio/wav":
                        raise ValueError("Siyin did not return WAV audio")
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 8 * 1024 * 1024:
                            raise ValueError("Siyin audio exceeds the reply size limit")
                with wave.open(io.BytesIO(data), "rb") as audio:
                    frames, rate = audio.getnframes(), audio.getframerate()
                    if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or rate not in (24000, 48000):
                        raise ValueError("Unsupported Siyin WAV format")
                    pcm = audio.readframes(frames)
                    if not 0 < frames / rate <= 45 or len(pcm) != frames * 2 or not any(pcm):
                        raise ValueError("Siyin returned empty, truncated or excessive audio")
            directory = Path(get_astrbot_temp_path())
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(prefix="siyin_", suffix=".wav", dir=directory, delete=False) as output:
                output.write(data)
                return Path(output.name)
        except (httpx.HTTPError, TimeoutError, ValueError, wave.Error, EOFError) as error:
            logger.warning("Siyin语音不可用，保留文字：%s", type(error).__name__)
            return None

    async def decorate(self, event, allowed_origins: list[str]):
        result = event.get_result()
        if (event.is_stopped() or event.get_platform_id() != "cuteq_qq" or event.get_group_id()
                or event.unified_msg_origin not in allowed_origins or event.get_extra("cron_payload")
                or result is None or not result.is_llm_result() or not result.chain
                or not all(isinstance(part, Plain) for part in result.chain)):
            return
        text = "\n\n".join(part.text for part in result.chain).strip()
        if not 2 <= len(text) <= self.config["max_chars"] or re.search(r"https?://|```|~~~", text):
            return
        origin = event.unified_msg_origin
        now = time.monotonic()
        if (event.get_extra("lonelyfriend_speech_attempted", False)
                or now < self.next_attempt.get(origin, 0) or random.random() >= self.config["probability"]):
            return
        # Reserve before IO so concurrent replies and an offline desktop cannot cause bursts.
        event.set_extra("lonelyfriend_speech_attempted", True)
        self.next_attempt[origin] = now + self.config["cooldown_seconds"]
        audio = await self.synthesize(text)
        if audio is not None:
            result.chain = [Record.fromFileSystem(audio, text=text)]


def load_siyin(path: Path) -> SiyinSpeech | None:
    return SiyinSpeech(json.loads(path.read_text(encoding="utf-8-sig"))) if path.is_file() else None
