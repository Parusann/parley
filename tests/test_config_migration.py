# One-time config-dir migration: pre-1.0 installs kept settings in `whisperkey`;
# 1.0 must copy (never move) them into the new `parley` dir exactly once.

import os
import sys
import types
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from parley.utils import _migrate_legacy_config_dir


def _fake_paths(legacy: Path):
    mod = types.SimpleNamespace()
    mod.get_legacy_app_data_path = lambda: legacy
    return mod


def test_copies_legacy_dir_when_new_missing(tmp_path):
    legacy = tmp_path / "whisperkey"
    legacy.mkdir()
    (legacy / "user_settings.yaml").write_text("whisper:\n  model: small\n")
    (legacy / "sub").mkdir()
    (legacy / "sub" / "stats.json").write_text("{}")
    new = tmp_path / "parley"

    _migrate_legacy_config_dir(_fake_paths(legacy), new)

    assert (new / "user_settings.yaml").read_text() == "whisper:\n  model: small\n"
    assert (new / "sub" / "stats.json").exists()
    # Non-destructive: the legacy dir must survive untouched.
    assert (legacy / "user_settings.yaml").exists()


def test_existing_new_dir_is_never_overwritten(tmp_path):
    legacy = tmp_path / "whisperkey"
    legacy.mkdir()
    (legacy / "user_settings.yaml").write_text("old: true\n")
    new = tmp_path / "parley"
    new.mkdir()
    (new / "user_settings.yaml").write_text("current: true\n")

    _migrate_legacy_config_dir(_fake_paths(legacy), new)

    assert (new / "user_settings.yaml").read_text() == "current: true\n"


def test_no_legacy_dir_is_a_noop(tmp_path):
    new = tmp_path / "parley"
    _migrate_legacy_config_dir(_fake_paths(tmp_path / "missing"), new)
    assert not new.exists()  # caller mkdirs afterwards; migration itself is a no-op


def test_platform_without_legacy_helper_is_safe(tmp_path):
    # macOS/Windows ship get_legacy_app_data_path, but a future platform may not.
    _migrate_legacy_config_dir(types.SimpleNamespace(), tmp_path / "parley")
