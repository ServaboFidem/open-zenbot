"""Reading replies aloud: the OpenRouter speech call and its WAV, the speech settings, the Narrator pipeline and the
`/speech` routes. No test touches the network: OpenRouter is an httpx.MockTransport, the Narrator a fake provider."""

from __future__ import annotations

import json
import struct

import httpx
import pytest

from src.providers.interface import ProviderAuthError, ProviderError
from src.providers.secrets import Secret
from src.providers.speech import OpenRouterSpeech, pcm_to_wav, speech_provider

KEY = "sk-or-v1-speechtestkey0123456789abcdef"
PCM = b"\x00\x01" * 10


def test_pcm_to_wav_writes_a_24khz_mono_16bit_header():
    wav = pcm_to_wav(PCM)
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE" and wav[12:16] == b"fmt "
    channels, rate = struct.unpack("<HI", wav[22:28])
    bits = struct.unpack("<H", wav[34:36])[0]
    assert (channels, rate, bits) == (1, 24000, 16)
    assert wav[36:40] == b"data" and struct.unpack("<I", wav[40:44])[0] == len(PCM) and wav[44:] == PCM
    assert struct.unpack("<I", wav[4:8])[0] == len(wav) - 8


async def test_speak_posts_pcm_request_and_answers_wav():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, content=PCM, headers={"content-type": "application/octet-stream"})

    wav = await OpenRouterSpeech(transport=httpx.MockTransport(handler)).speak(Secret(KEY), "google/gemini-3.8-flash-lite-tts", "Zephyr", "Hello there.")
    assert seen["url"] == "https://openrouter.ai/api/v1/audio/speech"
    assert seen["body"] == {"model": "google/gemini-3.8-flash-lite-tts", "input": "Hello there.", "voice": "Zephyr", "response_format": "pcm"}
    assert seen["auth"] == f"Bearer {KEY}"
    assert wav == pcm_to_wav(PCM)


async def test_speak_maps_a_rejected_key():
    speech = OpenRouterSpeech(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "no"})))
    with pytest.raises(ProviderAuthError):
        await speech.speak(Secret(KEY), "m", "Zephyr", "hi")


async def test_speak_refuses_an_empty_answer():
    speech = OpenRouterSpeech(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"")))
    with pytest.raises(ProviderError, match="no audio"):
        await speech.speak(Secret(KEY), "m", "Zephyr", "hi")


async def test_speak_refuses_a_json_error_body_with_200():
    speech = OpenRouterSpeech(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"error": {"message": "x"}})))
    with pytest.raises(ProviderError, match="no audio"):
        await speech.speak(Secret(KEY), "m", "Zephyr", "hi")


def test_speech_provider_knows_openrouter_only():
    assert isinstance(speech_provider("openrouter"), OpenRouterSpeech)
    assert speech_provider("openai-compatible") is None


async def test_pcm_that_starts_with_a_brace_byte_is_still_audio():
    pcm = b"{\x00" * 8
    speech = OpenRouterSpeech(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=pcm, headers={"content-type": "audio/pcm"})))
    assert await speech.speak(Secret(KEY), "m", "Zephyr", "hi") == pcm_to_wav(pcm)
