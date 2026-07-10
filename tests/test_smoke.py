import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


class HotkeyParsingTests(unittest.TestCase):
    def test_parse_hotkey_splits_on_plus(self):
        from parley.utils import parse_hotkey
        self.assertEqual(parse_hotkey("ctrl+a"), ["ctrl", "a"])
        self.assertEqual(parse_hotkey("Ctrl+Shift+F1"), ["ctrl", "shift", "f1"])

    def test_parse_hotkey_handles_empty(self):
        from parley.utils import parse_hotkey
        self.assertEqual(parse_hotkey(""), [])
        self.assertEqual(parse_hotkey(None), [])

    def test_beautify_hotkey_uppercases(self):
        from parley.utils import beautify_hotkey
        self.assertEqual(beautify_hotkey("ctrl+a"), "CTRL+A")
        self.assertEqual(beautify_hotkey(""), "")


class VersionMetadataTests(unittest.TestCase):
    def test_pyproject_has_local_name(self):
        import tomllib
        with open(ROOT / "pyproject.toml", "rb") as f:
            data = tomllib.load(f)
        self.assertEqual(data["project"]["name"], "parley")
        self.assertIn("parley", data["project"]["scripts"])
        self.assertEqual(data["project"]["version"], "1.0.0")

    def test_no_orphan_update_checker(self):
        self.assertFalse((ROOT / "src" / "parley" / "update_checker.py").exists())

    def test_main_does_not_import_update_checker(self):
        main_src = (ROOT / "src" / "parley" / "main.py").read_text(encoding="utf-8")
        self.assertNotIn("update_checker", main_src)
        self.assertNotIn("check_for_updates", main_src)


class VoiceCommandsDefaultsTests(unittest.TestCase):
    def test_no_registry_writes_in_defaults(self):
        defaults = (ROOT / "src" / "parley" / "commands.defaults.yaml").read_text(encoding="utf-8")
        self.assertNotIn("reg add", defaults.lower())


class WhisperBackendTests(unittest.TestCase):
    def test_default_backend_is_faster_whisper(self):
        from ruamel.yaml import YAML
        path = ROOT / "src" / "parley" / "config.defaults.yaml"
        with open(path, encoding="utf-8") as f:
            cfg = YAML().load(f)
        self.assertEqual(cfg["whisper"].get("backend"), "faster_whisper")

    def test_whisper_cpp_module_imports_lazily(self):
        # Import the module — the heavy pywhispercpp import is lazy inside __init__
        from parley import whisper_engine_cpp
        self.assertTrue(hasattr(whisper_engine_cpp, 'WhisperEngineCpp'))

    def test_optional_dep_declared(self):
        import tomllib
        with open(ROOT / "pyproject.toml", "rb") as f:
            data = tomllib.load(f)
        extras = data.get("project", {}).get("optional-dependencies", {})
        self.assertIn("whispercpp", extras)
        self.assertTrue(any("pywhispercpp" in d for d in extras["whispercpp"]))


class InstanceManagerTests(unittest.TestCase):
    def test_exposes_cleanup_pid_file(self):
        src = (ROOT / "src" / "parley" / "instance_manager.py").read_text(encoding="utf-8")
        self.assertIn("def cleanup_pid_file", src)
        self.assertIn("os.kill", src)
        self.assertIn("_wait_for_lock", src)

    def test_main_calls_cleanup_pid_file(self):
        main_src = (ROOT / "src" / "parley" / "main.py").read_text(encoding="utf-8")
        self.assertIn("cleanup_pid_file(instance_name)", main_src)


class TextPostprocessTests(unittest.TestCase):
    def test_strip_filler_words(self):
        from parley.text_postprocess import postprocess
        cfg = {'strip_filler_words': True}
        self.assertEqual(postprocess("um, hello like world", cfg), "hello world")

    def test_capitalize_first(self):
        from parley.text_postprocess import postprocess
        cfg = {'capitalize_first': True}
        self.assertEqual(postprocess("hello world", cfg), "Hello world")

    def test_ensure_punctuation(self):
        from parley.text_postprocess import postprocess
        cfg = {'ensure_punctuation': True}
        self.assertEqual(postprocess("hello world", cfg), "hello world.")
        self.assertEqual(postprocess("hello world.", cfg), "hello world.")

    def test_postprocess_passthrough_when_empty(self):
        from parley.text_postprocess import postprocess
        self.assertEqual(postprocess("hello", {}), "hello")
        self.assertEqual(postprocess("", {'capitalize_first': True}), "")

    def test_strip_trailing_period(self):
        from parley.text_postprocess import postprocess
        cfg = {'strip_trailing_period': True}
        self.assertEqual(postprocess("hello world.", cfg), "hello world")
        self.assertEqual(postprocess("done.\n", cfg), "done\n")
        self.assertEqual(postprocess("e.g..", cfg), "e.g..")  # don't touch ellipsis-like
        self.assertEqual(postprocess("no period here", cfg), "no period here")

    def test_inline_formatting_basics(self):
        from parley.text_postprocess import postprocess
        cfg = {'inline_formatting': True}
        self.assertEqual(postprocess("hello comma world period", cfg), "hello, world.")
        self.assertEqual(postprocess("what time is it question mark", cfg), "what time is it?")

    def test_inline_formatting_custom_replaces_defaults(self):
        # Custom list replaces the English defaults (non-English use case).
        from parley.text_postprocess import postprocess
        cfg = {
            'inline_formatting': True,
            'inline_formatting_replacements': [
                {'phrase': 'przecinek', 'replacement': ','},
                {'phrase': 'strzałka', 'replacement': '→'},
            ],
        }
        self.assertEqual(postprocess("tekst przecinek dalej", cfg), "tekst, dalej")
        self.assertEqual(postprocess("a strzałka b", cfg), "a → b")
        # English defaults are NOT active in replace mode.
        self.assertEqual(postprocess("hello comma world", cfg), "hello comma world")

    def test_inline_formatting_extend_keeps_defaults(self):
        from parley.text_postprocess import postprocess
        cfg = {
            'inline_formatting': True,
            'inline_formatting_extend': True,
            'inline_formatting_replacements': [{'phrase': 'arrow', 'replacement': '→'}],
        }
        # both the English default AND the custom phrase apply
        self.assertEqual(postprocess("hello comma arrow there", cfg), "hello, → there")

    def test_absorb_cleans_whisper_prosody_punctuation(self):
        # Whisper adds its own commas/periods around spoken cue words; absorb should
        # eat them so the output isn't polluted (Discussion #1 bug report).
        from parley.text_postprocess import postprocess
        cfg = {
            'inline_formatting': True,
            'inline_formatting_absorb_punctuation': True,
            'inline_formatting_replacements': [
                {'phrase': 'comma', 'replacement': ', '},
                {'phrase': 'arrow', 'replacement': ' → '},
            ],
        }
        whisper_out = "Hello, comma, and welcome, arrow. Common greeting."
        self.assertEqual(postprocess(whisper_out, cfg),
                         "Hello, and welcome → Common greeting.")

    def test_absorb_off_leaves_prosody_artifacts(self):
        # Documents that WITHOUT absorb the artifacts remain (opt-in, no regression).
        from parley.text_postprocess import postprocess
        cfg = {
            'inline_formatting': True,
            'inline_formatting_replacements': [{'phrase': 'comma', 'replacement': ','}],
        }
        self.assertIn(",,", postprocess("a, comma, b", cfg))

    def test_absorb_with_builtins_does_not_glue_or_eat_breaks(self):
        # SEC #4: absorb + built-in English cue words must keep spacing and newlines.
        from parley.text_postprocess import postprocess
        cfg = {'inline_formatting': True, 'inline_formatting_absorb_punctuation': True}
        self.assertEqual(postprocess("Hello, comma, world.", cfg), "Hello, world.")
        # "new paragraph" break must survive a following cue's absorb
        out = postprocess("first new paragraph second period", cfg)
        self.assertIn("\n\n", out)

    def test_absorb_respects_word_boundary(self):
        # "comma" must not fire inside "commander"/"Common".
        from parley.text_postprocess import postprocess
        cfg = {
            'inline_formatting': True,
            'inline_formatting_absorb_punctuation': True,
            'inline_formatting_replacements': [{'phrase': 'comma', 'replacement': ', '}],
        }
        out = postprocess("the commander said comma done", cfg)
        self.assertIn("commander", out)
        self.assertNotIn("commaander", out)

    def test_inline_formatting_replacement_is_literal(self):
        # A replacement containing regex-special chars must be inserted literally.
        from parley.text_postprocess import postprocess
        cfg = {
            'inline_formatting': True,
            'inline_formatting_replacements': [{'phrase': 'backref', 'replacement': r'\1\g<0>'}],
        }
        self.assertEqual(postprocess("x backref y", cfg), r"x \1\g<0> y")

    def test_ollama_polish_handles_curly_braces_in_text(self):
        from parley.text_postprocess import _ollama_polish
        cfg = {'enabled': True, 'endpoint': 'http://127.0.0.1:0', 'timeout': 0.1,
               'prompt': 'Polish:\n\n{text}'}
        result = _ollama_polish("config = {a: 1, b: 2}", cfg)
        self.assertEqual(result, '')

    def test_ollama_polish_handles_curly_braces_in_prompt(self):
        from parley.text_postprocess import _ollama_polish
        cfg = {'enabled': True, 'endpoint': 'http://127.0.0.1:0', 'timeout': 0.1,
               'prompt': 'Format: {format_var} not a placeholder. {text}'}
        result = _ollama_polish("hi", cfg)
        self.assertEqual(result, '')


class AppRulesShapeTests(unittest.TestCase):
    def test_defaults_yaml_is_valid(self):
        from ruamel.yaml import YAML
        path = ROOT / "src" / "parley" / "app_rules.defaults.yaml"
        self.assertTrue(path.exists())
        with open(path, encoding="utf-8") as f:
            data = YAML().load(f)
        self.assertIn('rules', data)
        self.assertIsInstance(data['rules'], list)
        for rule in data['rules']:
            self.assertIn('match', rule)


class AppRulesFormattingTests(unittest.TestCase):
    def test_formatting_overrides_extracts_only_format_keys(self):
        from parley.app_rules import formatting_overrides
        rule = {
            'match': ['code.exe'],
            'auto_paste': False,          # delivery key, not a formatting key
            'capitalize_first': False,
            'ensure_punctuation': False,
        }
        self.assertEqual(
            formatting_overrides(rule),
            {'capitalize_first': False, 'ensure_punctuation': False},
        )

    def test_formatting_overrides_empty_and_none(self):
        from parley.app_rules import formatting_overrides
        self.assertEqual(formatting_overrides(None), {})
        self.assertEqual(formatting_overrides({'match': ['x'], 'auto_send': True}), {})

    def test_merge_overrides_global_postprocess(self):
        # Simulate the pipeline merge: rule formatting overrides global config.
        from parley.app_rules import formatting_overrides
        global_cfg = {'capitalize_first': True, 'ensure_punctuation': True, 'inline_formatting': True}
        rule = {'capitalize_first': False, 'ensure_punctuation': False}
        merged = {**global_cfg, **formatting_overrides(rule)}
        self.assertFalse(merged['capitalize_first'])
        self.assertFalse(merged['ensure_punctuation'])
        self.assertTrue(merged['inline_formatting'])  # untouched key inherited


class TransformsShapeTests(unittest.TestCase):
    def test_defaults_yaml_is_valid(self):
        from ruamel.yaml import YAML
        path = ROOT / "src" / "parley" / "transforms.defaults.yaml"
        self.assertTrue(path.exists())
        with open(path, encoding="utf-8") as f:
            data = YAML().load(f)
        self.assertIn('transforms', data)
        for t in data['transforms']:
            self.assertIn('name', t)
            self.assertIn('prompt', t)

    def test_transforms_manager_loads(self):
        from parley.transforms import TransformsManager
        tm = TransformsManager()
        names = [t.get('name') for t in tm.list_transforms()]
        self.assertIn('polish', names)
        self.assertIn('prompt-engineer', names)


class StreakComputationTests(unittest.TestCase):
    def test_streak_basic(self):
        import datetime
        from parley.stats import _compute_streaks
        today = datetime.date(2026, 5, 18)
        active = {
            '2026-05-18', '2026-05-17', '2026-05-16',
            '2026-05-10', '2026-05-09',
        }
        current, longest = _compute_streaks(active, today)
        self.assertEqual(current, 3)
        self.assertEqual(longest, 3)

    def test_streak_breaks_yesterday(self):
        import datetime
        from parley.stats import _compute_streaks
        today = datetime.date(2026, 5, 18)
        active = {'2026-05-10'}
        current, longest = _compute_streaks(active, today)
        self.assertEqual(current, 0)
        self.assertEqual(longest, 1)


class ProfilesShapeTests(unittest.TestCase):
    def test_defaults_yaml_is_valid(self):
        from ruamel.yaml import YAML
        path = ROOT / "src" / "parley" / "profiles.defaults.yaml"
        self.assertTrue(path.exists())
        with open(path, encoding="utf-8") as f:
            data = YAML().load(f)
        self.assertIn('profiles', data)
        self.assertIn('dictation', data['profiles'])


class UtilsTests(unittest.TestCase):
    def test_resolve_asset_path_relative(self):
        from parley.utils import resolve_asset_path
        result = resolve_asset_path("config.defaults.yaml")
        self.assertTrue(result.endswith("config.defaults.yaml"))

    def test_resolve_asset_path_absolute_passthrough(self):
        from parley.utils import resolve_asset_path
        absolute = os.path.abspath(__file__)
        self.assertEqual(resolve_asset_path(absolute), absolute)


class NoiseSuppresionTests(unittest.TestCase):
    def test_passthrough_without_noisereduce(self):
        import sys
        import unittest.mock as mock
        import numpy as np
        sys.modules.pop('parley.noise_suppression', None)
        import importlib
        with mock.patch.dict(sys.modules, {'noisereduce': None}):
            import parley.noise_suppression as ns_mod
            importlib.reload(ns_mod)
            audio = np.zeros(16000, dtype=np.float32)
            result = ns_mod.apply_noise_reduction(audio, 16000, 0.75)
            np.testing.assert_array_equal(result, audio)

    def test_config_in_defaults(self):
        from ruamel.yaml import YAML
        path = ROOT / "src" / "parley" / "config.defaults.yaml"
        with open(path, encoding="utf-8") as f:
            cfg = YAML().load(f)
        ns = cfg["audio"]["noise_suppression"]
        self.assertFalse(ns["enabled"])
        self.assertAlmostEqual(float(ns["strength"]), 0.75, places=2)


class UpdateCheckTests(unittest.TestCase):
    def test_is_newer_basic(self):
        from parley.update_check import _is_newer
        self.assertTrue(_is_newer("1.0.0", "0.9.0"))
        self.assertFalse(_is_newer("0.9.0", "1.0.0"))
        self.assertFalse(_is_newer("0.9.0", "0.9.0"))

    def test_no_network_when_disabled(self):
        import unittest.mock as mock
        from parley.update_check import maybe_check_for_update
        with mock.patch('parley.update_check._check_in_background') as m:
            maybe_check_for_update(lambda _: None, {'enabled': False})
            m.assert_not_called()

    def test_update_check_in_config_defaults(self):
        from ruamel.yaml import YAML
        path = ROOT / "src" / "parley" / "config.defaults.yaml"
        with open(path, encoding="utf-8") as f:
            cfg = YAML().load(f)
        self.assertIn("update_check", cfg)
        self.assertFalse(cfg["update_check"]["enabled"])


class TranscriptLogTests(unittest.TestCase):
    def test_record_and_load(self):
        import tempfile
        import unittest.mock as mock
        from parley import transcript_log
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch('parley.transcript_log.get_user_app_data_path', return_value=tmpdir):
                transcript_log.record_transcript("Hello world", app="test.exe", duration_s=1.5)
                entries = transcript_log.load_transcripts()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["text"], "Hello world")
        self.assertEqual(entries[0]["app"], "test.exe")

    def test_empty_text_not_logged(self):
        import tempfile
        import unittest.mock as mock
        from parley import transcript_log
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch('parley.transcript_log.get_user_app_data_path', return_value=tmpdir):
                transcript_log.record_transcript("", app="test.exe")
                entries = transcript_log.load_transcripts()
        self.assertEqual(len(entries), 0)


class SettingsUiModuleTests(unittest.TestCase):
    def test_module_importable(self):
        from parley import settings_ui
        self.assertTrue(hasattr(settings_ui, 'run_settings_window'))


class OnboardingBannerTests(unittest.TestCase):
    # UX #4b: the first-launch banner must reflect the user's ACTUAL configured
    # hotkeys (and notify message), not hardcoded Windows defaults.
    def test_banner_uses_supplied_hotkeys(self):
        import io
        import contextlib
        import unittest.mock as mock
        from parley import onboarding_tutorial
        seen = {}

        def fake_notify(msg):
            seen['notify'] = msg

        buf = io.StringIO()
        with mock.patch.object(onboarding_tutorial, 'mark_complete', lambda: None):
            with contextlib.redirect_stdout(buf):
                onboarding_tutorial.show_console_welcome(
                    hotkeys={'record': 'F9', 'rephrase': 'F10', 'command': 'F11',
                             'cancel': 'F12', 'pause': 'F8'},
                    notify=fake_notify,
                )
        out = buf.getvalue()
        self.assertIn('F9', out)
        self.assertIn('F10', out)
        self.assertNotIn('Ctrl+Win', out)  # no leaked default
        self.assertEqual(seen.get('notify'), 'Welcome! Hold F9 to start dictating.')


class PostprocessHotReloadTests(unittest.TestCase):
    # UX #1/#3: editing postprocess in user_settings.yaml applies on next dictation
    # (get_postprocess_config) without an app restart.
    def test_postprocess_reloads_on_file_change(self):
        import os
        import tempfile
        import unittest.mock as mock
        from ruamel.yaml import YAML
        # Import the submodule object so patch.object resolves it without relying
        # on it already being an attribute of the package (string-target patches
        # fail under a fresh `unittest` run where nothing imported it first).
        # config_manager pulls `.platform`, which eagerly imports the OS-specific
        # backend (win32api / AppKit) — absent in the lean CI env, so skip there.
        try:
            from parley import config_manager as cm_mod
        except Exception:
            self.skipTest("config_manager not importable on this platform")
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(cm_mod, 'get_user_app_data_path', return_value=d):
                cm = cm_mod.ConfigManager(quiet=True)
                self.assertFalse(cm.get_postprocess_config().get('strip_filler_words'))
                sp = os.path.join(d, 'user_settings.yaml')
                base = cm._postprocess_mtime or os.path.getmtime(sp)
                with open(sp, 'w', encoding='utf-8') as f:
                    YAML().dump({'postprocess': {'strip_filler_words': True}}, f)
                os.utime(sp, (base + 10, base + 10))  # guarantee a newer mtime
                self.assertTrue(cm.get_postprocess_config().get('strip_filler_words'))


class SettingsResetTests(unittest.TestCase):
    # SEC #1: "Reset to defaults" promises hotwords survive — verify they do.
    def test_reset_preserves_hotwords(self):
        import tempfile, os
        from ruamel.yaml import YAML
        from parley.settings_ui import reset_settings_preserving_hotwords
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'user_settings.yaml')
            with open(path, 'w', encoding='utf-8') as f:
                YAML().dump({'whisper': {'model': 'small', 'hotwords': ['Kubernetes', 'Parusan']},
                             'clipboard': {'auto_paste': False}}, f)
            preserved = reset_settings_preserving_hotwords(path)
            self.assertEqual(preserved, ['Kubernetes', 'Parusan'])
            with open(path, encoding='utf-8') as f:
                after = YAML().load(f)
            # hotwords kept, everything else gone (back to defaults)
            self.assertEqual(list(after['whisper']['hotwords']), ['Kubernetes', 'Parusan'])
            self.assertNotIn('clipboard', after)
            self.assertNotIn('model', after['whisper'])

    def test_reset_with_no_hotwords_removes_file(self):
        import tempfile, os
        from ruamel.yaml import YAML
        from parley.settings_ui import reset_settings_preserving_hotwords
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'user_settings.yaml')
            with open(path, 'w', encoding='utf-8') as f:
                YAML().dump({'clipboard': {'auto_paste': False}}, f)
            self.assertEqual(reset_settings_preserving_hotwords(path), [])
            self.assertFalse(os.path.exists(path))  # clean wipe when nothing to keep


class HistoryWindowModuleTests(unittest.TestCase):
    def test_module_importable(self):
        from parley import history_window
        self.assertTrue(hasattr(history_window, 'show_history'))


class ReleaseWorkflowTests(unittest.TestCase):
    def test_release_workflow_exists(self):
        self.assertTrue((ROOT / ".github" / "workflows" / "release.yml").exists())

    def test_release_workflow_triggers_on_tag(self):
        with open(ROOT / ".github" / "workflows" / "release.yml", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("tags:", content)
        self.assertIn("v*", content)
        self.assertIn("pypa/gh-action-pypi-publish", content)


class PyprojectOptionalDepsTests(unittest.TestCase):
    def test_noise_optional_dep(self):
        import tomllib
        with open(ROOT / "pyproject.toml", "rb") as f:
            data = tomllib.load(f)
        extras = data.get("project", {}).get("optional-dependencies", {})
        self.assertIn("noise", extras)
        self.assertTrue(any("noisereduce" in d for d in extras["noise"]))


class SelftestModuleTests(unittest.TestCase):
    def test_module_importable(self):
        from parley import selftest
        self.assertTrue(hasattr(selftest, 'run_selftest'))

    def test_report_helper(self):
        import io
        import sys
        from parley.selftest import _report
        failures = []
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            _report(True, "looks good", failures, "fake")
            _report(False, ("uh oh", "fix-me"), failures, "fake-2")
        finally:
            sys.stdout = old_stdout
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0][0], "fake-2")


class FirstRunTests(unittest.TestCase):
    def test_flag_file_lifecycle(self):
        import tempfile
        import unittest.mock as mock
        from parley import first_run

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch('parley.first_run.get_user_app_data_path', return_value=tmpdir):
                self.assertTrue(first_run.is_first_run())
                first_run.mark_first_run_complete()
                self.assertFalse(first_run.is_first_run())

    def test_show_welcome_module_importable(self):
        from parley import first_run
        self.assertTrue(hasattr(first_run, 'show_welcome_window'))


class CheatSheetTests(unittest.TestCase):
    def test_module_importable(self):
        from parley import cheat_sheet
        self.assertTrue(hasattr(cheat_sheet, 'show_cheat_sheet'))


class BundleLogsTests(unittest.TestCase):
    def test_redaction_replaces_usernames(self):
        from parley.bundle_logs import _redact
        sample = "Path: C:\\Users\\rohit\\AppData\\Roaming and email me at test@example.com"
        red = _redact(sample)
        self.assertNotIn("rohit", red.lower())
        self.assertNotIn("test@example.com", red)
        self.assertIn("<USER>", red)
        self.assertIn("<EMAIL>", red)

    def test_redaction_macos_linux_paths(self):
        from parley.bundle_logs import _redact
        self.assertIn("<USER>", _redact("/Users/alice/.parley/"))
        self.assertIn("<USER>", _redact("/home/bob/log.txt"))

    def test_bundle_creates_zip(self):
        import io
        import sys
        import tempfile
        import unittest.mock as mock
        import zipfile
        from parley import bundle_logs

        with tempfile.TemporaryDirectory() as appdata:
            with tempfile.TemporaryDirectory() as out:
                output = f"{out}/bundle.zip"
                old_stdout = sys.stdout
                sys.stdout = io.StringIO()
                try:
                    with mock.patch('parley.bundle_logs.get_user_app_data_path', return_value=appdata), \
                         mock.patch('parley.bundle_logs._capture_doctor', return_value='[doctor mocked]'):
                        rc = bundle_logs.bundle_logs(output)
                finally:
                    sys.stdout = old_stdout
                self.assertEqual(rc, 0)
                with zipfile.ZipFile(output) as zf:
                    names = zf.namelist()
                self.assertIn('about.txt', names)
                self.assertIn('doctor.txt', names)


class LocalServerTests(unittest.TestCase):
    def test_module_importable(self):
        from parley import local_server
        self.assertTrue(hasattr(local_server, 'run_server'))
        self.assertEqual(local_server.DEFAULT_PORT, 7777)

    def test_decode_wav_fallback(self):
        import io
        import wave
        import numpy as np
        from parley.local_server import _decode_wav_fallback

        with io.BytesIO() as buf:
            with wave.open(buf, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                samples = (np.sin(2 * np.pi * 440 * np.arange(16000) / 16000) * 32767).astype(np.int16)
                wf.writeframes(samples.tobytes())
            wav_bytes = buf.getvalue()

        decoded = _decode_wav_fallback(wav_bytes)
        self.assertEqual(decoded.dtype, np.float32)
        self.assertEqual(len(decoded), 16000)
        self.assertGreater(float(np.max(np.abs(decoded))), 0.5)


class MainCliFlagsTests(unittest.TestCase):
    def test_main_registers_new_flags(self):
        main_src = (ROOT / "src" / "parley" / "main.py").read_text(encoding="utf-8")
        for flag in ('--selftest', '--cheat-sheet', '--bundle-logs', '--serve'):
            self.assertIn(flag, main_src, f"main.py should register {flag}")


class TroubleshootingDocTests(unittest.TestCase):
    def test_docs_exist(self):
        self.assertTrue((ROOT / "docs" / "troubleshooting.md").exists())
        self.assertTrue((ROOT / "docs" / "faq.md").exists())


class VersionBumpTests(unittest.TestCase):
    def test_pyproject_version(self):
        import tomllib
        with open(ROOT / "pyproject.toml", "rb") as f:
            data = tomllib.load(f)
        version = data["project"]["version"]
        major, minor = version.split('.')[:2]
        self.assertGreaterEqual((int(major), int(minor)), (0, 10))

    def test_citation_matches_pyproject(self):
        import tomllib
        with open(ROOT / "pyproject.toml", "rb") as f:
            pyproject_version = tomllib.load(f)["project"]["version"]
        citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
        self.assertIn(f"version: {pyproject_version}", citation,
                      "CITATION.cff version must match pyproject.toml")


class _FakeHandler:
    """Minimal stand-in for BaseHTTPRequestHandler for parser tests."""
    def __init__(self, body: bytes, boundary: str, content_length=None):
        import io
        self.headers = {
            'Content-Type': f'multipart/form-data; boundary={boundary}',
            'Content-Length': str(content_length if content_length is not None else len(body)),
        }
        self.rfile = io.BytesIO(body)


def _build_multipart(boundary: str, file_bytes: bytes) -> bytes:
    b = boundary.encode()
    return (
        b'--' + b + b'\r\n'
        b'Content-Disposition: form-data; name="file"; filename="a.wav"\r\n'
        b'Content-Type: application/octet-stream\r\n\r\n'
        + file_bytes + b'\r\n'
        b'--' + b + b'--\r\n'
    )


class LocalServerSecurityTests(unittest.TestCase):
    # SRV-1: binary audio ending in 0x2D ('-') must NOT be truncated.
    def test_parse_multipart_preserves_trailing_dashes(self):
        from parley.local_server import _parse_multipart
        audio = b'\x00\x01\x02\x2d\x2d\x2d'  # ends in three dashes
        body = _build_multipart('BoUnDaRy123', audio)
        fields = _parse_multipart(_FakeHandler(body, 'BoUnDaRy123'))
        self.assertEqual(fields['file']['data'], audio)

    # SRV-2: oversized Content-Length is rejected before the body is read.
    def test_oversized_content_length_rejected(self):
        from parley import local_server
        huge = local_server.MAX_UPLOAD_BYTES + 1
        handler = _FakeHandler(b'x', 'B', content_length=huge)
        with self.assertRaises(ValueError):
            local_server._parse_multipart(handler)

    # SEC #3: negative Content-Length must be rejected (read(-1) would drain socket).
    def test_negative_content_length_rejected(self):
        from parley import local_server
        handler = _FakeHandler(b'x', 'B', content_length=-1)
        with self.assertRaises(ValueError):
            local_server._parse_multipart(handler)


class BundleRedactionTests(unittest.TestCase):
    # SEC #2: URL credentials + secret query params masked in ALL bundled files
    # (this is what protects doctor.txt, not just user_settings.yaml).
    def test_redact_masks_url_credentials_and_tokens(self):
        from parley.bundle_logs import _redact
        out = _redact("Ollama post-edit: http://user:s3cret@ollama.host:11434 reachable")
        self.assertNotIn("s3cret", out)
        self.assertIn("<REDACTED>@", out)
        out2 = _redact("GET https://api.example.com/x?token=abc123&z=1")
        self.assertNotIn("abc123", out2)
        self.assertIn("<REDACTED>", out2)

    # PRIV-1: sensitive config fields are masked in user_settings.yaml.
    def test_redact_yaml_masks_sensitive_fields(self):
        from parley.bundle_logs import _redact_yaml
        sample = (
            "whisper:\n"
            "  hotwords: [SecretName, CodeWord]\n"
            "  initial_prompt: my private context\n"
            "postprocess:\n"
            "  ollama:\n"
            "    endpoint: http://user:pass@host:11434\n"
        )
        out = _redact_yaml(sample)
        self.assertNotIn('SecretName', out)
        self.assertNotIn('CodeWord', out)
        self.assertNotIn('private context', out)
        self.assertNotIn('user:pass', out)
        self.assertIn('<REDACTED>', out)

    def test_redact_yaml_masks_block_hotwords(self):
        from parley.bundle_logs import _redact_yaml
        sample = "whisper:\n  hotwords:\n    - Alpha\n    - Bravo\n  model: tiny\n"
        out = _redact_yaml(sample)
        self.assertNotIn('Alpha', out)
        self.assertNotIn('Bravo', out)
        self.assertIn('model: tiny', out)  # non-sensitive keys untouched


class VoiceCommandQuotingTests(unittest.TestCase):
    # VC-1: clipboard content is shell-quoted when expanded into a run: command.
    def test_shell_safe_quotes_clipboard(self):
        try:
            from parley.voice_commands import VoiceCommandManager
        except Exception:
            self.skipTest("voice_commands not importable on this platform")
        import shlex
        import unittest.mock as mock
        vc = VoiceCommandManager.__new__(VoiceCommandManager)
        with mock.patch('parley.voice_commands.pyperclip.paste', return_value='; rm -rf ~'):
            safe = vc._expand_template('echo ${clipboard}', shell_safe=True)
            raw = vc._expand_template('echo ${clipboard}', shell_safe=False)
        self.assertEqual(safe, 'echo ' + shlex.quote('; rm -rf ~'))
        self.assertEqual(raw, 'echo ; rm -rf ~')


class AutostartTests(unittest.TestCase):
    def test_module_and_api(self):
        from parley import autostart
        for fn in ('is_supported', 'is_enabled', 'enable', 'disable', 'toggle'):
            self.assertTrue(callable(getattr(autostart, fn, None)), f"missing {fn}")

    def test_is_supported_matches_platform(self):
        import sys
        from parley import autostart
        self.assertEqual(autostart.is_supported(), sys.platform in ('win32', 'darwin'))

    def test_launch_command_nonempty(self):
        from parley import autostart
        cmd = autostart._launch_command()
        self.assertIsInstance(cmd, list)
        self.assertTrue(cmd and cmd[0])

    def test_macos_plist_roundtrip(self):
        import tempfile
        import unittest.mock as mock
        from pathlib import Path
        from parley import autostart
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'agent.plist'
            with mock.patch('parley.autostart._mac_plist_path', return_value=p):
                self.assertFalse(autostart._mac_is_enabled())
                autostart._mac_enable()
                self.assertTrue(autostart._mac_is_enabled())
                content = p.read_text(encoding='utf-8')
                self.assertIn('RunAtLoad', content)
                self.assertIn('com.parusann.parley', content)
                autostart._mac_disable()
                self.assertFalse(autostart._mac_is_enabled())

    def test_main_registers_autostart_flags(self):
        main_src = (ROOT / "src" / "parley" / "main.py").read_text(encoding="utf-8")
        self.assertIn('--enable-autostart', main_src)
        self.assertIn('--disable-autostart', main_src)


class DefaultsTests(unittest.TestCase):
    def test_default_model_and_recording_mode(self):
        from ruamel.yaml import YAML
        path = ROOT / "src" / "parley" / "config.defaults.yaml"
        with open(path, encoding="utf-8") as f:
            cfg = YAML().load(f)
        self.assertEqual(cfg["whisper"]["model"], "base")
        self.assertEqual(cfg["hotkey"]["recording_mode"], "push_to_talk")

    def test_profiles_no_tiny_downgrade(self):
        from ruamel.yaml import YAML
        path = ROOT / "src" / "parley" / "profiles.defaults.yaml"
        with open(path, encoding="utf-8") as f:
            data = YAML().load(f)
        for name, prof in data["profiles"].items():
            model = (prof.get("overrides", {}).get("whisper") or {}).get("model")
            self.assertNotEqual(model, "tiny", f"profile '{name}' still pins tiny")

    def test_export_covers_all_user_files(self):
        # backup/restore must include every user-editable config file.
        from parley.settings_io import EXPORTABLE_FILES
        for f in ("user_settings.yaml", "commands.yaml", "profiles.yaml",
                  "app_rules.yaml", "transforms.yaml"):
            self.assertIn(f, EXPORTABLE_FILES)


class ReviewFixTests(unittest.TestCase):
    # update_check must not crash on a "-dev" suffixed local version.
    def test_is_newer_handles_dev_suffix(self):
        from parley.update_check import _is_newer
        self.assertTrue(_is_newer("0.12.0", "0.11.0-dev"))
        self.assertFalse(_is_newer("0.11.0", "0.11.0-dev"))   # same core, not newer
        self.assertFalse(_is_newer("1.2", "1.2.0"))           # padded equal
        self.assertTrue(_is_newer("v1.3.0", "1.2.9"))         # leading v tolerated

    # settings_ui._coerce must keep free-text settings as strings even when numeric-looking.
    def test_coerce_is_type_aware(self):
        from parley.settings_ui import _coerce
        sentinel = object()  # not a BooleanVar
        self.assertEqual(_coerce(sentinel, "2024", "whisper.initial_prompt"), "2024")
        self.assertEqual(_coerce(sentinel, "3", "postprocess.ollama.model"), "3")
        self.assertEqual(_coerce(sentinel, "5", "whisper.beam_size"), 5)
        self.assertAlmostEqual(_coerce(sentinel, "0.75", "audio.noise_suppression.strength"), 0.75)

    # autostart.toggle returns the achieved state (macOS path, temp plist).
    def test_autostart_toggle_returns_state(self):
        import sys
        if sys.platform != "darwin":
            self.skipTest("toggle round-trip exercised via macOS plist path only")
        import tempfile, unittest.mock as mock
        from pathlib import Path
        from parley import autostart
        with tempfile.TemporaryDirectory() as d:
            with mock.patch("parley.autostart._mac_plist_path", return_value=Path(d) / "a.plist"):
                self.assertTrue(autostart.toggle())
                self.assertFalse(autostart.toggle())


class StreamingDeliveryDecisionTests(unittest.TestCase):
    def _cfg(self, on=True):
        return {'deliver_to_cursor': on}

    def test_off_by_default(self):
        from parley.streaming_delivery import decide_stream_delivery
        self.assertFalse(decide_stream_delivery({}, True, True, True, None))

    def test_all_conditions_met(self):
        from parley.streaming_delivery import decide_stream_delivery
        self.assertTrue(decide_stream_delivery(self._cfg(), True, True, True, None))

    def test_requires_streaming_available(self):
        from parley.streaming_delivery import decide_stream_delivery
        self.assertFalse(decide_stream_delivery(self._cfg(), False, True, True, None))

    def test_requires_auto_paste(self):
        from parley.streaming_delivery import decide_stream_delivery
        self.assertFalse(decide_stream_delivery(self._cfg(), True, False, True, None))

    def test_requires_textable_foreground(self):
        from parley.streaming_delivery import decide_stream_delivery
        self.assertFalse(decide_stream_delivery(self._cfg(), True, True, False, None))

    def test_respects_app_rule_suppress_and_copyonly(self):
        from parley.streaming_delivery import decide_stream_delivery
        self.assertFalse(decide_stream_delivery(self._cfg(), True, True, True, {'suppress': True}))
        self.assertFalse(decide_stream_delivery(self._cfg(), True, True, True, {'auto_paste': False}))
        # a rule that doesn't touch delivery is fine
        self.assertTrue(decide_stream_delivery(self._cfg(), True, True, True, {'initial_prompt': 'x'}))


class StreamingDeliveryWorkerTests(unittest.TestCase):
    def test_segments_delivered_in_order_and_recorded(self):
        from parley.streaming_delivery import StreamingDelivery
        delivered = []
        sd = StreamingDelivery(deliver_fn=delivered.append)
        sd.start()
        sd.submit_final("hello")
        sd.submit_final("world")
        full = sd.stop()
        self.assertEqual(delivered, ["hello ", "world "])
        self.assertEqual(full, "hello world")
        self.assertTrue(sd.submitted_any)

    def test_blank_segments_ignored(self):
        from parley.streaming_delivery import StreamingDelivery
        delivered = []
        sd = StreamingDelivery(deliver_fn=delivered.append)
        sd.start()
        sd.submit_final("   ")
        sd.submit_final("")
        self.assertEqual(sd.stop(), "")
        self.assertEqual(delivered, [])
        self.assertFalse(sd.submitted_any)

    def test_stop_is_idempotent(self):
        from parley.streaming_delivery import StreamingDelivery
        sd = StreamingDelivery(deliver_fn=lambda s: None)
        sd.start()
        sd.submit_final("x")
        self.assertEqual(sd.stop(), "x")
        self.assertEqual(sd.stop(), "x")  # second stop returns same, no error

    def test_deliver_fn_exception_does_not_crash_and_flags_failure(self):
        from parley.streaming_delivery import StreamingDelivery
        def boom(_):
            raise RuntimeError("boom")
        sd = StreamingDelivery(deliver_fn=boom)
        sd.start()
        sd.submit_final("x")
        # stop() must still return cleanly even though delivery raised
        self.assertEqual(sd.stop(), "")
        self.assertTrue(sd.had_failure)

    def test_no_failure_flag_on_clean_delivery(self):
        from parley.streaming_delivery import StreamingDelivery
        sd = StreamingDelivery(deliver_fn=lambda s: None)
        sd.start()
        sd.submit_final("ok")
        sd.stop()
        self.assertFalse(sd.had_failure)

    def test_submit_after_stop_is_ignored(self):
        # Closes the submit/stop race: a late segment after stop is dropped, not enqueued.
        from parley.streaming_delivery import StreamingDelivery
        delivered = []
        sd = StreamingDelivery(deliver_fn=delivered.append)
        sd.start()
        sd.submit_final("a")
        sd.stop()
        sd.submit_final("late")
        self.assertEqual(delivered, ["a "])


class RouterDefaultsTests(unittest.TestCase):
    # ConfigManager prunes user keys that are missing from the shipped defaults,
    # so the router section must carry every key or user opt-ins get dropped.
    def _load_defaults(self):
        from ruamel.yaml import YAML
        path = ROOT / "src" / "parley" / "config.defaults.yaml"
        with open(path, encoding="utf-8") as f:
            return YAML().load(f)

    def test_defaults_contain_full_router_section(self):
        router = self._load_defaults().get("router")
        self.assertIsNotNone(router, "router section missing from defaults")
        self.assertIs(bool(router["enabled"]), False)  # ships OFF: Parley stays standalone
        self.assertEqual(router["endpoint"], "http://127.0.0.1:7780/ask")
        self.assertEqual(router["hotkey"], "f9")
        self.assertEqual(router["timeout_seconds"], 30)

    def test_assistant_hotkey_does_not_collide(self):
        cfg = self._load_defaults()
        hk = cfg["hotkey"]
        taken = {str(hk.get(k) or "").split(" | ")[0].strip()
                 for k in ("recording_hotkey", "stop_key", "auto_send_key",
                           "cancel_combination", "command_hotkey",
                           "rephrase_hotkey", "pause_hotkey")}
        self.assertNotIn(cfg["router"]["hotkey"], taken)


class RouterClientTests(unittest.TestCase):
    # The globe liveness ping: send_event must POST {kind,state} to /event
    # (derived from the /ask endpoint), fire-and-forget.
    def test_send_event_posts_to_event_endpoint(self):
        import json as jsonlib
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from parley import router_client

        received = {}

        class StubHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                received["path"] = self.path
                received["body"] = jsonlib.loads(self.rfile.read(length))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{\"ok\": true}")

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), StubHandler)
        server_thread = threading.Thread(target=server.handle_request, daemon=True)
        server_thread.start()
        try:
            cfg = {"enabled": True, "endpoint": f"http://127.0.0.1:{server.server_port}/ask"}
            router_client.send_event("listening", "start", cfg)
            server_thread.join(timeout=5)  # completes once the ping lands
        finally:
            server.server_close()

        self.assertEqual(received["path"], "/event")
        self.assertEqual(received["body"], {"kind": "listening", "state": "start"})

    def test_send_event_never_raises_when_switchboard_down(self):
        # A dead endpoint must be swallowed — a missed globe state is fine.
        import socket
        from parley import router_client

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            dead_port = probe.getsockname()[1]
        # Must not raise even though nothing is listening.
        router_client.send_event("listening", "stop",
                                 {"endpoint": f"http://127.0.0.1:{dead_port}/ask"})

    # Exercise the real HTTP path against a stub Switchboard on an ephemeral
    # port — asserts the wire payload shape without needing the real service.
    def test_send_posts_expected_payload(self):
        import json as jsonlib
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from parley import router_client

        received = {}

        class StubHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                received["path"] = self.path
                received["content_type"] = self.headers.get("Content-Type")
                received["body"] = jsonlib.loads(self.rfile.read(length))
                reply = jsonlib.dumps({"reply": "done", "spoken": True}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(reply)

            def log_message(self, *args):
                pass  # keep test output clean

        server = HTTPServer(("127.0.0.1", 0), StubHandler)
        server_thread = threading.Thread(target=server.handle_request, daemon=True)
        server_thread.start()
        try:
            cfg = {"enabled": True,
                   "endpoint": f"http://127.0.0.1:{server.server_port}/ask",
                   "timeout_seconds": 5}
            sender = router_client.send("turn on the lights", cfg)
            self.assertIsNotNone(sender)
            sender.join(timeout=5)
            server_thread.join(timeout=5)
        finally:
            server.server_close()

        self.assertEqual(received["path"], "/ask")
        self.assertEqual(received["content_type"], "application/json")
        body = received["body"]
        self.assertEqual(body["text"], "turn on the lights")
        self.assertEqual(body["source"], "parley")
        self.assertEqual(body["mode"], "assistant")
        # ts must be a parseable ISO-8601 timestamp
        from datetime import datetime
        datetime.fromisoformat(body["ts"])

    def test_send_survives_connection_refused(self):
        # Bind-then-close guarantees the port is unused; the failed POST must be
        # swallowed inside router_client, never raised into the pipeline.
        import socket
        from parley import router_client

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            dead_port = probe.getsockname()[1]

        cfg = {"endpoint": f"http://127.0.0.1:{dead_port}/ask", "timeout_seconds": 2}
        sender = router_client.send("anyone home?", cfg)
        self.assertIsNotNone(sender)
        sender.join(timeout=10)
        self.assertFalse(sender.is_alive())

    def test_send_skips_empty_text(self):
        from parley import router_client
        self.assertIsNone(router_client.send("", {}))
        self.assertIsNone(router_client.send("   ", {}))

    def test_send_tolerates_missing_config(self):
        # A None config must fall back to the default endpoint, not crash send().
        # Patch the default to a guaranteed-dead port: once Switchboard runs
        # persistently on :7780, this test must NOT trigger a real assistant
        # request (Ollama + Kokoro speaking aloud on every pytest run).
        import socket
        import unittest.mock
        from parley import router_client

        self.assertEqual(router_client.DEFAULT_ENDPOINT, "http://127.0.0.1:7780/ask")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            dead_port = probe.getsockname()[1]
        with unittest.mock.patch.object(
            router_client, "DEFAULT_ENDPOINT", f"http://127.0.0.1:{dead_port}/ask"
        ):
            sender = router_client.send("hello", None)
            self.assertIsNotNone(sender)
            sender.join(timeout=15)
        self.assertFalse(sender.is_alive())

    def test_send_fires_on_failure_only_when_dropped(self):
        # Connection refused -> on_failure fires exactly once (the request
        # was definitely dropped and the user needs an audible signal).
        import socket
        import threading
        from parley import router_client

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            dead_port = probe.getsockname()[1]

        fired = threading.Event()
        cfg = {"endpoint": f"http://127.0.0.1:{dead_port}/ask", "timeout_seconds": 2}
        sender = router_client.send("anyone home?", cfg, on_failure=fired.set)
        sender.join(timeout=10)
        self.assertTrue(fired.is_set())

    def test_assistant_hotkey_conflict_is_disabled(self):
        # router.hotkey colliding with the recording hotkey must be disabled
        # loudly, never allowed to hijack dictation to Switchboard.
        import logging
        from parley.config_manager import validate_config

        config = {
            "audio": {"max_duration": 60},
            "vad": {
                "vad_onset_threshold": 0.5,
                "vad_offset_threshold": 0.35,
                "vad_min_speech_duration": 0.1,
                "vad_silence_timeout_seconds": 30.0,
            },
            "hotkey": {
                "recording_mode": "push_to_talk",
                "recording_hotkey": "f10",
                "stop_key": "ctrl",
                "auto_send_key": "alt",
                "command_hotkey": "alt+win",
            },
            "router": {"enabled": True, "hotkey": "f10"},
        }
        validate_config(config, config, logging.getLogger("test"))
        self.assertEqual(config["router"]["hotkey"], "")


if __name__ == "__main__":
    unittest.main()
