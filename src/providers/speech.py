"""Text-to-speech providers. One today: OpenRouter's `POST /api/v1/audio/speech` (OpenAI's speech API shape: model,
input, voice, response_format). Gemini TTS answers only raw PCM, so every request asks for `pcm` and the answer is
wrapped in a WAV header here (24 kHz, 16-bit, mono: Gemini's output, assumed until a real call confirms it). The
pipeline that uses this lives in `src/generation/speech.py`; HTTP stays behind `src/providers/`."""

from __future__ import annotations

import struct
from typing import Any, Protocol

from . import tracing
from .interface import ProviderError
from .openrouter import OpenRouterHttp
from .secrets import Secret

SPEECH_PATH = "/v1/audio/speech"  # OpenRouterHttp's base is https://openrouter.ai/api
RATE = 24000
CHANNELS = 1
WIDTH = 2  # bytes a sample: 16-bit


def pcm_to_wav(pcm: bytes, rate: int = RATE, channels: int = CHANNELS, width: int = WIDTH) -> bytes:
    """Raw little-endian PCM as a playable WAV file: the 44-byte RIFF header, then the samples."""
    block = channels * width
    header = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(pcm), b"WAVE", b"fmt ", 16, 1, channels, rate,
                         rate * block, block, width * 8, b"data", len(pcm))
    return header + pcm


class SpeechProvider(Protocol):
    async def speak(self, key: Secret, model: str, voice: str, text: str) -> bytes: ...


class OpenRouterSpeech:
    def __init__(self, *, timeout: float = 120.0, transport: Any = None) -> None:
        self.timeout = timeout
        self.transport = transport  # tests pass an httpx.MockTransport

    async def speak(self, key: Secret, model: str, voice: str, text: str) -> bytes:
        """The text spoken by `model` in `voice`, as WAV bytes."""
        payload = {"model": model, "input": text, "voice": voice, "response_format": "pcm"}
        with tracing.observe("speak", as_type="generation", model=model, input=text, model_parameters={"voice": voice}) as gen:
            http = OpenRouterHttp(key, timeout=self.timeout, transport=self.transport)
            try:
                pcm = await http.post_bytes(SPEECH_PATH, payload, what="speech")
            finally:
                await http.aclose()
            if not pcm:
                raise ProviderError(f"{model} returned no audio")
            gen.update(output={"bytes": len(pcm)})
        return pcm_to_wav(pcm)


PROVIDERS: dict[str, type] = {"openrouter": OpenRouterSpeech}


def speech_provider(kind: str, **kwargs: Any) -> SpeechProvider | None:
    """The provider registered as `kind`, or None for an unknown one."""
    cls = PROVIDERS.get(kind)
    return cls(**kwargs) if cls else None
