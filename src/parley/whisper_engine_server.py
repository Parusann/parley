# whisper_engine_server.py
# GPU backend: drives a prebuilt whisper.cpp **Vulkan** `whisper-server.exe` as a
# warm local HTTP service and transcribes over its /inference endpoint. This is the
# only proven GPU path on Windows + AMD RDNA3 (RX 7900 XTX): faster-whisper/CTranslate2
# is CUDA-only, and ROCm on Windows excludes RDNA3, so Vulkan (via whisper.cpp) is it.
# Selected via `whisper.backend: whisper_cpp_server`. Mirrors WhisperEngine's public
# API (transcribe_audio / change_model / is_loading) so the rest of the app is unchanged.
#
# Why a persistent server rather than per-utterance CLI: the model load dominates
# (~1.7s for large-v3-turbo) while warm inference is ~150ms. Keeping the model resident
# in VRAM turns every dictation into a sub-second round-trip.

import atexit
import io
import logging
import os
import socket
import subprocess
import sys
import time
import wave
from typing import Callable, Optional
from urllib import request as urllib_request
from urllib.error import URLError

import numpy as np

# whisper.cpp expects 16 kHz mono audio; the recorder already resamples to this.
_SAMPLE_RATE = 16000


class WhisperEngineServer:
    """Transcribe by POSTing audio to a warm whisper.cpp Vulkan HTTP server.

    Depends on: a prebuilt `whisper-server.exe` (+ ggml-vulkan.dll) and a ggml model
    file. Both are located via `server_config` (explicit paths or "auto"). The server
    subprocess is owned by this object and torn down on exit.
    """

    def __init__(self,
                 model_key: str = "large-v3-turbo",
                 device: str = "cpu",            # accepted for API parity; ignored (GPU is Vulkan)
                 compute_type: str = "int8",     # accepted for API parity; ignored
                 language: Optional[str] = None,
                 beam_size: int = 5,
                 initial_prompt: str = "",
                 hotwords: Optional[list] = None,
                 task: str = "transcribe",
                 vad_manager=None,
                 model_registry=None,
                 log_transcriptions: bool = False,
                 server_config: Optional[dict] = None):

        self.logger = logging.getLogger(__name__)
        self.model_key = model_key
        self.language = None if language in (None, 'auto') else language
        self.beam_size = beam_size
        self.initial_prompt = initial_prompt or ""
        self.hotwords = hotwords or []
        self.task = task if task in ('transcribe', 'translate') else 'transcribe'
        self.log_transcriptions = log_transcriptions
        self.vad_manager = vad_manager
        self.registry = model_registry

        cfg = server_config or {}
        self.host = str(cfg.get('host', '127.0.0.1'))
        self.port = int(cfg.get('port', 8181))
        self.auto_launch = bool(cfg.get('auto_launch', True))
        self.startup_timeout = float(cfg.get('startup_timeout', 60))
        self.request_timeout = float(cfg.get('request_timeout', 60))
        self.extra_args = list(cfg.get('extra_args', []) or [])
        self.gpu_device = cfg.get('gpu_device', None)  # Vulkan device index; None = whisper.cpp default (0)

        self.binary_path = self._resolve_binary_path(cfg.get('binary_path', 'auto'))
        self.model_path = self._resolve_model_path(cfg.get('model_path', 'auto'), cfg.get('model_dir', 'auto'))

        self._proc: Optional[subprocess.Popen] = None
        self._owns_server = False
        self._ready = False

        self._start_or_attach()

    # -------------------------------------------------------------------------
    # Path resolution — the binary + models live under the repo (NOT %APPDATA%,
    # which sandboxed/virtualized environments may redirect), or wherever the user points config.
    # -------------------------------------------------------------------------

    def _runtime_dir(self) -> str:
        """Default home for the Vulkan binary + ggml models.

        Shared with `parley --setup-vulkan` so what setup installs is exactly
        what the engine finds: env override → repo runtime/ (source checkout)
        → per-user data dir (pip/pipx/pyapp installs).
        """
        from .vulkan_setup import runtime_dir
        return runtime_dir()

    def _resolve_binary_path(self, configured: str) -> Optional[str]:
        if configured and configured != 'auto':
            return configured
        candidates = [
            os.path.join(self._runtime_dir(), 'whisper-server.exe'),
            os.path.join(self._runtime_dir(), 'bin', 'whisper-server.exe'),
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c
        # Fall back to PATH lookup (e.g. a system-wide install).
        from shutil import which
        found = which('whisper-server') or which('whisper-server.exe')
        return found

    def _resolve_model_path(self, configured: str, model_dir: str) -> Optional[str]:
        if configured and configured != 'auto':
            return configured
        base = model_dir if (model_dir and model_dir != 'auto') else os.path.join(self._runtime_dir(), 'models')
        candidate = os.path.join(base, f'ggml-{self.model_key}.bin')
        return candidate  # may not exist yet; onboarding downloads it

    # -------------------------------------------------------------------------
    # Server lifecycle
    # -------------------------------------------------------------------------

    def _port_open(self) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            return s.connect_ex((self.host, self.port)) == 0

    def _start_or_attach(self):
        # If something is already serving on the port, reuse it (dev convenience,
        # and avoids a second copy of the model in VRAM).
        if self._port_open():
            self.logger.info(f"Attaching to existing whisper server at {self.host}:{self.port}")
            self._ready = True
            self._owns_server = False
            return

        if not self.auto_launch:
            raise RuntimeError(
                f"whisper_cpp_server: nothing is listening on {self.host}:{self.port} "
                f"and auto_launch is off. Start whisper-server.exe yourself or enable auto_launch."
            )

        if not self.binary_path or not os.path.isfile(self.binary_path):
            raise RuntimeError(
                "whisper_cpp_server backend selected but whisper-server.exe was not found.\n"
                f"Looked under: {self._runtime_dir()} (and PATH).\n"
                "Run `python setup-vulkan.py` (from the repo) to download the Vulkan build, "
                "or set whisper.server.binary_path in your config."
            )
        if not self.model_path or not os.path.isfile(self.model_path):
            raise RuntimeError(
                f"whisper_cpp_server: ggml model not found at {self.model_path}.\n"
                f"Download ggml-{self.model_key}.bin from ggerganov/whisper.cpp (Hugging Face) "
                "or run `python setup-vulkan.py` from the repo."
            )

        print(f"🧠 Launching whisper.cpp Vulkan server [{self.model_key}] on {self.host}:{self.port}...")
        args = [
            self.binary_path,
            '-m', self.model_path,
            '--host', self.host,
            '--port', str(self.port),
        ]
        if self.language:
            args += ['-l', self.language]
        if self.task == 'translate':
            args += ['--translate']
        args += self.extra_args

        env = os.environ.copy()
        # Pin the Vulkan device (0 = discrete RX 7900 XTX on this box; iGPU is 1).
        if self.gpu_device is not None:
            env['GGML_VK_VISIBLE_DEVICES'] = str(self.gpu_device)

        # Hide the child console window on Windows; send its logs to our logger's stderr.
        creationflags = 0
        if sys.platform == 'win32':
            creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

        self._proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            creationflags=creationflags,
        )
        self._owns_server = True
        atexit.register(self.shutdown)  # never orphan whisper-server.exe

        self._wait_until_ready()

    def _wait_until_ready(self):
        deadline = time.time() + self.startup_timeout
        while time.time() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                raise RuntimeError(
                    f"whisper-server.exe exited during startup (code {self._proc.returncode}). "
                    "This is often the known Vulkan-on-Windows-AMD crash — check drivers, or fall "
                    "back to the CPU large-v3-turbo backend."
                )
            if self._port_open():
                self._ready = True
                print(f"   ✓ whisper.cpp Vulkan server ready ({self.model_key})")
                return
            time.sleep(0.25)
        self.shutdown()
        raise RuntimeError(
            f"whisper-server.exe did not become ready within {self.startup_timeout}s on {self.host}:{self.port}."
        )

    def _try_recover(self) -> bool:
        """One-shot mid-session recovery: relaunch the server if ours died.

        Only fires when we own the subprocess and it has actually exited —
        an attached (external) server or a live-but-erroring one is left alone.
        """
        if not self._owns_server or self._proc is None or self._proc.poll() is None:
            return False
        code = self._proc.returncode
        print(f"⚠ whisper server exited (code {code}) — restarting it...")
        self.logger.warning(f"whisper-server.exe died mid-session (code {code}); restarting")
        self._proc = None
        self._ready = False
        self._owns_server = False
        try:
            self._start_or_attach()
            return True
        except Exception as e:
            self.logger.error(f"whisper server restart failed: {e}")
            return False

    def shutdown(self):
        """Terminate the server subprocess we own. Idempotent; safe from atexit."""
        proc = self._proc
        self._proc = None
        self._ready = False
        if proc is None or not self._owns_server:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception as e:
            self.logger.debug(f"whisper server shutdown: {e}")

    def __del__(self):
        try:
            self.shutdown()
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # Transcription
    # -------------------------------------------------------------------------

    def transcribe_audio(self, audio_data: Optional[np.ndarray]) -> Optional[str]:
        if audio_data is None or len(audio_data) == 0:
            self.logger.warning("No audio data to transcribe")
            return None

        # Honour VAD exactly like the other backends: skip transcription on silence.
        if self.vad_manager and self.vad_manager.is_available():
            if not self.vad_manager.check_audio_for_speech(audio_data):
                print("   ✗ No speech detected, skipping transcription")
                return None

        try:
            wav_bytes = self._to_wav_bytes(audio_data)
            t0 = time.time()
            try:
                text = self._post_inference(wav_bytes)
            except RuntimeError:
                # The warm server can die mid-session (driver reset, OOM, crash).
                # If it was ours, restart it once and retry — the user just
                # spoke; losing that utterance to a dead subprocess is not OK.
                if not self._try_recover():
                    raise
                text = self._post_inference(wav_bytes)
            elapsed = time.time() - t0
            print(f"   ✓ Transcription completed in {elapsed:.1f} seconds")

            text = (text or "").strip()
            if self.log_transcriptions:
                self.logger.info(f"Transcribed: '{text}'")
            else:
                self.logger.info(f"Transcribed {len(text)} chars")
            return text or None

        except Exception as e:
            self.logger.error(f"Transcription failed: {e}", exc_info=True)
            print(f"❌ Transcription failed: {e}")
            return None

    def _to_wav_bytes(self, audio_data: np.ndarray) -> bytes:
        """Encode a float32 mono waveform as 16-bit PCM WAV (what /inference wants)."""
        if audio_data.ndim > 1:
            audio_data = audio_data.flatten()
        audio_data = np.asarray(audio_data, dtype=np.float32)
        # Clip then scale to int16.
        clipped = np.clip(audio_data, -1.0, 1.0)
        pcm16 = (clipped * 32767.0).astype('<i2')

        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(_SAMPLE_RATE)
            wf.writeframes(pcm16.tobytes())
        return buf.getvalue()

    def _post_inference(self, wav_bytes: bytes) -> str:
        """multipart/form-data POST to /inference; returns the transcript text.

        Stdlib-only (no `requests` dependency): we hand-build the multipart body.
        """
        url = f"http://{self.host}:{self.port}/inference"
        boundary = "----parleyBoundary7MA4YWxkTrZu0gW"

        def field(name: str, value: str) -> bytes:
            return (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode('utf-8')

        parts = [
            field('response_format', 'json'),
            field('temperature', '0.0'),
        ]
        if self.initial_prompt:
            parts.append(field('prompt', self.initial_prompt))
        # File part.
        parts.append(
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
                "Content-Type: audio/wav\r\n\r\n"
            ).encode('utf-8')
        )
        body = b"".join(parts) + wav_bytes + f"\r\n--{boundary}--\r\n".encode('utf-8')

        req = urllib_request.Request(url, data=body, method='POST')
        req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')
        try:
            with urllib_request.urlopen(req, timeout=self.request_timeout) as resp:
                raw = resp.read().decode('utf-8', errors='replace')
        except URLError as e:
            raise RuntimeError(f"whisper server request failed: {e}") from e

        # /inference returns {"text": "..."}; be tolerant of plain-text too.
        import json
        try:
            return json.loads(raw).get('text', '')
        except (ValueError, AttributeError):
            return raw

    # -------------------------------------------------------------------------
    # Model management + status (mirrors WhisperEngineCpp)
    # -------------------------------------------------------------------------

    def change_model(self,
                     new_model_key: str,
                     progress_callback: Optional[Callable[[str], None]] = None) -> bool:
        if new_model_key == self.model_key:
            return False
        try:
            if progress_callback:
                progress_callback(f"Restarting Vulkan server with {new_model_key}...")
            self.shutdown()
            self.model_key = new_model_key
            self.model_path = self._resolve_model_path('auto', 'auto')
            self._owns_server = False
            self._proc = None
            self._start_or_attach()
            if progress_callback:
                progress_callback(f"Model {new_model_key} ready")
            return True
        except Exception as e:
            self.logger.error(f"Model change failed: {e}")
            if progress_callback:
                progress_callback(f"Model change failed: {e}")
            return False

    def is_loading(self) -> bool:
        return not self._ready
