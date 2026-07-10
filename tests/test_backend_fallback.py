# Backend-selection fallback tests: a broken GPU server backend must degrade
# to CPU faster-whisper instead of crashing the app at launch.

import os
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

# parley.main pulls in the full runtime (sounddevice etc.); skip cleanly in
# lean environments rather than erroring at collection.
main = pytest.importorskip("parley.main")


def _whisper_config(backend):
    return {
        "backend": backend,
        "model": "large-v3-turbo",
        "device": "cuda",
        "compute_type": "float16",
        "language": "auto",
        "beam_size": 5,
        "server": {"auto_launch": True},
    }


def test_server_backend_failure_falls_back_to_cpu_engine():
    cfg = _whisper_config("whisper_cpp_server")

    with mock.patch("parley.whisper_engine_server.WhisperEngineServer",
                    side_effect=RuntimeError("whisper-server.exe was not found")), \
         mock.patch.object(main, "WhisperEngine") as fake_engine:
        fake_engine.return_value = mock.sentinel.cpu_engine
        result = main.setup_whisper_engine(cfg, vad_manager=None, model_registry=None)

    assert result is mock.sentinel.cpu_engine
    kwargs = fake_engine.call_args.kwargs
    assert kwargs["device"] == "cpu"
    assert kwargs["compute_type"] == "int8"
    # The caller's dict must not be mutated behind its back.
    assert cfg["device"] == "cuda"


def test_server_backend_success_returns_server_engine():
    cfg = _whisper_config("whisper_cpp_server")

    with mock.patch("parley.whisper_engine_server.WhisperEngineServer",
                    return_value=mock.sentinel.server_engine) as fake_server:
        result = main.setup_whisper_engine(cfg, vad_manager=None, model_registry=None)

    assert result is mock.sentinel.server_engine
    assert fake_server.call_args.kwargs["server_config"] == {"auto_launch": True}
