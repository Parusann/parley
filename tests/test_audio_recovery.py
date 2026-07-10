# Tests for audio_recorder.py mic-disconnect recovery (_capture_loop).
# No real audio hardware: the whole `sounddevice` module reference inside
# audio_recorder is swapped for a fake whose InputStream can be made to raise
# disconnect-signature errors on demand. The 0.5s recovery pause is shortened
# via a fake `time` so every scenario runs in milliseconds.

import os
import sys
import threading
import time as real_time
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

pytest.importorskip("sounddevice")
pytest.importorskip("soxr")
np = pytest.importorskip("numpy")

from parley import audio_recorder as ar_module
from parley.audio_recorder import AudioRecorder

DISCONNECT_ERROR = RuntimeError("Unanticipated host error [PaErrorCode -9999]")


class _FakeStream:
    # Context-manager stand-in for a successfully opened sd.InputStream.
    def __init__(self, fake_sd):
        self._fake_sd = fake_sd

    def __enter__(self):
        with self._fake_sd.lock:
            self._fake_sd.open_count += 1
        return self

    def __exit__(self, *exc):
        return False


class FakeSounddevice:
    # Minimal stand-in for the parts of `sounddevice` AudioRecorder touches.
    def __init__(self, fail_first=0, error=DISCONNECT_ERROR):
        self.fail_first = fail_first      # raise on this many initial InputStream opens
        self.error = error
        self.attempts = 0                 # InputStream construction attempts
        self.open_count = 0               # successful stream opens
        self.raise_in_sleep = threading.Event()  # one-shot mid-stream failure
        self.lock = threading.Lock()

    def query_devices(self, device=None, kind=None):
        return {"name": "Fake Mic", "hostapi": 0, "default_samplerate": 16000,
                "max_input_channels": 2, "index": 7}

    def query_hostapis(self, index=None):
        return {"name": "MME"}  # not WASAPI -> no resampling path

    def sleep(self, ms):
        if self.raise_in_sleep.is_set():
            self.raise_in_sleep.clear()
            raise self.error  # simulates the stream dying mid-recording
        real_time.sleep(0.005)

    def InputStream(self, **kwargs):
        with self.lock:
            self.attempts += 1
            attempt = self.attempts
        if attempt <= self.fail_first:
            raise self.error
        return _FakeStream(self)


class _FastTime:
    # audio_recorder's `time.sleep(0.5)` recovery pause, shortened.
    time = staticmethod(real_time.time)

    @staticmethod
    def sleep(seconds):
        real_time.sleep(min(seconds, 0.01))


def _wait_for(predicate, timeout=3.0):
    deadline = real_time.monotonic() + timeout
    while real_time.monotonic() < deadline:
        if predicate():
            return True
        real_time.sleep(0.01)
    return predicate()


def _make_recorder(monkeypatch, fake_sd, device=7):
    monkeypatch.setattr(ar_module, "sd", fake_sd)
    monkeypatch.setattr(ar_module, "time", _FastTime)
    vad_manager = mock.Mock()
    vad_manager.is_available.return_value = False  # no continuous VAD wiring
    return AudioRecorder(
        on_vad_event=lambda event: None,
        vad_manager=vad_manager,
        device=device,
        max_duration=30,
    )


def test_disconnect_signature_detected_and_falls_back_to_default(monkeypatch):
    # (a)+(b): first open dies with a disconnect signature -> the loop detects
    # it, drops the pinned device (fallback to system default) and reopens.
    fake_sd = FakeSounddevice(fail_first=1)
    recorder = _make_recorder(monkeypatch, fake_sd, device=7)
    try:
        assert _wait_for(lambda: fake_sd.open_count >= 1), "stream never recovered"
        assert recorder.device is None                    # fell back to default input
        assert recorder._stream_error is fake_sd.error    # the disconnect was recorded
        assert fake_sd.attempts == 2                      # one failure + one recovery
        assert recorder._capture_thread.is_alive()        # loop kept running
    finally:
        recorder.shutdown()
    assert not recorder._capture_thread.is_alive()


def test_mid_recording_disconnect_aborts_recording_and_clears_buffer(monkeypatch):
    # Stream dies WHILE a recording is in flight: the loop must drop the
    # half-recording (is_recording False, buffer cleared) before recovering.
    fake_sd = FakeSounddevice(fail_first=0)
    recorder = _make_recorder(monkeypatch, fake_sd, device=7)
    try:
        assert _wait_for(lambda: fake_sd.open_count >= 1)
        assert recorder.start_recording() is True
        with recorder._buffer_lock:
            recorder._buffer.append(np.zeros((256, 1), dtype=np.float32))

        fake_sd.raise_in_sleep.set()  # next sd.sleep inside the stream raises
        assert _wait_for(lambda: fake_sd.open_count >= 2), "stream never reopened"

        assert recorder.is_recording is False
        with recorder._buffer_lock:
            assert len(recorder._buffer) == 0
        assert recorder.device is None
    finally:
        recorder.shutdown()


def test_persistent_disconnect_exhausts_retries_without_raising(monkeypatch):
    # (c): the error never goes away -> after max_retries the loop gives up
    # and exits cleanly; nothing propagates into the constructor/caller.
    fake_sd = FakeSounddevice(fail_first=999)
    recorder = _make_recorder(monkeypatch, fake_sd, device=7)
    recorder._capture_thread.join(timeout=5)
    assert not recorder._capture_thread.is_alive()
    assert fake_sd.attempts == 4          # initial attempt + 3 retries
    assert recorder.device is None        # fallback was attempted along the way
    recorder.shutdown()


def test_non_disconnect_error_does_not_trigger_device_fallback(monkeypatch):
    # (d): an unrelated stream error must NOT be treated as a disconnect —
    # no retries, no fallback to the default device.
    fake_sd = FakeSounddevice(fail_first=999,
                              error=ValueError("expected float32 samples, got float64"))
    recorder = _make_recorder(monkeypatch, fake_sd, device=7)
    recorder._capture_thread.join(timeout=5)
    assert not recorder._capture_thread.is_alive()
    assert fake_sd.attempts == 1          # gave up immediately, no retry loop
    assert recorder.device == 7           # pinned device kept
    recorder.shutdown()


@pytest.mark.parametrize("message", [
    "PaErrorCode -9999: Unanticipated host error",
    "Error opening InputStream: Invalid device [PaErrno -9996]",
    "[Errno -9988] Stream closed",
    "No Default Input Device Available",
])
def test_disconnect_signature_matching_is_case_insensitive(monkeypatch, message):
    # (a): every documented disconnect signature (matched on the lowercased
    # message) must route into the retry/fallback branch, i.e. 4 attempts.
    fake_sd = FakeSounddevice(fail_first=999, error=RuntimeError(message))
    recorder = _make_recorder(monkeypatch, fake_sd, device=7)
    recorder._capture_thread.join(timeout=5)
    assert fake_sd.attempts == 4
    assert recorder.device is None
    recorder.shutdown()


def test_resolve_device_rejects_input_less_device(monkeypatch):
    # resolve_device sanity: a device with no input channels falls back to
    # the default input (device=None) instead of pinning a useless device.
    fake_sd = FakeSounddevice()
    fake_sd.query_devices = lambda device=None, kind=None: {
        "name": "HDMI Out", "hostapi": 0, "default_samplerate": 48000,
        "max_input_channels": 0 if device is not None else 2, "index": 3,
    }
    recorder = _make_recorder(monkeypatch, fake_sd, device=3)
    try:
        assert recorder.device is None
    finally:
        recorder.shutdown()
