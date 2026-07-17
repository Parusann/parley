# keybind_capture.py
# Combo vocabulary + capture primitive for the hub's keybind editor.
# Config vocab is what user_settings.yaml stores ("ctrl+win+space"); the
# keyboard lib emits "left ctrl+windows+space"; global_hotkeys wants
# "control + window + space" (see platform/windows/hotkeys.py KEY_MAP).
# Everything here canonicalizes INTO config vocab.

import threading

# any-vocab name -> config vocab
_NAME_MAP = {
    'control': 'ctrl', 'windows': 'win', 'window': 'win', 'cmd': 'win',
    'super': 'win', 'escape': 'esc', 'return': 'enter',
}
_MODIFIER_ORDER = {'ctrl': 0, 'alt': 1, 'shift': 2, 'win': 3}


def _canon_key(name: str) -> str:
    name = name.strip().lower()
    for side in ('left ', 'right '):
        if name.startswith(side):
            name = name[len(side):]
    return _NAME_MAP.get(name, name)


def normalize_combo(raw: str) -> str:
    """Any-vocabulary combo string -> canonical config form ('ctrl+win+space')."""
    parts = [_canon_key(p) for p in str(raw or '').split('+') if p.strip()]
    mods = sorted((p for p in parts if p in _MODIFIER_ORDER), key=_MODIFIER_ORDER.get)
    keys = [p for p in parts if p not in _MODIFIER_ORDER]
    return '+'.join(mods + keys)


def combos_equal(a: str, b: str) -> bool:
    na, nb = normalize_combo(a), normalize_combo(b)
    return bool(na) and na == nb


def find_conflict(name: str, candidate: str, binds: dict) -> str | None:
    """Name of the other bind already using `candidate`, or None."""
    for other, combo in binds.items():
        if other != name and combo and combos_equal(candidate, combo):
            return other
    return None


def capture_combo(on_done, cancel_event: threading.Event) -> None:
    """Block in a worker thread until one hotkey is pressed; deliver the
    canonical combo ('' = cancelled via Esc). A set cancel_event discards a
    late result so an abandoned capture can't fire into a closed dialog."""

    def _worker():
        try:
            import keyboard  # LL hook — sees the win key, unlike tk events
            raw = keyboard.read_hotkey(suppress=False)
        except Exception:
            raw = ''
        if cancel_event.is_set():
            return
        combo = normalize_combo(raw)
        on_done('' if combo == 'esc' else combo)

    threading.Thread(target=_worker, name='hub-keycapture', daemon=True).start()
