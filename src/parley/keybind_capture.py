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


def _read_chord(events, timeout_s: float = 30.0) -> str:
    """One chord from an event source exposing hook(cb)/unhook(h) with
    {event_type, name} events: the union of keys held together, delivered when
    the last one is released — so modifier-only binds work ('ctrl', 'alt' are
    Parley's stock stop/auto-send). Esc pressed on its own cancels ('')."""
    done = threading.Event()
    state = {'down': set(), 'chord': set(), 'result': ''}

    def on_event(ev):
        name = _canon_key(str(ev.name or ''))
        if not name:
            return
        if ev.event_type == 'down':
            if name == 'esc' and not state['down']:
                state['result'] = ''
                done.set()
                return
            state['down'].add(name)
            state['chord'] |= state['down']
        elif ev.event_type == 'up':
            state['down'].discard(name)
            if state['chord'] and not state['down']:
                state['result'] = '+'.join(state['chord'])
                done.set()

    hook = events.hook(on_event)
    try:
        done.wait(timeout=timeout_s)
    finally:
        events.unhook(hook)
    return state['result']


def capture_combo(on_done, cancel_event: threading.Event) -> None:
    """Capture one chord on a worker thread and deliver the canonical combo
    ('' = cancelled/timeout). A set cancel_event discards a late result so an
    abandoned capture can't fire into a closed dialog."""

    def _worker():
        try:
            # Parley's own raw WH_KEYBOARD_LL tap. NOT the `keyboard` package:
            # its hook receives nothing in a process running global_hotkeys.
            from .platform.windows import hook_events
            raw = _read_chord(hook_events)
        except Exception:
            raw = ''
        if cancel_event.is_set():
            return
        on_done(normalize_combo(raw))

    threading.Thread(target=_worker, name='hub-keycapture', daemon=True).start()
