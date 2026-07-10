# Tests for whisper_engine_server.py (the whisper.cpp Vulkan server backend).
# No GPU or real binary needed: we stand up a tiny mock /inference HTTP server and point
# the engine at it (auto_launch=False -> it attaches to the running port).

import io
import json
import os
import socket
import sys
import threading
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from parley.whisper_engine_server import WhisperEngineServer


class _MockInferenceHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # keep test output clean

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)  # drain the multipart body
        payload = json.dumps({"text": " mocked transcript "}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def mock_server():
    port = _free_port()
    srv = HTTPServer(("127.0.0.1", port), _MockInferenceHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        srv.shutdown()
        srv.server_close()


def _engine(port: int) -> WhisperEngineServer:
    # auto_launch=False -> attach to the mock instead of spawning whisper-server.exe.
    return WhisperEngineServer(model_key="test", server_config={"port": port, "auto_launch": False})


def test_attaches_to_running_server(mock_server):
    eng = _engine(mock_server)
    assert eng.is_loading() is False
    assert eng._owns_server is False  # must not try to manage a server it didn't start
    eng.shutdown()


def test_transcribes_via_inference_endpoint(mock_server):
    eng = _engine(mock_server)
    audio = np.zeros(16000, dtype=np.float32)
    assert eng.transcribe_audio(audio) == "mocked transcript"  # stripped
    eng.shutdown()


def test_empty_audio_returns_none(mock_server):
    eng = _engine(mock_server)
    assert eng.transcribe_audio(np.array([], dtype=np.float32)) is None
    eng.shutdown()


def test_wav_encoding_is_16k_mono_pcm16(mock_server):
    eng = _engine(mock_server)
    audio = (np.sin(np.linspace(0, 3.14, 8000)) * 0.5).astype(np.float32)
    wav_bytes = eng._to_wav_bytes(audio)
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        assert w.getframerate() == 16000
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getnframes() == 8000
    eng.shutdown()


def test_missing_server_without_autolaunch_raises():
    # Nothing listening + auto_launch off -> a clear error, not a hang.
    with pytest.raises(RuntimeError):
        WhisperEngineServer(model_key="test", server_config={"port": _free_port(), "auto_launch": False})


class _DeadProc:
    returncode = 1

    def poll(self):
        return 1  # exited


class _LiveProc:
    returncode = None

    def poll(self):
        return None  # still running


def test_mid_session_death_restarts_and_retries(mock_server):
    # Our server dies between utterances -> one restart + retry, user keeps their words.
    eng = _engine(mock_server)
    eng._owns_server = True
    eng._proc = _DeadProc()

    original = eng._post_inference
    calls = {"n": 0}

    def flaky(wav_bytes):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("whisper server request failed: connection refused")
        return original(wav_bytes)

    eng._post_inference = flaky
    restarted = {"v": False}

    def fake_start():
        restarted["v"] = True
        eng._ready = True
        eng._owns_server = True

    eng._start_or_attach = fake_start

    audio = np.zeros(16000, dtype=np.float32)
    assert eng.transcribe_audio(audio) == "mocked transcript"
    assert restarted["v"] is True
    assert calls["n"] == 2
    eng._proc = None
    eng._owns_server = False


def test_attached_server_error_never_restarts(mock_server):
    # We didn't start it -> we must not try to manage (restart) it.
    eng = _engine(mock_server)
    assert eng._owns_server is False

    def boom(wav_bytes):
        raise RuntimeError("whisper server request failed")

    eng._post_inference = boom

    def must_not_be_called():
        raise AssertionError("restart attempted on an attached server")

    eng._start_or_attach = must_not_be_called

    audio = np.zeros(16000, dtype=np.float32)
    assert eng.transcribe_audio(audio) is None  # graceful failure, no crash
    eng.shutdown()


def test_live_owned_server_error_does_not_restart(mock_server):
    # Server alive but a request failed (e.g. timeout) -> no restart churn.
    eng = _engine(mock_server)
    eng._owns_server = True
    eng._proc = _LiveProc()
    assert eng._try_recover() is False
    eng._proc = None
    eng._owns_server = False
    eng.shutdown()
