# Tests for the F9 assistant path: state_manager must hand a finished
# transcription to router_client (Switchboard) instead of pasting it, but ONLY
# when assistant mode triggered the recording. A real StateManager is built
# with mocked collaborators — no audio hardware, no whisper server, no network.

import os
import sys
import threading
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

pytest.importorskip("sounddevice")
np = pytest.importorskip("numpy")

from parley import state_manager as sm_module
from parley import router_client
from parley.state_manager import StateManager


class FakeRecorder:
    # Just enough recorder for start/stop bookkeeping — no audio thread.
    def __init__(self, audio):
        self._audio = audio
        self._recording = False

    def start_recording(self):
        self._recording = True
        return True

    def get_recording_status(self):
        return self._recording

    def stop_recording(self):
        self._recording = False
        return self._audio

    def cancel_recording(self):
        self._recording = False

    def get_audio_duration(self, audio):
        return 1.5


@pytest.fixture
def env(monkeypatch):
    # Neutralize the heavy __init__ collaborators StateManager builds itself.
    monkeypatch.setattr(StateManager, "_initialize_audio_host", lambda self: None)
    for name in ("ProfileManager", "AppRules", "TransformsManager", "FallbackWindow"):
        monkeypatch.setattr(sm_module, name, mock.Mock())
    # Stats / history / audit writers must not touch the real user profile.
    monkeypatch.setattr(sm_module, "record_transcription", mock.Mock())
    monkeypatch.setattr(sm_module, "record_transcript", mock.Mock())
    monkeypatch.setattr(sm_module, "audit_record", mock.Mock())
    monkeypatch.setattr(sm_module.foreground, "get_foreground_app",
                        lambda: {"exe": "notepad.exe", "title": "Untitled - Notepad"})

    # The router seam under test: state_manager calls these module functions.
    send_mock = mock.Mock(return_value=None)
    event_mock = mock.Mock()
    monkeypatch.setattr(router_client, "send", send_mock)
    monkeypatch.setattr(router_client, "send_event", event_mock)

    router_cfg = {"endpoint": "http://127.0.0.1:9/ask", "timeout_seconds": 1}
    config_manager = mock.Mock()
    config_manager.get_postprocess_config.return_value = {}
    config_manager.get_router_config.return_value = router_cfg
    config_manager.config = {"audit": {"enabled": False}, "audio": {}, "overlay": {}}

    whisper_engine = mock.Mock()
    whisper_engine.transcribe_audio.return_value = "hello there"

    clipboard = mock.Mock()
    clipboard.deliver_transcription.return_value = True
    clipboard.auto_paste = True

    tray = mock.Mock()
    feedback = mock.Mock()

    manager = StateManager(
        audio_recorder=FakeRecorder(np.zeros(16000, dtype=np.float32)),
        whisper_engine=whisper_engine,
        clipboard_manager=clipboard,
        config_manager=config_manager,
        vad_manager=mock.Mock(),
        system_tray=tray,
        audio_feedback=feedback,
        voice_command_manager=None,
    )
    # The AppRules mock instance must behave like "no rule matched".
    manager.app_rules.match_for_foreground.return_value = None

    return {
        "manager": manager, "send": send_mock, "event": event_mock,
        "clipboard": clipboard, "tray": tray, "feedback": feedback,
        "router_cfg": router_cfg, "whisper": whisper_engine,
    }


def test_assistant_dictation_routes_to_switchboard_not_clipboard(env):
    manager = env["manager"]

    manager.start_assistant_recording()
    assert manager._assistant_mode is True
    env["event"].assert_called_once_with("listening", "start", env["router_cfg"])

    assert manager.stop_recording() is True

    # (a) transcript went out via the router with the right payload...
    env["send"].assert_called_once()
    args, kwargs = env["send"].call_args
    assert args[0] == "hello there"
    assert args[1] == env["router_cfg"]
    assert kwargs["on_failure"] == manager._assistant_send_failed
    # ...the listening ping was closed out...
    assert env["event"].call_args_list[-1] == mock.call("listening", "stop", env["router_cfg"])
    # ...and NOTHING was pasted or copied.
    env["clipboard"].deliver_transcription.assert_not_called()
    env["clipboard"].copy_text.assert_not_called()

    assert manager.last_transcription == "hello there"
    assert manager._assistant_mode is False  # flag consumed, not sticky

    # A follow-up plain dictation must deliver normally (regression: a stale
    # assistant flag would silently reroute the NEXT recording).
    manager.audio_recorder._recording = True
    assert manager.stop_recording() is True
    env["clipboard"].deliver_transcription.assert_called_once_with("hello there", False)
    env["send"].assert_called_once()  # still only the assistant send


def test_normal_dictation_never_calls_router(env):
    manager = env["manager"]
    manager.audio_recorder._recording = True

    assert manager.stop_recording(use_auto_enter=False) is True

    env["clipboard"].deliver_transcription.assert_called_once_with("hello there", False)
    env["send"].assert_not_called()
    env["event"].assert_not_called()


def test_assistant_with_empty_cleaned_text_sends_nothing(env):
    manager = env["manager"]
    env["whisper"].transcribe_audio.return_value = "   "  # cleanup strips to empty

    manager.start_assistant_recording()
    manager.stop_recording()

    env["send"].assert_not_called()
    env["tray"].notify.assert_any_call("Nothing usable to send (cleanup left empty text).")


def test_assistant_send_failed_callback_does_not_crash(env):
    manager = env["manager"]
    # Runs on the router daemon thread when the POST is definitely dropped:
    # it must only beep + notify, never raise back into the pipeline.
    manager._assistant_send_failed()
    env["feedback"].play_cancel_sound.assert_called_once()
    env["tray"].notify.assert_called_once_with(
        "Switchboard is not running — assistant request was dropped.")


class _RefusingConnection:
    # http.client.HTTPConnection stand-in whose connect() always refuses.
    def __init__(self, host, port, timeout=None):
        pass

    def connect(self):
        raise ConnectionRefusedError("connection refused")

    def close(self):
        pass


def test_router_client_fires_on_failure_when_connection_refused(monkeypatch):
    # Real router_client.send code path (not the mock): a connect-phase failure
    # must invoke on_failure exactly once, on the worker thread.
    monkeypatch.setattr(router_client.http.client, "HTTPConnection", _RefusingConnection)
    on_failure = mock.Mock()

    thread = router_client.send("ping", {"endpoint": "http://127.0.0.1:7780/ask"},
                                on_failure=on_failure)
    assert isinstance(thread, threading.Thread)
    thread.join(timeout=3)
    assert not thread.is_alive()
    on_failure.assert_called_once_with()


def test_router_client_swallows_on_failure_exceptions(monkeypatch):
    # Even a broken failure callback must terminate on the daemon thread.
    monkeypatch.setattr(router_client.http.client, "HTTPConnection", _RefusingConnection)

    def broken_callback():
        raise RuntimeError("callback exploded")

    thread = router_client.send("ping", {"endpoint": "http://127.0.0.1:7780/ask"},
                                on_failure=broken_callback)
    thread.join(timeout=3)
    assert not thread.is_alive()  # finished despite the raising callback


def test_router_client_skips_empty_text():
    assert router_client.send("", {}) is None
    assert router_client.send("   ", {}) is None
