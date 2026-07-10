# GPU Acceleration Setup

GPU acceleration lets you run bigger Whisper models with faster transcription. On CPU, `tiny` and `base` models work well. With a GPU, you can comfortably run `medium`, `large`, and `large-v3-turbo` for significantly better accuracy.

## Automatic Setup (Recommended)

On first launch, Parley detects your GPU and offers to install the required runtime libraries automatically:

```
🖥️ System check...
   ✓ Detected NVIDIA GeForce RTX 4070
   ✗ NVIDIA CUDA runtime not found

  ┌─────────────────────────────────────────────────────────┐
  │ GPU acceleration available                              │
  │ Use NVIDIA GeForce RTX 4070 for fast transcription?     │
  │                                                         │
  │ [1] Setup GPU, install CUDA                             │
  │     1.2 GB download, 1.8 GB disk space                  │
  │                                                         │
  │ [2] Skip for now                                        │
  │     Use CPU transcription for this session              │
  │                                                         │
  │ [3] Use CPU only                                        │
  │     Don't ask again                                     │
  └─────────────────────────────────────────────────────────┘

  Press a number to choose:
```

# Manual Setup

If you prefer to install GPU dependencies yourself, have an [AMD RDNA 1 GPU](#amd--rdna-1-rocm-62), or need to troubleshoot, follow the instructions below.

To enable GPU mode, set these in your [user settings](../README.md#️-configuration):
```yaml
whisper:
  device: cuda
  compute_type: float16
```
Note: `cuda` applies to both [NVIDIA](#nvidia-cuda) and [AMD](#amd--rdna-2-rocm) GPUs.

## NVIDIA (CUDA)

**Requirements:** NVIDIA GPU with CUDA support (GTX 900 series or newer)

### Option A: pip packages (simplest)

```
pip install nvidia-cuda-runtime-cu12 nvidia-cublas-cu12 nvidia-cudnn-cu12
```

### Option B: CUDA Toolkit installer

Install CUDA Toolkit 12 (CUDA 13 is not yet supported by faster-whisper):
```
winget install Nvidia.CUDA --version 12.9
```
Or download from [nvidia.com/cuda-downloads](https://developer.nvidia.com/cuda-downloads)

Then verify with `nvidia-smi`, set `device: cuda` and `compute_type: float16`, and restart Parley.

## AMD — RDNA 2+ (ROCm)

**Requirements:** AMD GPU with RDNA 2 or newer (RX 6000, 7000, 9000 series)

First, install the ROCm prerequisites using **one** of these two methods:

#### Method A: HIP SDK 7.1 installer

1. Install [HIP SDK 7.1](https://www.amd.com/en/developer/resources/rocm-hub/hip-sdk.html)
2. **DLL fix required:** HIP 7.1 renamed `hipblas.dll` to `libhipblas.dll`, which CTranslate2 doesn't recognize. Go to the ROCm bin folder (e.g. `C:\Program Files\AMD\ROCm\7.1\bin`), find `libhipblas.dll`, and copy it as `hipblas.dll` in the same folder.

#### Method B: ROCm SDK via pip (HIP 7.2)

**Prerequisite:** [AMD Adrenalin 26.1.1](https://www.amd.com/en/resources/support-articles/release-notes/RN-RAD-WIN-26-1-1.html) graphics driver or newer

```powershell
pip install --no-cache-dir `
    https://repo.radeon.com/rocm/windows/rocm-rel-7.2/rocm_sdk_core-7.2.0.dev0-py3-none-win_amd64.whl `
    https://repo.radeon.com/rocm/windows/rocm-rel-7.2/rocm_sdk_libraries_custom-7.2.0.dev0-py3-none-win_amd64.whl `
    https://repo.radeon.com/rocm/windows/rocm-rel-7.2/rocm-7.2.0.dev0.tar.gz
```

Then install the ROCm CTranslate2 wheel:

### Portable exe

1. Build a release exe locally (see [`pyapp-build/README.md`](../pyapp-build/README.md))
2. Run `parley.exe`
3. Set `device: cuda` and `compute_type: float16`

### pip

1. Install the ROCm CTranslate2 wheel matching your Python version from [ctranslate2-rocm-wheels](https://github.com/PinW/ctranslate2-rocm-wheels/releases/tag/v4.7.1-rocm72):
   ```
   pip install ctranslate2-4.7.1-cp313-cp313-win_amd64.whl --force-reinstall --no-deps
   ```
2. Set `device: cuda` and `compute_type: float16`
3. Restart Parley

### pipx

1. Install the ROCm CTranslate2 wheel into the pipx venv from [ctranslate2-rocm-wheels](https://github.com/PinW/ctranslate2-rocm-wheels/releases/tag/v4.7.1-rocm72):
   ```
   pipx runpip parley install ctranslate2-4.7.1-cp313-cp313-win_amd64.whl --force-reinstall --no-deps
   ```
2. Set `device: cuda` and `compute_type: float16`
3. Restart Parley

## AMD — whisper.cpp Vulkan backend (no ROCm)

The `whisper_cpp_server` backend runs a large Whisper model on your AMD GPU through
**Vulkan** — no ROCm, no HIP SDK, no `hipblas.dll` fix, and no sensitivity to Adrenalin
driver updates. It drives a self-contained prebuilt whisper.cpp `whisper-server.exe` kept
warm in the background. On an RX 7900 XTX this transcribes a short utterance in ~150 ms.

Trade-off vs the ROCm path above: fewer decoding levers (no `hotwords` / beam controls), and
transcription is batch-per-utterance (whisper.cpp *streaming* crashes on Windows + AMD). For
push-to-talk dictation that shape is exactly right.

**Setup:**

1. Download the runtime (binary + ggml model) into `runtime/whisper-cpp-vulkan/`:
   ```
   python setup-vulkan.py
   ```
2. Enable it in your [user settings](../README.md#️-configuration):
   ```yaml
   whisper:
     backend: whisper_cpp_server
     model: large-v3-turbo
     server:
       gpu_device: 0   # Vulkan device index; 0 is usually the discrete GPU
   ```
3. Restart Parley — the server starts automatically and stops with the app.

If you have both a discrete GPU and an iGPU, check the Vulkan indices with
`vulkaninfo --summary` and set `server.gpu_device` accordingly (the discrete card is
usually device 0).

### Where the Vulkan runtime comes from (provenance)

`setup-vulkan.py` refuses to trust anything it cannot verify:

- **Server binary** — whisper.cpp upstream ([ggml-org](https://github.com/ggml-org/whisper.cpp))
  publishes no Windows Vulkan release asset (as of v1.9.1: CPU, BLAS, and CUDA only), so the
  binary comes from the community build
  [DomoticX/whisper.cpp-windows-vulkan](https://github.com/DomoticX/whisper.cpp-windows-vulkan),
  pinned to release **v1.0** with the zip's SHA-256 recorded in `setup-vulkan.py`.
  The download is verified against that hash before extraction, extraction refuses
  path traversal, and `whisper-server.exe` itself is re-verified on every run —
  a tampered or changed artifact is quarantined, never executed.
- **Models** — downloaded from the official `ggerganov/whisper.cpp` mirror on Hugging Face,
  pinned to an exact repository revision (never `main`) and verified against per-model
  SHA-256 hashes recorded in `setup-vulkan.py`.
- **Updating a pin** is a deliberate act: fetch the new artifact, test it, and record its
  hash in the same commit that changes the URL.

## AMD — RDNA 1 (ROCm 6.2)

**Requirements:** AMD GPU with RDNA 1 architecture (RX 5000 series)

> **Note:** The portable exe AMD variant ships with RDNA 2+ support. RDNA 1 users should install via pip or pipx.

1. Follow the instructions at **[ctranslate2-rocm-rdna1](https://github.com/PinW/ctranslate2-rocm-rdna1)** to install the custom wheel and prerequisites
   - For pip: `pip install <wheel> --force-reinstall --no-deps`
   - For pipx: `pipx runpip parley install <wheel> --force-reinstall --no-deps`
2. Set `device: cuda` and `compute_type: float16`
3. Restart Parley

## CPU (no setup needed)

CPU mode works out of the box with no extra dependencies.

- Use `compute_type: int8` for the best speed (default)
- `tiny` and `base` models transcribe quickly on most machines
- `small` is usable but slower — larger models will be very slow on CPU
