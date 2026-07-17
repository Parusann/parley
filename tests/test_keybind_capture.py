# Combo normalization, equality, conflict detection, and the chord collector
# for the hub's press-to-capture keybind editor. No real hooks, no tk — the
# collector is driven by a fake keyboard module.

from parley.keybind_capture import (_read_chord, combos_equal, find_conflict,
                                    normalize_combo)


class _Ev:
    def __init__(self, event_type, name):
        self.event_type = event_type
        self.name = name


class _FakeKeyboard:
    """Delivers a scripted event sequence the moment the hook installs."""

    def __init__(self, events):
        self._events = [_Ev(t, n) for t, n in events]

    def hook(self, callback):
        for ev in self._events:
            callback(ev)
        return callback

    def unhook(self, hook):
        pass


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


def test_read_chord_modifier_only_bind():
    # Parley's stock stop/auto-send binds ARE bare modifiers — the collector
    # must finish on all-released, not wait for a non-modifier key.
    fake = _FakeKeyboard([('down', 'alt'), ('up', 'alt')])
    assert _read_chord(fake, timeout_s=0.1) == 'alt'


def test_read_chord_multi_key_chord_any_release_order():
    fake = _FakeKeyboard([('down', 'left ctrl'), ('down', 'f7'),
                          ('up', 'left ctrl'), ('up', 'f7')])
    assert normalize_combo(_read_chord(fake, timeout_s=0.1)) == 'ctrl+f7'


def test_read_chord_esc_alone_cancels():
    fake = _FakeKeyboard([('down', 'esc'), ('up', 'esc')])
    assert _read_chord(fake, timeout_s=0.1) == ''


def test_read_chord_esc_inside_chord_is_a_key():
    # ctrl+esc held together is a combo, not a cancel (esc only cancels alone).
    fake = _FakeKeyboard([('down', 'ctrl'), ('down', 'esc'),
                          ('up', 'esc'), ('up', 'ctrl')])
    assert normalize_combo(_read_chord(fake, timeout_s=0.1)) == 'ctrl+esc'


def test_find_conflict_names_the_other_bind():
    binds = {'recording_hotkey': 'f8', 'assistant_hotkey': 'f9', 'stop_key': ''}
    assert find_conflict('command_hotkey', 'F9', binds) == 'assistant_hotkey'
    assert find_conflict('recording_hotkey', 'f8', binds) is None  # itself
    assert find_conflict('command_hotkey', 'ctrl+q', binds) is None
    assert find_conflict('command_hotkey', '', binds) is None  # clearing never conflicts
