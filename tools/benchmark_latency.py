# benchmark_latency.py
# Measures warm dictation latency: the wall-clock cost of one transcribe_audio()
# call against a resident model, which is what the user feels between releasing
# the hotkey and text landing. Use it before/after any change that touches the
# dictation path to prove the change adds zero latency.
#
#   python tools/benchmark_latency.py                       # whisper_cpp_server backend
#   python tools/benchmark_latency.py --backend faster_whisper
#   python tools/benchmark_latency.py --wav my_clip.wav --runs 20

import argparse
import json
import os
import statistics
import sys
import time
import wave

import numpy as np

# Run from a repo checkout without installing: put src/ on the path.
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "src"))

_SAMPLE_RATE = 16000
_TTS_SENTENCE = (
    "The quick brown fox jumps over the lazy dog, "
    "and Parley turns speech into text on this machine."
)


def _generate_speech_wav(path: str) -> None:
    """Synthesize a fixed sentence with Windows SAPI so runs are comparable.

    16 kHz 16-bit mono matches what the recorder hands the engines.
    """
    import win32com.client  # pywin32 is a hard dependency of the app on Windows

    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
    fmt.Type = 34  # SAFT16kHz16BitMono
    stream.Format = fmt
    stream.Open(path, 3)  # SSFMCreateForWrite
    voice = win32com.client.Dispatch("SAPI.SpVoice")
    voice.AudioOutputStream = stream
    voice.Speak(_TTS_SENTENCE)
    stream.Close()


def _load_wav_as_float32(path: str) -> np.ndarray:
    """Read a WAV into the float32 mono 16 kHz array the engines expect."""
    with wave.open(path, "rb") as wf:
        rate = wf.getframerate()
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())
    if width != 2:
        raise SystemExit(f"expected 16-bit PCM, got {8 * width}-bit: {path}")
    audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != _SAMPLE_RATE:
        import soxr  # already an app dependency

        audio = soxr.resample(audio, rate, _SAMPLE_RATE)
    return audio


def _build_engine(backend: str, model: str):
    if backend == "whisper_cpp_server":
        from parley.whisper_engine_server import WhisperEngineServer

        return WhisperEngineServer(model_key=model)
    if backend == "faster_whisper":
        from parley.whisper_engine import WhisperEngine

        return WhisperEngine(model_key=model, device="auto", compute_type="auto")
    if backend == "whisper_cpp":
        from parley.whisper_engine_cpp import WhisperEngineCpp

        return WhisperEngineCpp(model_key=model)
    raise SystemExit(f"unknown backend: {backend}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default="whisper_cpp_server",
                        choices=["whisper_cpp_server", "faster_whisper", "whisper_cpp"])
    parser.add_argument("--model", default="large-v3-turbo")
    parser.add_argument("--wav", default=None, help="fixed clip to transcribe (default: SAPI-generated)")
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    wav_path = args.wav
    if not wav_path:
        wav_path = os.path.join(_REPO, ".temp", "benchmark_clip.wav")
        os.makedirs(os.path.dirname(wav_path), exist_ok=True)
        if not os.path.isfile(wav_path):
            _generate_speech_wav(wav_path)
    audio = _load_wav_as_float32(wav_path)

    engine = _build_engine(args.backend, args.model)

    # Warmup runs absorb one-time costs (model residency, first-request setup).
    for _ in range(args.warmup):
        engine.transcribe_audio(audio)

    times = []
    text = ""
    for _ in range(args.runs):
        t0 = time.perf_counter()
        text = engine.transcribe_audio(audio) or ""
        times.append(time.perf_counter() - t0)

    result = {
        "backend": args.backend,
        "model": args.model,
        "clip_seconds": round(len(audio) / _SAMPLE_RATE, 2),
        "runs": args.runs,
        "min_ms": round(min(times) * 1000, 1),
        "median_ms": round(statistics.median(times) * 1000, 1),
        "mean_ms": round(statistics.mean(times) * 1000, 1),
        "p95_ms": round(sorted(times)[max(0, int(len(times) * 0.95) - 1)] * 1000, 1),
        "transcript": text,
    }
    if args.json:
        print(json.dumps(result))
    else:
        print(f"\nbackend={result['backend']}  model={result['model']}  clip={result['clip_seconds']}s")
        print(f"warm latency over {args.runs} runs:  "
              f"min {result['min_ms']} ms · median {result['median_ms']} ms · "
              f"mean {result['mean_ms']} ms · p95 {result['p95_ms']} ms")
        print(f"transcript: {text!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
