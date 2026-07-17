# Combo normalization, equality, and conflict detection for the hub's
# press-to-capture keybind editor. Pure logic — no hooks, no tk.

from parley.keybind_capture import combos_equal, find_conflict, normalize_combo


def test_normalize_strips_sides_and_maps_names():
    assert normalize_combo('left ctrl+windows+space') == 'ctrl+win+space'
    assert normalize_combo('right shift+F9') == 'shift+f9'
    assert normalize_combo('ctrl + alt + t') == 'ctrl+alt+t'


def test_normalize_orders_modifiers_canonically():
    assert normalize_combo('shift+ctrl+k') == 'ctrl+shift+k'
    assert normalize_combo('win+alt+ctrl+m') == 'ctrl+alt+win+m'


def test_normalize_single_key_and_esc():
    assert normalize_combo('f9') == 'f9'
    assert normalize_combo('escape') == 'esc'


def test_combos_equal_across_vocabularies():
    # config vocab vs global_hotkeys vocab vs keyboard-lib output
    assert combos_equal('ctrl+win+space', 'control + window + space')
    assert combos_equal('esc', 'escape')
    assert not combos_equal('ctrl+space', 'alt+space')
    assert not combos_equal('', 'f9')


def test_find_conflict_names_the_other_bind():
    binds = {'recording_hotkey': 'f8', 'assistant_hotkey': 'f9', 'stop_key': ''}
    assert find_conflict('command_hotkey', 'F9', binds) == 'assistant_hotkey'
    assert find_conflict('recording_hotkey', 'f8', binds) is None  # itself
    assert find_conflict('command_hotkey', 'ctrl+q', binds) is None
    assert find_conflict('command_hotkey', '', binds) is None  # clearing never conflicts
