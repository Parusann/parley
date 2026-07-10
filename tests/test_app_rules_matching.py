# Tests for app_rules.py (the per-app injection-rules engine).
# No real config dir is touched: get_user_app_data_path is monkeypatched to a
# tmp dir holding a synthetic app_rules.yaml, and the foreground-app probe is
# monkeypatched so no real window inspection happens.

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from parley import app_rules as app_rules_module
from parley.app_rules import AppRules, formatting_overrides

# The platform foreground module needs pywin32/pyobjc; skip cleanly where absent.
# (It is re-exported through the platform package, so importorskip's dotted-path
# form can't reach it directly.)
try:
    from parley.platform import foreground
except Exception:
    pytest.skip("platform foreground module unavailable", allow_module_level=True)


RULES_YAML = """\
rules:
  # Malformed rule (no match key) — must never fire.
  - auto_send: true

  - match: "slack.exe"
    auto_send: true
    capitalize_first: true

  - match: "TELEGRAM.EXE"
    auto_send: true

  # Listed BEFORE the broader "code" rule below: first match must win.
  - match: ["code.exe", "cursor.exe"]
    suppress: true
    capitalize_first: false
    strip_trailing_period: true

  - match: "code"
    auto_send: false

  # Title-based match (no exe pattern would hit winword.exe).
  - match: "my novel"
    language: "fr"
"""


@pytest.fixture
def rules(tmp_path, monkeypatch):
    (tmp_path / "app_rules.yaml").write_text(RULES_YAML, encoding="utf-8")
    monkeypatch.setattr(app_rules_module, "get_user_app_data_path", lambda: str(tmp_path))
    return AppRules()


def _set_foreground(monkeypatch, exe="", title=""):
    monkeypatch.setattr(foreground, "get_foreground_app", lambda: {"exe": exe, "title": title})


def test_exact_exe_name_match(rules, monkeypatch):
    _set_foreground(monkeypatch, exe="slack.exe", title="general - Slack")
    rule = rules.match_for_foreground()
    assert rule is not None
    assert rule.get("auto_send") is True
    assert rule.get("capitalize_first") is True


def test_match_is_case_insensitive_both_ways(rules, monkeypatch):
    # Uppercase foreground exe vs lowercase pattern...
    _set_foreground(monkeypatch, exe="SLACK.EXE")
    assert rules.match_for_foreground() is not None
    # ...and uppercase pattern vs lowercase foreground exe.
    _set_foreground(monkeypatch, exe="telegram.exe")
    rule = rules.match_for_foreground()
    assert rule is not None
    assert rule.get("auto_send") is True


def test_no_match_returns_none(rules, monkeypatch):
    _set_foreground(monkeypatch, exe="unknownapp.exe", title="Nothing Relevant")
    assert rules.match_for_foreground() is None


def test_rule_precedence_first_match_wins(rules, monkeypatch):
    # "code.exe" matches BOTH the code-editors rule and the broader "code"
    # substring rule; rules are matched top-down, so the earlier one wins.
    _set_foreground(monkeypatch, exe="code.exe", title="main.py - Visual Studio Code")
    rule = rules.match_for_foreground()
    assert rule is not None
    assert rule.get("suppress") is True          # from the first (specific) rule
    assert "auto_send" not in rule               # NOT the later broad rule


def test_title_substring_match(rules, monkeypatch):
    _set_foreground(monkeypatch, exe="winword.exe", title="My Novel - Word")
    rule = rules.match_for_foreground()
    assert rule is not None
    assert rule.get("language") == "fr"


def test_rule_without_match_key_never_fires(rules, monkeypatch):
    # The malformed first rule would shadow everything if it matched.
    _set_foreground(monkeypatch, exe="slack.exe")
    rule = rules.match_for_foreground()
    assert rule.get("capitalize_first") is True  # got the slack rule, not rule #0


def test_empty_foreground_info_returns_none(rules, monkeypatch):
    _set_foreground(monkeypatch, exe="", title="")
    assert rules.match_for_foreground() is None
    monkeypatch.setattr(foreground, "get_foreground_app", lambda: None)
    assert rules.match_for_foreground() is None


def test_empty_rules_list_returns_none(tmp_path, monkeypatch):
    (tmp_path / "app_rules.yaml").write_text("rules: []\n", encoding="utf-8")
    monkeypatch.setattr(app_rules_module, "get_user_app_data_path", lambda: str(tmp_path))
    # Returns before the foreground probe, so no foreground patch is needed.
    assert AppRules().match_for_foreground() is None


def test_formatting_overrides_surface_through_public_helper(rules, monkeypatch):
    # This is exactly how state_manager consumes a matched rule.
    _set_foreground(monkeypatch, exe="cursor.exe")
    rule = rules.match_for_foreground()
    assert formatting_overrides(rule) == {
        "capitalize_first": False,
        "strip_trailing_period": True,
    }
    # Only the formatting keys present in the rule are surfaced.
    _set_foreground(monkeypatch, exe="slack.exe")
    assert formatting_overrides(rules.match_for_foreground()) == {"capitalize_first": True}
    # No rule -> no overrides.
    assert formatting_overrides(None) == {}


def test_rules_hot_reload_when_file_changes(rules, tmp_path, monkeypatch):
    _set_foreground(monkeypatch, exe="slack.exe")
    assert rules.match_for_foreground() is not None

    path = tmp_path / "app_rules.yaml"
    path.write_text("rules:\n  - match: \"otherapp.exe\"\n    auto_send: true\n", encoding="utf-8")
    # Force the mtime forward so the reload check trips regardless of fs resolution.
    bumped = rules._mtime + 10
    os.utime(path, (bumped, bumped))

    assert rules.match_for_foreground() is None  # slack rule is gone
    _set_foreground(monkeypatch, exe="otherapp.exe")
    assert rules.match_for_foreground().get("auto_send") is True
