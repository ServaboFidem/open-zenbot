import asyncio
import gc
import subprocess
import sys
import threading
import time
from pathlib import Path

import anyio
import numpy as np
import pytest

from src.generation.speech import VOICE_RE
from src.kokoro import engine as eng
from src.kokoro.engine import EngineFailed, KokoroEngine, NotDownloaded, clean, voice, voices_view
from src.providers.interface import ProviderError


class FakeVoiceLib:
    def __init__(self, samples=None, rate=24000):
        self.calls = []
        self.samples = samples
        self.rate = rate
        self.error = None
        self.delay = 0.0
        self.now = 0
        self.peak = 0
        self._lock = threading.Lock()

    def create(self, text, voice="af_heart", speed=1.0, lang="en-us"):
        self.calls.append({"text": text, "voice": voice, "speed": speed, "lang": lang})
        with self._lock:
            self.now += 1
            self.peak = max(self.peak, self.now)
        try:
            if self.delay:
                time.sleep(self.delay)
            if self.error:
                raise self.error
            samples = self.samples if self.samples is not None else np.full(2400, 0.5, dtype=np.float32)
            return samples, self.rate
        finally:
            with self._lock:
                self.now -= 1


class FakeModel:
    def __init__(self, ready=True, verified=True):
        self._ready = ready
        self._verified = verified

    def ready(self):
        return self._ready

    async def verify(self):
        return self._verified

    def path(self, name):
        return Path("/nowhere") / name


def make(lib=None, model=None, idle_s=600.0):
    lib = lib or FakeVoiceLib()
    loads = []

    def load(m, v):
        loads.append((m, v))
        return lib

    return KokoroEngine(model or FakeModel(), load=load, idle_s=idle_s), lib, loads


def test_there_are_28_voices_and_each_name_passes_voice_re():
    assert len(eng.VOICES) == 28
    assert len({v.name for v in eng.VOICES}) == 28
    assert all(VOICE_RE.match(v.name) for v in eng.VOICES)
    assert eng.VOICES[0].name == "af_heart" and eng.VOICES[0].label == "Heart"
    assert voice("nope") is None
    assert voices_view()[0] == {"name": "af_heart", "label": "Heart", "accent": "American", "gender": "female"}


async def test_british_voices_speak_en_gb_and_american_en_us():
    e, lib, _ = make()
    await e.speak("hi", "bf_emma")
    await e.speak("hi", "am_adam")
    assert [c["lang"] for c in lib.calls] == ["en-gb", "en-us"]
    assert lib.calls[0]["voice"] == "bf_emma" and lib.calls[0]["speed"] == 1.0
    await e.close()


async def test_speak_answers_int16_pcm_of_the_right_length():
    e, _, _ = make()
    pcm = await e.speak("hello", "af_heart")
    assert len(pcm) == 4800
    first = int.from_bytes(pcm[:2], "little", signed=True)
    assert abs(first - 16383) <= 1
    await e.close()


async def test_samples_past_one_are_clipped_not_wrapped():
    e, _, _ = make(FakeVoiceLib(np.array([2.0, -3.0, 0.0], dtype=np.float32)))
    pcm = np.frombuffer(await e.speak("hello", "af_heart"), dtype="<i2")
    assert pcm.tolist() == [32767, -32767, 0]
    await e.close()


async def test_the_library_is_loaded_on_first_use_and_once():
    e, _, loads = make()
    assert not e.loaded and loads == []
    await e.speak("a", "af_heart")
    await e.speak("b", "af_heart")
    assert e.loaded and len(loads) == 1
    assert loads[0][0].name == "kokoro-v1.0.onnx" and loads[0][1].name == "voices-v1.0.bin"
    await e.close()


async def test_two_speaks_never_overlap():
    lib = FakeVoiceLib()
    lib.delay = 0.05
    e, _, _ = make(lib)
    await asyncio.gather(e.speak("a", "af_heart"), e.speak("b", "af_heart"), e.speak("c", "af_heart"))
    assert lib.peak == 1 and len(lib.calls) == 3
    await e.close()


async def test_it_unloads_after_the_idle_time_and_loads_again():
    e, _, loads = make(idle_s=0.05)
    await e.speak("a", "af_heart")
    assert e.loaded
    await asyncio.sleep(0.3)
    assert not e.loaded
    await e.speak("b", "af_heart")
    assert e.loaded and len(loads) == 2
    await e.close()


async def test_unload_waits_for_a_synthesis_in_flight():
    lib = FakeVoiceLib()
    lib.delay = 0.2
    e, _, _ = make(lib)
    await e.ensure()
    task = asyncio.create_task(e.speak("a", "af_heart"))
    await asyncio.sleep(0.05)
    assert lib.now == 1
    await e.unload()
    assert lib.now == 0 and not e.loaded
    assert len(await task) == 4800
    await e.close()


async def test_an_unknown_voice_is_refused_before_loading():
    e, _, loads = make()
    with pytest.raises(ValueError):
        await e.speak("hi", "zz_nobody")
    assert loads == [] and not e.loaded


@pytest.mark.parametrize("model", [FakeModel(ready=False), FakeModel(verified=False)])
async def test_ensure_raises_not_downloaded_when_files_are_missing_or_fail_their_hash(model):
    e, _, loads = make(model=model)
    with pytest.raises(NotDownloaded):
        await e.ensure()
    with pytest.raises(ProviderError):
        await e.speak("hi", "af_heart")
    assert loads == []


async def test_a_load_error_is_engine_failed_naming_the_class_only():
    def load(m, v):
        raise ImportError("secret path /home/x")

    e = KokoroEngine(FakeModel(), load=load)
    with pytest.raises(EngineFailed) as info:
        await e.ensure()
    assert str(info.value) == "ImportError"
    assert isinstance(info.value, ProviderError)
    assert not e.loaded


async def test_a_synthesis_error_is_engine_failed_naming_the_class_only():
    lib = FakeVoiceLib()
    lib.error = ValueError("secret reply text")
    e, _, _ = make(lib)
    with pytest.raises(EngineFailed) as info:
        await e.speak("hello", "af_heart")
    assert "ValueError" in str(info.value) and "secret" not in str(info.value)
    assert info.value.__cause__ is None or "secret" not in str(info.value)
    await e.close()


async def test_a_rate_other_than_24000_is_engine_failed():
    e, _, _ = make(FakeVoiceLib(rate=22050))
    with pytest.raises(EngineFailed):
        await e.speak("hello", "af_heart")
    await e.close()


async def test_a_model_removed_between_two_speaks_fails_the_second_cleanly():
    model = FakeModel()
    e, _, _ = make(model=model)
    await e.speak("a", "af_heart")
    model._ready = False  # remove() ran: the files are gone
    with pytest.raises(NotDownloaded):
        await e.speak("b", "af_heart")
    assert not e.loaded
    await e.close()


def test_clean_removes_controls_and_collapses_space():
    assert clean("  a\u0000b‪c d e \n\t f​g  ") == "abcde fg"
    assert clean("one\ntwo   three") == "one two three"
    assert clean("\x00  ") == ""


async def test_text_empty_after_cleaning_gives_no_audio_and_no_call():
    e, lib, loads = make()
    assert await e.speak(" \x00\n ", "af_heart") == b""
    assert lib.calls == [] and loads == []


def test_importing_the_engine_module_imports_no_kokoro_onnx():
    code = "import src.kokoro.engine, sys; assert 'kokoro_onnx' not in sys.modules and 'onnxruntime' not in sys.modules"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


async def test_ensure_alone_arms_the_idle_timer():
    e, _, _ = make(idle_s=0.05)
    await e.ensure()
    assert e.loaded
    await asyncio.sleep(0.3)
    assert not e.loaded


async def test_a_failed_synthesis_on_a_fresh_load_still_unloads_when_idle():
    lib = FakeVoiceLib()
    lib.error = ValueError("x")
    e, _, _ = make(lib, idle_s=0.05)
    with pytest.raises(EngineFailed):
        await e.speak("hello", "af_heart")
    await asyncio.sleep(0.3)
    assert not e.loaded


async def test_the_guard_fixture_is_not_swallowed_as_engine_failed():
    e = KokoroEngine(FakeModel())  # no load=: the autouse guard stands in for real_load
    with pytest.raises(pytest.fail.Exception):
        await e.ensure()


async def test_a_verify_error_is_engine_failed_naming_the_class_only():
    class Locked(FakeModel):
        async def verify(self):
            raise PermissionError(r"C:\secret\path")

    e, _, loads = make(model=Locked())
    with pytest.raises(EngineFailed) as info:
        await e.ensure()
    assert "PermissionError" in str(info.value) and "secret" not in str(info.value)
    assert loads == []


# -- a caller cancelled the way the route's is: Starlette's anyio task group (ASGI spec 2.3) --------------------

async def _until(check, limit=5.0):
    end = time.monotonic() + limit
    while not check():
        assert time.monotonic() < end, "timed out"
        await asyncio.sleep(0.005)


async def _cancel_as_anyio_does(call, started):
    """Run `call()` in an anyio task group and cancel the group's scope once `started()` is true, as Starlette's
    StreamingResponse does when the client goes away. The scope delivers the cancel again on every loop turn, so a
    cancelled coroutine cannot await anything afterwards."""
    async with anyio.create_task_group() as tg:
        tg.start_soon(call)
        await asyncio.sleep(0.02)  # the call is under way (or waiting for the lock) before the check
        await _until(started)
        tg.cancel_scope.cancel()


async def test_a_speak_cancelled_by_a_task_group_keeps_the_lock_until_its_thread_ends():
    lib = FakeVoiceLib()
    lib.delay = 0.3
    e, _, _ = make(lib)
    await e.ensure()
    await _cancel_as_anyio_does(lambda: e.speak("a", "af_heart"), lambda: lib.now == 1)
    assert lib.now == 1  # the caller has gone; its thread has not
    await e.speak("b", "af_heart")
    assert lib.peak == 1 and [c["text"] for c in lib.calls] == ["a", "b"]
    await e.close()


async def test_unload_after_a_cancelled_speak_waits_for_its_thread():
    lib = FakeVoiceLib()
    lib.delay = 0.3
    e, _, _ = make(lib)
    await e.ensure()
    await _cancel_as_anyio_does(lambda: e.speak("a", "af_heart"), lambda: lib.now == 1)
    await e.unload()
    assert lib.now == 0 and not e.loaded
    await e.close()


def _slow_load(lib, delay=0.3):
    state = {"loads": 0, "now": 0, "peak": 0}
    guard = threading.Lock()

    def load(m, v):
        with guard:
            state["loads"] += 1
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        try:
            time.sleep(delay)
            return lib
        finally:
            with guard:
                state["now"] -= 1
    return load, state


async def test_a_load_cancelled_by_a_task_group_is_kept_and_never_loaded_twice():
    lib = FakeVoiceLib()
    load, state = _slow_load(lib)
    e = KokoroEngine(FakeModel(), load=load)
    await _cancel_as_anyio_does(e.ensure, lambda: state["now"] == 1)
    assert state["now"] == 1
    await e.speak("a", "af_heart")
    assert state["loads"] == 1 and state["peak"] == 1 and e.loaded
    await e.close()


async def test_unload_after_a_cancelled_load_waits_for_the_load_to_end():
    lib = FakeVoiceLib()
    load, state = _slow_load(lib)
    e = KokoroEngine(FakeModel(), load=load)
    await _cancel_as_anyio_does(e.ensure, lambda: state["now"] == 1)
    await e.unload()
    assert state["now"] == 0 and state["loads"] == 1 and not e.loaded
    await e.close()


async def test_a_failure_whose_caller_was_cancelled_reaches_no_exception_handler():
    loop = asyncio.get_running_loop()
    seen: list = []
    loop.set_exception_handler(lambda _loop, context: seen.append(context))
    try:
        lib = FakeVoiceLib()
        lib.delay = 0.2
        lib.error = ValueError("secret reply text")
        e, _, _ = make(lib)
        await e.ensure()
        await _cancel_as_anyio_does(lambda: e.speak("a", "af_heart"), lambda: lib.now == 1)
        await _until(lambda: lib.now == 0)
        await e.unload()
        await e.close()
        for _ in range(5):
            await asyncio.sleep(0.01)
        gc.collect()
        await asyncio.sleep(0)
        assert seen == []
    finally:
        loop.set_exception_handler(None)


async def test_a_speak_cancelled_while_it_waits_for_the_lock_never_starts_its_synthesis():
    lib = FakeVoiceLib()
    lib.delay = 0.3
    e, _, _ = make(lib)
    await e.ensure()
    first = asyncio.create_task(e.speak("a", "af_heart"))
    await _until(lambda: lib.now == 1)
    await _cancel_as_anyio_does(lambda: e.speak("b", "af_heart"), lambda: True)
    assert len(await first) == 4800
    await e.unload()
    assert [c["text"] for c in lib.calls] == ["a"]
    await e.close()


async def test_a_load_whose_speak_was_cancelled_does_not_go_on_to_the_synthesis():
    lib = FakeVoiceLib()
    load, state = _slow_load(lib)
    e = KokoroEngine(FakeModel(), load=load)
    await _cancel_as_anyio_does(lambda: e.speak("a", "af_heart"), lambda: state["now"] == 1)
    await e.unload()
    assert state["loads"] == 1 and lib.calls == []
    await e.close()


async def test_a_speak_cancelled_after_its_thread_ended_still_rearms_the_idle_timer():
    lib = FakeVoiceLib()
    lib.delay = 0.1
    e, _, _ = make(lib, idle_s=0.05)
    await e.ensure()
    await _cancel_as_anyio_does(lambda: e.speak("a", "af_heart"), lambda: lib.now == 1)
    await _until(lambda: not e.loaded)
    await e.close()


async def test_close_waits_for_the_one_piece_in_flight_and_no_longer():
    lib = FakeVoiceLib()
    lib.delay = 0.2
    e, _, _ = make(lib)
    await e.ensure()
    await _cancel_as_anyio_does(lambda: e.speak("a", "af_heart"), lambda: lib.now == 1)
    t0 = time.monotonic()
    await asyncio.wait_for(e.close(), 5)
    assert lib.now == 0 and not e.loaded and time.monotonic() - t0 < 1.0


async def test_a_job_whose_thread_ends_after_the_test_task_is_cancelled_twice_still_holds_the_lock():
    """A plain asyncio task cancelled twice (not anyio): the second cancel arrives while the first is handled."""
    lib = FakeVoiceLib()
    lib.delay = 0.3
    e, _, _ = make(lib)
    await e.ensure()
    task = asyncio.create_task(e.speak("a", "af_heart"))
    await _until(lambda: lib.now == 1)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await e.speak("b", "af_heart")
    assert lib.peak == 1
    await e.close()


def test_importing_the_engine_module_imports_no_numpy():
    code = "import src.kokoro.engine, sys; assert 'numpy' not in sys.modules, 'numpy'"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
