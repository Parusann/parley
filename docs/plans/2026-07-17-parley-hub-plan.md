# Parley Hub Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A hub window that opens when Parley launches: live status lights, real dictation latency + recent transcripts, quick actions, and a press-to-capture keybind editor that applies without restart.

**Architecture:** In-process daemon-thread tk window (cheat_sheet pattern) holding live refs to ConfigManager/StateManager/HotkeyListener via a `hub_deps` dict built in main.py. Pure logic (combo normalization, probes, latency field) lives in small headless modules with tests; the window consumes them. Spec: `docs/plans/2026-07-17-parley-hub-design.md`.

**Tech Stack:** tkinter + `theme.py` primitives, `keyboard` lib for capture, `global_hotkeys` via the existing listener for live rebind, urllib for probes, pytest.

---

### Task 1: Combo logic (`keybind_capture.py`)

**Files:**
- Create: `src/parley/keybind_capture.py`
- Test: `tests/test_keybind_capture.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_keybind_capture.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_keybind_capture.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'parley.keybind_capture'`

- [ ] **Step 3: Implement**

```python
# src/parley/keybind_capture.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_keybind_capture.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/parley/keybind_capture.py tests/test_keybind_capture.py
git commit -m "feat: combo normalization + capture primitive for the hub keybind editor"
```

### Task 2: Real dictation latency in history

**Files:**
- Modify: `src/parley/transcript_log.py` (record_transcript signature + entry)
- Modify: `src/parley/state_manager.py` (measure around transcription; the function containing the `record_transcript(` call for the standard dictation path — locate all 3 call sites first with `rg -n "record_transcript\(" src/parley/state_manager.py`)
- Test: `tests/test_transcript_latency.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_transcript_latency.py
# The dictation history gains an optional latency_ms field. Old entries
# without it must keep loading fine (the file is append-only across versions).

import json

from parley import transcript_log


def test_latency_recorded_when_given(tmp_path, monkeypatch):
    monkeypatch.setattr(transcript_log, '_LOG_FILE', tmp_path / 't.jsonl')
    transcript_log.record_transcript('hello world', app='code', duration_s=1.2,
                                     latency_ms=142)
    entry = transcript_log.load_transcripts()[0]
    assert entry['latency_ms'] == 142


def test_latency_absent_when_not_given(tmp_path, monkeypatch):
    monkeypatch.setattr(transcript_log, '_LOG_FILE', tmp_path / 't.jsonl')
    transcript_log.record_transcript('hello', app='', duration_s=0.5)
    entry = transcript_log.load_transcripts()[0]
    assert 'latency_ms' not in entry


def test_old_entries_without_latency_still_load(tmp_path, monkeypatch):
    monkeypatch.setattr(transcript_log, '_LOG_FILE', tmp_path / 't.jsonl')
    (tmp_path / 't.jsonl').write_text(
        json.dumps({'timestamp': '2026-01-01T00:00:00', 'text': 'old', 'app': '',
                    'duration_s': 1.0, 'chars': 3}) + '\n', encoding='utf-8')
    transcript_log.record_transcript('new', latency_ms=99)
    texts = [e['text'] for e in transcript_log.load_transcripts()]
    assert texts == ['new', 'old']
```

Note: confirm `_LOG_FILE` is the module-level path constant (transcript_log.py:19) and is read at call time; if it is resolved through a function, monkeypatch that instead.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_transcript_latency.py -q`
Expected: FAIL — `TypeError: record_transcript() got an unexpected keyword argument 'latency_ms'`

- [ ] **Step 3: Implement transcript_log side**

In `record_transcript(text, app='', duration_s=0.0)` → add `latency_ms=None` parameter; where the entry dict is built (lines ~33-39), add after `chars`:

```python
    if latency_ms is not None:
        entry['latency_ms'] = int(latency_ms)
```

(Adapt to the actual dict-literal style in the file — if the entry is a single literal, build it then conditionally assign.)

- [ ] **Step 4: Implement state_manager side**

Read the function around each `record_transcript(` call site (dictation ~line 397, assistant ~707). In the standard dictation path, find where transcription runs (the call whose result becomes the transcript text — expect `transcribe_audio(...)` or similar). Wrap:

```python
        _t0 = time.time()
        # ... existing transcription call, unchanged ...
        _latency_ms = int((time.time() - _t0) * 1000)
```

and pass `latency_ms=_latency_ms` into that path's `record_transcript(...)` call. `time` is already imported in state_manager (verify; add import if not). Only the standard dictation path needs it (assistant/command paths measure a round-trip to another system — leave them without latency rather than record a misleading number).

- [ ] **Step 5: Run tests + full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_transcript_latency.py -q` → 3 passed
Run: `.venv/Scripts/python.exe -m pytest tests/ -q` → everything green (171+ passed)

- [ ] **Step 6: Commit**

```bash
git add src/parley/transcript_log.py src/parley/state_manager.py tests/test_transcript_latency.py
git commit -m "feat: record real dictation latency (latency_ms) in transcript history"
```

### Task 3: Status probes (`hub_probes.py` + `router_client.health`)

**Files:**
- Create: `src/parley/hub_probes.py`
- Modify: `src/parley/router_client.py` (add `health(cfg)`)
- Test: `tests/test_hub_probes.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_hub_probes.py
# Probe classification with injected primitives — no sockets, no network.

from parley import hub_probes


def test_vulkan_up_and_down():
    assert hub_probes.vulkan_server_up('127.0.0.1', 8181, connect=lambda h, p: 0)
    assert not hub_probes.vulkan_server_up('127.0.0.1', 8181, connect=lambda h, p: 10061)


def test_ollama_up_uses_tags_endpoint():
    seen = {}

    def fake_get(url, timeout):
        seen['url'] = url
        return 200

    assert hub_probes.ollama_up('http://127.0.0.1:11434', get_status=fake_get)
    assert seen['url'] == 'http://127.0.0.1:11434/api/tags'
    assert not hub_probes.ollama_up('http://127.0.0.1:11434',
                                    get_status=lambda u, timeout: (_ for _ in ()).throw(OSError()))


def test_amadeus_health_derived_from_ask_endpoint():
    seen = {}

    def fake_get(url, timeout):
        seen['url'] = url
        return 200

    cfg = {'endpoint': 'http://127.0.0.1:7780/ask'}
    assert hub_probes.amadeus_up(cfg, get_status=fake_get)
    assert seen['url'] == 'http://127.0.0.1:7780/health'


def test_prober_snapshot_single_cycle():
    prober = hub_probes.Prober(probes={'a': lambda: (True, 'fine'),
                                       'b': lambda: (False, 'down')})
    prober.refresh_once()
    snap = prober.snapshot()
    assert snap['a'] == (True, 'fine')
    assert snap['b'] == (False, 'down')


def test_prober_survives_a_raising_probe():
    prober = hub_probes.Prober(probes={'bad': lambda: (_ for _ in ()).throw(RuntimeError('x'))})
    prober.refresh_once()
    ok, detail = prober.snapshot()['bad']
    assert ok is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_hub_probes.py -q`
Expected: FAIL — no module `parley.hub_probes`

- [ ] **Step 3: Implement hub_probes**

```python
# src/parley/hub_probes.py
# Headless status checks behind the hub's lights. Every probe returns
# (ok: bool, detail: str) and never raises; network primitives are
# injectable so tests stay offline. Mirrors doctor.py's probe styles.

import socket
import threading
import urllib.request


def _tcp_connect(host: str, port: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port))


def _http_status(url: str, timeout: float) -> int:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status


def vulkan_server_up(host: str, port: int, connect=_tcp_connect) -> bool:
    try:
        return connect(host, port) == 0
    except OSError:
        return False


def ollama_up(endpoint: str, get_status=_http_status) -> bool:
    try:
        return get_status(endpoint.rstrip('/') + '/api/tags', timeout=1.5) == 200
    except Exception:
        return False


def amadeus_up(router_cfg: dict, get_status=_http_status) -> bool:
    """Reachability of the Switchboard, derived from the configured /ask endpoint."""
    endpoint = str(router_cfg.get('endpoint', '') or '')
    if not endpoint:
        return False
    base = endpoint.rsplit('/', 1)[0]
    try:
        return get_status(base + '/health', timeout=2.0) == 200
    except Exception:
        return False


def mic_present(configured_name: str = '') -> tuple:
    """(ok, device label). Import inside so a broken audio stack can't kill the hub."""
    try:
        import sounddevice
        devices = [d for d in sounddevice.query_devices() if d.get('max_input_channels', 0) > 0]
        if not devices:
            return False, 'no input devices'
        if configured_name:
            for d in devices:
                if configured_name.lower() in str(d.get('name', '')).lower():
                    return True, d['name']
            return False, f'"{configured_name}" not found'
        default = sounddevice.query_devices(kind='input')
        return True, default.get('name', 'default input')
    except Exception as exc:
        return False, f'audio stack error: {exc}'


class Prober:
    """Refreshes {name: (ok, detail)} on a daemon thread while the hub is open."""

    def __init__(self, probes: dict, interval_s: float = 2.0):
        self._probes = probes
        self._interval = interval_s
        self._lock = threading.Lock()
        self._snapshot = {}
        self._stop = threading.Event()

    def refresh_once(self) -> None:
        results = {}
        for name, fn in self._probes.items():
            try:
                results[name] = fn()
            except Exception as exc:
                results[name] = (False, f'probe error: {exc}')
        with self._lock:
            self._snapshot = results

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._snapshot)

    def start(self) -> None:
        def _loop():
            while not self._stop.is_set():
                self.refresh_once()
                self._stop.wait(self._interval)

        threading.Thread(target=_loop, name='hub-prober', daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
```

Note: probes used with `Prober` must return `(ok, detail)` tuples — wrap the plain-bool probes at the call site in hub.py (Task 5) e.g. `lambda: (vulkan_server_up(h, p), 'port %s' % p)`.

- [ ] **Step 4: Add router_client.health**

In `src/parley/router_client.py`, after `send_event` (same base-derivation idiom):

```python
def health(cfg: dict, timeout: float = 2.0) -> bool:
    """True when the Switchboard answers /health. Never raises — the hub
    shows a light, not an error."""
    endpoint = str((cfg or {}).get('endpoint', DEFAULT_ENDPOINT) or DEFAULT_ENDPOINT)
    url = endpoint.rsplit('/', 1)[0] + '/health'
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False
```

(hub_probes.amadeus_up stays as the injectable/testable variant; hub.py may use either — prefer hub_probes for uniformity.)

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_hub_probes.py -q` → 5 passed

- [ ] **Step 6: Commit**

```bash
git add src/parley/hub_probes.py src/parley/router_client.py tests/test_hub_probes.py
git commit -m "feat: headless status probes for the hub (vulkan/ollama/amadeus/mic)"
```

### Task 4: Keybinds leave the settings window

**Files:**
- Modify: `src/parley/settings_ui.py` (remove `_build_hotkeys_tab` and its registration; header comment "4 tabs" → 3)

- [ ] **Step 1: Remove the tab**

Delete `_build_hotkeys_tab` (lines ~505-527) and the line that registers it (search `_build_hotkeys_tab(` — one call near the notebook construction, plus any search-index/tab list references from `on_search`). Update the module header comment ("Notebook with 4 tabs — General, Audio, Hotkeys, Post-process" → "3 tabs — General, Audio, Post-process; keybinds live in the Hub").

- [ ] **Step 2: Verify settings window still builds headlessly**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: green (no settings test may exist; the suite guards imports). Also: `.venv/Scripts/python.exe -c "import parley.settings_ui"` → no error.

- [ ] **Step 3: Commit**

```bash
git add src/parley/settings_ui.py
git commit -m "refactor: hotkeys tab moves out of settings (hub owns keybinds)"
```

### Task 5: The hub window (`hub.py`)

**Files:**
- Create: `src/parley/hub.py`
- Modify: `src/parley/hotkey_listener.py` (public `toggle_pause()` delegating to the existing `_pause_hotkey_pressed` behavior)

- [ ] **Step 0: Confirm theme helper signatures before writing UI code**

Read the definitions of `heading`, `caption`, `entry`, `key_chip`, `card`, `button`, `checkbox`, `scrollbar` in `theme.py` (the scout mapped names, not signatures) and the first ~40 lines of `cheat_sheet.py` (singleton + thread launch pattern to mirror). Adjust the code below to the real signatures.

- [ ] **Step 1: Add `toggle_pause` to HotkeyListener**

```python
    def toggle_pause(self):
        """Public pause/resume for the hub's quick action — same behavior as
        pressing the pause hotkey."""
        self._pause_hotkey_pressed()
```

- [ ] **Step 2: Write hub.py**

Full structure (adapt widget-helper calls to verified signatures):

```python
# hub.py
# The Parley Hub — the window that opens with the app: status lights, real
# dictation latency + recent transcripts, quick actions, and the keybind
# editor (press-to-capture, applied live through HotkeyListener). Runs like
# cheat_sheet.py: in-process daemon thread, own tk root, singleton.

import subprocess
import sys
import threading
import tkinter as tk

from . import hub_probes, theme, transcript_log
from .keybind_capture import capture_combo, find_conflict

_lock = threading.Lock()
_current_root = None

# (config key, human label, clearable)
BIND_ROWS = [
    ('recording_hotkey', 'Record (push-to-talk)', False),
    ('stop_key', 'Stop', False),
    ('auto_send_key', 'Auto-send', True),
    ('cancel_combination', 'Cancel recording', True),
    ('command_hotkey', 'Command mode', True),
    ('rephrase_hotkey', 'Rephrase', True),
    ('assistant_hotkey', 'Ask Amadeus', True),
    ('pause_hotkey', 'Pause all hotkeys', True),
]

STATUS_ROWS = [
    ('model', 'Whisper model'),
    ('vulkan', 'Vulkan server'),
    ('mic', 'Microphone'),
    ('ollama', 'Ollama cleanup'),
    ('amadeus', 'Amadeus link'),
]


def show_hub(deps: dict) -> None:
    """Open (or lift) the hub. deps: config_manager, state_manager,
    hotkey_listener, transforms_manager, restart_app (callable)."""
    global _current_root
    with _lock:
        root = _current_root
    if root is not None:
        try:
            root.after(0, root.deiconify)
            root.after(0, root.lift)
            return
        except Exception:
            pass  # stale ref — fall through and build fresh

    threading.Thread(target=_run, args=(deps,), name='parley-hub', daemon=True).start()


def _run(deps):
    global _current_root
    root = tk.Tk()
    with _lock:
        _current_root = root
    try:
        _build(root, deps)
        root.mainloop()
    finally:
        with _lock:
            _current_root = None
```

`_build(root, deps)` lays out (all via theme prims, GAP/RADIUS constants):

- **Status card**: one row per STATUS_ROWS — a 10px colored dot canvas + label + detail caption. Data from a `hub_probes.Prober` started here with probes: vulkan (host/port from `config_manager.get_setting('whisper','server')` dict or defaults 127.0.0.1:8181), ollama (endpoint from postprocess config), amadeus (`router` config via `get_router_config()`), mic (`audio` device name), model (always-True light showing `whisper.model` + `state_manager.get_current_state()` — red while `model_loading`). A `root.after(2000, tick)` loop re-renders from `prober.snapshot()`; `prober.stop()` on window destroy.
- **Dictations card**: headline = median of `latency_ms` over the last 20 entries of `transcript_log.load_transcripts()` that have it ("— ms · warm median" when none) + up to 5 recent rows (time `ts[11:16]`, 46-char snippet, per-row Copy button → `root.clipboard_clear(); root.clipboard_append(text)`).
- **Actions card**: buttons — "All settings…" (`_spawn('--settings')`), "History…" (`_spawn('--history')`), "Cheat sheet" (in-process `show_cheat_sheet(cm, tm)`), "Add word…" (mirror tray's add-word dialog call), "Pause hotkeys" (deps['hotkey_listener'].toggle_pause(), label flips with `state_manager.is_paused`), "Restart Parley" (deps['restart_app']()).

```python
def _spawn(flag: str) -> None:
    subprocess.Popen([sys.executable, '-m', 'parley.main', flag],
                     creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0))
```

- **Keybinds card** (right column): a row per BIND_ROWS — label, current combo as `key_chip` (or "unset"), Change + (if clearable) Clear. Plus a recording-mode chip row toggling `push_to_talk`/`toggle`. Change → `_begin_capture(name)`:

```python
def _begin_capture(root, deps, name, row_state):
    # Modal strip inside the card: "Press the new keys… (Esc cancels)".
    listener = deps['hotkey_listener']
    cancel = threading.Event()
    listener.stop_listening()  # so the pressed combo can't trigger an action

    def done(combo):
        root.after(0, lambda: _finish_capture(root, deps, name, combo, row_state))

    capture_combo(done, cancel)
    row_state['cancel'] = cancel


def _finish_capture(root, deps, name, combo, row_state):
    listener = deps['hotkey_listener']
    cm = deps['config_manager']
    try:
        if not combo:  # Esc — keep the old bind
            return _set_row_error(row_state, None)
        binds = {n: getattr(listener, n, '') or '' for n, _, _ in BIND_ROWS}
        clash = find_conflict(name, combo, binds)
        if clash:
            return _set_row_error(row_state, f'already used by {clash}')
        old = binds.get(name, '')
        cm.update_user_setting('hotkey', name, combo)
        try:
            listener.change_hotkey_config(name, combo)
        except Exception as exc:
            cm.update_user_setting('hotkey', name, old)  # yaml/live never disagree
            return _set_row_error(row_state, f'could not apply: {exc}')
        _set_row_chip(row_state, combo)
    finally:
        listener.start_listening()
```

Clear = `_finish_capture` with `combo=''` but bypassing the Esc guard (direct call: persist '' + `change_hotkey_config(name, '')` — verify the listener accepts empty/None for optional binds by reading `_setup_hotkeys`'s conditional registration first; if it requires None, store None into the listener call while yaml stores '').

- **Footer**: `theme.checkbox` "Show hub when Parley starts" bound to `cm.get_setting('hub', 'show_on_startup')`, writing through `cm.update_user_setting('hub', 'show_on_startup', bool)`.
- Esc/Ctrl+W close bindings; `theme.init_window(root, 'Parley Hub')`.

- [ ] **Step 3: Import smoke test**

Run: `.venv/Scripts/python.exe -c "import parley.hub"` → no error.
Run: `.venv/Scripts/python.exe -m pytest tests/ -q` → green.

- [ ] **Step 4: Commit**

```bash
git add src/parley/hub.py src/parley/hotkey_listener.py
git commit -m "feat: the Parley Hub window — status, latency, recents, live keybind editor"
```

### Task 6: Wiring — config default, main.py, tray

**Files:**
- Modify: `config.defaults.yaml` (new `hub:` section)
- Modify: `src/parley/main.py` (hub_deps + startup open)
- Modify: `src/parley/system_tray.py` ("Hub..." menu item)

- [ ] **Step 1: Config default**

In `config.defaults.yaml`, after the `system_tray:` section (keep neighborhood alphabetical-ish/topical):

```yaml
# ── Hub ──────────────────────────────────────────────────────────
# The window that opens with Parley: status, latency, recent
# dictations, quick actions, and the keybind editor.
hub:
  show_on_startup: true
```

- [ ] **Step 2: main.py wiring**

Read main.py's run sequence (tray built ~line 568; find where the HotkeyListener is constructed — search `HotkeyListener(`). After BOTH exist:

```python
    from . import hub as hub_module
    hub_deps = {
        'config_manager': config_manager,
        'state_manager': state_manager,
        'hotkey_listener': hotkey_listener,
        'transforms_manager': transforms_manager,
        'restart_app': <the same callable the tray's Restart Parley item uses —
                        locate it (search 'estart' in system_tray.py) and pass
                        the underlying function, factoring it to a module-level
                        helper if it is currently a tray method>,
    }
    if system_tray:
        system_tray.hub_deps = hub_deps
    if config_manager.get_setting('hub', 'show_on_startup'):
        hub_module.show_hub(hub_deps)
```

(`get_setting` signature: verify — tray uses `config_manager.get_setting('whisper','model')`.)

- [ ] **Step 3: Tray item**

In `system_tray.py` menu construction (line ~303 block), FIRST item:

```python
                pystray.MenuItem("Hub...", self._open_hub, default=True),
```

(`default=True` also makes left-clicking the tray icon open the hub.) Handler beside the other `_open_*`:

```python
    def _open_hub(self, icon=None, item=None):
        deps = getattr(self, 'hub_deps', None)
        if deps:
            from . import hub
            hub.show_hub(deps)
```

- [ ] **Step 4: Suite + import smoke**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q` → green.

- [ ] **Step 5: Commit**

```bash
git add config.defaults.yaml src/parley/main.py src/parley/system_tray.py
git commit -m "feat: hub opens with Parley (show_on_startup) and from the tray"
```

### Task 7: Verification + changelog

- [ ] **Step 1: Full suite** — `.venv/Scripts/python.exe -m pytest tests/ -q` → report exact count.
- [ ] **Step 2: Doctor** — `.venv/Scripts/python.exe -m parley.main --doctor` → exit 0.
- [ ] **Step 3: Live smoke** — kill any running dev Parley; launch `parley.cmd`; hub window appears; screenshot it (Windows-MCP). Exercise: change one clearable bind via capture (synthetic keys through Windows-MCP if feasible — SendInput reaches LL hooks), confirm `user_settings.yaml` updated + revert it; status lights show real state (Vulkan/Amadeus green with servers up).
- [ ] **Step 4: Benchmark guard** — `.venv/Scripts/python.exe tools/benchmark_latency.py` (server warm) → median in the ~135-145 ms band.
- [ ] **Step 5: CHANGELOG** — under 1.0.0 Highlights add the hub bullet (window with status, latency, recents, quick actions, live press-to-capture keybinds; hotkeys tab moved out of settings).
- [ ] **Step 6: Final commit** — `git add CHANGELOG.md && git commit -m "docs: changelog — the Parley Hub"`.
