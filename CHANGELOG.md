# Changelog

All notable changes to Parley are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and Parley adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — Unreleased

First public release. Parley is a push-to-talk, fully offline voice dictation
app for Windows: hold a hotkey, speak, and get clean text at your cursor —
no cloud, no account, no audio leaving your machine.

Parley builds on the excellent MIT-licensed
[drajb/whisper-local](https://github.com/drajb/whisper-local) and its parent
[PinW/whisper-key-local](https://github.com/PinW/whisper-key-local); see
[AUTHORS.md](AUTHORS.md) for the full credit chain.

### Highlights

- **Push-to-talk dictation** with always-on audio buffering (0.5 s pre-roll —
  first words are never clipped) and TEN-VAD silence rejection.
- **GPU-accelerated transcription on AMD via Vulkan**: a warm whisper.cpp
  server keeps `large-v3-turbo` resident in VRAM; warm dictations round-trip
  in ~135 ms on an RX 7900 XTX. One command to install: `parley --setup-vulkan`.
- **Verified supply chain**: the Vulkan runtime and every Whisper model are
  pinned to exact versions and SHA-256-verified before they are trusted;
  mismatches are quarantined, never executed.
- **Resilient backends**: if the GPU server can't start, Parley falls back to
  CPU instead of failing to launch; if the server dies mid-session it is
  restarted once and your utterance is retried, not lost.
- **Deterministic vocabulary correction**: fuzzy-snaps near-miss words to your
  own terms ("Amadies" → "Amadeus") with zero hallucination, before any
  optional LLM polish.
- **Optional local LLM cleanup** via Ollama — removes filler words without
  rephrasing you; degrades gracefully to the raw transcript when Ollama is down.
- **A calm, cohesive UI**: an acrylic glass waveform pill (with live streaming
  text) as the hero surface, plus themed settings, history, cheat-sheet, and
  dictionary windows — dark/light aware, per-monitor DPI aware, and
  reduced-motion aware.
- **Per-app rules**: formatting and paste behavior that adapt to the focused
  application (terminals, password fields, code editors).
- **Voice commands & AI transforms**: hotkey-routed command mode and
  rephrase-selection with your own prompt library.
- **Diagnostics that tell the truth**: `--doctor` reports the *active* STT
  backend and real GPU visibility (not just what the config says), and
  `--selftest` exercises mic → model → transcription → clipboard end to end.
- **Standalone executable**: a 4 MB `parley.exe` bootstraps its own private
  Python environment on first launch — no system Python required.

[1.0.0]: https://github.com/Parusann/parley/releases/tag/v1.0.0
