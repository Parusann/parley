# vulkan_setup.py
# Installs the whisper.cpp *Vulkan* GPU runtime (server binary + ggml model) so
# the `whisper_cpp_server` backend works on Windows + AMD. Invoked as
# `parley --setup-vulkan` (works for pip installs) or via the repo's
# setup-vulkan.py shim. Every artifact is pinned to an exact version and
# verified against a SHA-256 recorded here before it is trusted.
# Idempotent — re-run any time; existing files are re-verified, not re-downloaded.
#
# PROVENANCE — read before updating any pin:
# * whisper.cpp upstream (ggml-org) publishes no Windows Vulkan release asset
#   (checked v1.9.1, 2026-07-10: CPU/BLAS/CUDA only), so the server binary comes
#   from the community build DomoticX/whisper.cpp-windows-vulkan, release v1.0.
#   The zip's SHA-256 below was recorded from that release on 2026-07-10.
# * Models come from the official ggerganov/whisper.cpp mirror on Hugging Face,
#   pinned to an exact repo revision (not `main`) with per-file SHA-256 taken
#   from the Hugging Face LFS metadata for that revision.
# * To bump a pin: download the new artifact, verify it works, record its
#   SHA-256 here in the same commit that changes the URL. Never point at a
#   mutable ref (branch/latest) and never skip the hash.

import hashlib
import os
import re
import subprocess
import sys
import zipfile

# Prebuilt whisper.cpp Windows Vulkan binaries (bundles ggml-vulkan.dll + whisper-server.exe).
BIN_ZIP_URL = "https://github.com/DomoticX/whisper.cpp-windows-vulkan/releases/download/v1.0/whisper.cpp-windows-vulkan.zip"
BIN_ZIP_SHA256 = "b40e4284f9dcebb27f35e8685c60291b6e3c643df431a0933044bd6b4bd7090a"
SERVER_EXE_SHA256 = "50609723e8b3ec412f9dbed664c110c0e6f9c41ca6b5c95a96cdd8f8beb2a863"

# Official ggerganov/whisper.cpp model mirror, pinned to an exact revision.
MODEL_REPO_REVISION = "5359861c739e955e79d9a303bcbc70fb988958b1"
MODEL_SHA256 = {
    "tiny":           "be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21",
    "tiny.en":        "921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f",
    "base":           "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe",
    "base.en":        "a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002",
    "small":          "1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b",
    "small.en":       "c6138d6d58ecc8322097e0f987c32f1be8bb0a18532a3f88f734d1bbf9c41e5d",
    "medium":         "6c14d5adee5f86394037b4e4e8b59f1673b6cee10e3cf0b11bbdbee79c156208",
    "medium.en":      "cc37e93478338ec7700281a7ac30a10128929eb8f427dda2e865faa8f6da4356",
    "large-v3":       "64d182b440b98d5203c4f9bd541544d84c605196c4f7b845dfa11fb23594d1e2",
    "large-v3-turbo": "1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69",
}

_MODEL_NAME_RE = re.compile(r"[A-Za-z0-9.\-]+")


def runtime_dir() -> str:
    """Home of the Vulkan binary + ggml models.

    Order: env override → repo `runtime/` next to a source checkout → per-user
    data dir for installed (pip/pipx/pyapp) environments. Kept out of the
    roaming config dir on purpose: multi-GB binaries don't belong in %APPDATA%.
    """
    env = os.environ.get('WHISPER_CPP_VULKAN_DIR')
    if env:
        return env
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(os.path.dirname(pkg_dir))
    dev = os.path.join(repo_root, 'runtime', 'whisper-cpp-vulkan')
    if os.path.isdir(dev) or 'site-packages' not in pkg_dir:
        return dev
    base = os.environ.get('LOCALAPPDATA') or os.path.expanduser(os.path.join('~', '.local', 'share'))
    return os.path.join(base, 'parley', 'whisper-cpp-vulkan')


def model_url(model: str) -> str:
    return f"https://huggingface.co/ggerganov/whisper.cpp/resolve/{MODEL_REPO_REVISION}/ggml-{model}.bin"


def _download(url: str, dst: str) -> None:
    print(f"  downloading {url}")
    print(f"    -> {dst}")
    # curl.exe ships with Windows 10+; -4 avoids IPv6 stalls on hosts with
    # broken IPv6 routes (AAAA published but unreachable).
    curl = "curl.exe" if sys.platform == "win32" else "curl"
    subprocess.run([curl, "-4", "-L", "--fail", "--retry", "3", "-o", dst, url], check=True)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify(path: str, expected: str, label: str) -> None:
    actual = _sha256(path)
    if actual != expected:
        os.replace(path, path + ".quarantine")
        raise SystemExit(
            f"\nERROR: SHA-256 mismatch for {label}\n"
            f"  expected {expected}\n"
            f"  actual   {actual}\n"
            f"  The file was moved to {path}.quarantine — do NOT run it.\n"
            f"  This means the upstream artifact changed since it was pinned. "
            f"Re-verify provenance before updating the pin in parley/vulkan_setup.py."
        )
    print(f"  ✓ verified {label} (sha256 {expected[:12]}…)")


def _safe_extract(zip_path: str, dest: str) -> None:
    """zipfile.extractall with zip-slip protection: every member must land inside dest."""
    dest_root = os.path.realpath(dest)
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            target = os.path.realpath(os.path.join(dest_root, member.filename))
            if not (target == dest_root or target.startswith(dest_root + os.sep)):
                raise SystemExit(f"ERROR: zip member escapes target dir: {member.filename}")
        zf.extractall(dest_root)


def main(model: str = None) -> int:
    model = model or os.environ.get("WHISPER_GGML_MODEL", "large-v3-turbo")
    if not _MODEL_NAME_RE.fullmatch(model):
        raise SystemExit(f"ERROR: invalid model name: {model!r} "
                         "(letters, digits, dots and dashes only)")

    runtime = runtime_dir()
    models = os.path.join(runtime, "models")
    os.makedirs(models, exist_ok=True)

    server_exe = os.path.join(runtime, "whisper-server.exe")
    if not os.path.isfile(server_exe):
        zip_path = os.path.join(runtime, "_vulkan-dl.zip")
        _download(BIN_ZIP_URL, zip_path)
        _verify(zip_path, BIN_ZIP_SHA256, "whisper.cpp Vulkan zip")
        _safe_extract(zip_path, runtime)
        os.remove(zip_path)
        print("  extracted whisper.cpp Vulkan binaries")
    # Verify the binary we are about to trust, whether fresh or pre-existing.
    _verify(server_exe, SERVER_EXE_SHA256, "whisper-server.exe")

    model_path = os.path.join(models, f"ggml-{model}.bin")
    expected = MODEL_SHA256.get(model)
    if not os.path.isfile(model_path):
        if expected is None:
            print(f"  WARNING: no pinned SHA-256 for model '{model}' — it will be "
                  f"downloaded from the pinned revision but cannot be verified.")
        _download(model_url(model), model_path)
    if expected is not None:
        _verify(model_path, expected, f"ggml-{model}.bin")
    else:
        print(f"  (unpinned model '{model}': sha256 {_sha256(model_path)})")

    print("\nDone. Runtime installed at:")
    print(f"  {runtime}")
    print(f"\nEnable it with:  whisper.backend: whisper_cpp_server   and   whisper.model: {model}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
