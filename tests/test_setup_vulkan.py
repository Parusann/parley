# Tests for parley.vulkan_setup — the supply-chain gate for the GPU runtime.
# These pin the security behavior: artifacts must be hash-verified, zip
# extraction must refuse path traversal, and pins must stay well-formed.

import hashlib
import io
import os
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from parley import vulkan_setup as setup_vulkan

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class PinTableTests(unittest.TestCase):
    def test_binary_pins_are_wellformed(self):
        self.assertTrue(setup_vulkan.BIN_ZIP_URL.startswith("https://"))
        self.assertRegex(setup_vulkan.BIN_ZIP_SHA256, _HEX64)
        self.assertRegex(setup_vulkan.SERVER_EXE_SHA256, _HEX64)

    def test_binary_url_points_at_a_release_tag_not_latest(self):
        self.assertIn("/releases/download/", setup_vulkan.BIN_ZIP_URL)
        self.assertNotIn("/latest", setup_vulkan.BIN_ZIP_URL)

    def test_model_pins_are_wellformed(self):
        self.assertRegex(setup_vulkan.MODEL_REPO_REVISION, r"^[0-9a-f]{40}$")
        self.assertIn("large-v3-turbo", setup_vulkan.MODEL_SHA256)
        for name, digest in setup_vulkan.MODEL_SHA256.items():
            self.assertRegex(digest, _HEX64, f"model {name}")

    def test_model_url_uses_pinned_revision_not_main(self):
        url = setup_vulkan.model_url("large-v3-turbo")
        self.assertIn(setup_vulkan.MODEL_REPO_REVISION, url)
        self.assertNotIn("/resolve/main/", url)

    def test_model_name_is_validated(self):
        with self.assertRaises(SystemExit):
            setup_vulkan.main(model="../../evil")


class VerifyTests(unittest.TestCase):
    def test_matching_hash_passes(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "artifact.bin")
            with open(p, "wb") as f:
                f.write(b"trusted bytes")
            good = hashlib.sha256(b"trusted bytes").hexdigest()
            setup_vulkan._verify(p, good, "artifact")  # must not raise
            self.assertTrue(os.path.isfile(p))

    def test_mismatch_quarantines_and_aborts(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "artifact.bin")
            with open(p, "wb") as f:
                f.write(b"tampered bytes")
            wrong = "0" * 64
            with self.assertRaises(SystemExit):
                setup_vulkan._verify(p, wrong, "artifact")
            self.assertFalse(os.path.isfile(p), "tampered file must not stay in place")
            self.assertTrue(os.path.isfile(p + ".quarantine"))


class SafeExtractTests(unittest.TestCase):
    def _zip_with(self, *names) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for n in names:
                zf.writestr(n, b"x")
        return buf.getvalue()

    def test_normal_members_extract(self):
        with tempfile.TemporaryDirectory() as td:
            zp = os.path.join(td, "a.zip")
            with open(zp, "wb") as f:
                f.write(self._zip_with("bin/server.exe", "readme.txt"))
            dest = os.path.join(td, "out")
            os.makedirs(dest)
            setup_vulkan._safe_extract(zp, dest)
            self.assertTrue(os.path.isfile(os.path.join(dest, "bin", "server.exe")))

    def test_traversal_member_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            zp = os.path.join(td, "evil.zip")
            with open(zp, "wb") as f:
                f.write(self._zip_with("../escaped.txt"))
            dest = os.path.join(td, "out")
            os.makedirs(dest)
            with self.assertRaises(SystemExit):
                setup_vulkan._safe_extract(zp, dest)
            self.assertFalse(os.path.exists(os.path.join(td, "escaped.txt")))


if __name__ == "__main__":
    unittest.main()
