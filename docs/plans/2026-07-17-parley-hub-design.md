# Parley Hub — design

2026-07-17 · approved direction: option A ("the hub is the primary window"), decided
in-chat with Parusan. Scope answers locked: opens on every launch with a
show-on-startup toggle; press-to-capture keybinds applied live (no restart); home
view = status lights + latency & recent dictations + full keybind panel + quick
actions. Parley-only (Amadeus keeps its own dashboard; the hub shows one
reachability light for it).

One approved deviation from the raw option-A sentence: the existing
General/Audio/Post-process settings tabs stay in the settings window
(`parley --settings`) rather than being ported into the hub. Keybinds move OUT
of settings and live ONLY in the hub (one source of truth; the settings window's
Hotkeys tab is removed and its quick action links to the hub's keybind panel).
Rationale: porting three restart-to-apply tabs in-process duplicates ~400 lines
of maintained UI for no user-named value; every capability Parusan asked for
ships without it.

## Hosting

In-process daemon thread with its own tk root and mainloop — the cheat_sheet
pattern (singleton guard, Esc/Ctrl+W close, `theme.init_window`). NOT the
history/settings subprocess pattern: the hub needs live references to
`HotkeyListener.change_hotkey_config` (rebind without restart),
`StateManager` (state lights), and `ConfigManager` (persist + read).
Opened from: (a) startup in `main.py` when `hub.show_on_startup` (new config,
default true); (b) tray menu item "Hub..." at the top of the menu.

## Files

- `src/parley/hub.py` (new) — the window. Two-column card layout from
  `theme.py` primitives (`card`, `button`, `checkbox`, `key_chip`, `scrollbar`):
  left column = Status card (lights), Dictations card (median latency headline +
  last 5 transcripts with copy), Actions card (Settings, History, Cheat sheet,
  Dictionary, Pause/Resume hotkeys, Restart Parley); right column = Keybinds
  card (every `hotkey.*` bind + recording-mode toggle); footer = "Show hub when
  Parley starts" checkbox. A 2 s `after()` loop reads the prober's latest
  snapshot; no cross-thread tk calls.
- `src/parley/hub_probes.py` (new) — headless status logic. Pure probe
  functions with injectable ports/clients so tests never touch the network:
  `vulkan_server_up(host, port)` (TCP connect, mirrors doctor), `ollama_up(endpoint)`
  (GET /api/tags), `amadeus_up(ask_endpoint)` (GET derived /health, 2 s),
  `mic_present(configured_name)` (sounddevice enumeration), plus a `Prober`
  daemon thread refreshing a snapshot dict every 2 s while the hub is open.
- `src/parley/keybind_capture.py` (new) — combo logic + capture. Pure:
  `normalize_combo(lib_str) -> config_str` (keyboard-lib names → config vocab:
  `left ctrl→ctrl`, `windows→win`, lowercase, `+`-joined, stable modifier
  order), `combos_equal(a, b)` (canonicalize via the platform KEY_MAP so
  `ctrl+space == control + space`), `find_conflict(candidate, binds) ->
  str | None`. Capture: `capture_combo(on_done, cancel_event)` worker thread
  around `keyboard.read_hotkey(suppress=False)`; Esc result = cancel.
- `src/parley/router_client.py` — add `health(cfg) -> bool` (GET
  `<endpoint base>/health`, 2 s, never raises), same base-derivation as
  `send_event`.
- `src/parley/state_manager.py` + `src/parley/transcript_log.py` — record real
  latency: measure wall-clock around the transcription call in the dictation
  path and pass `latency_ms=int(...)` into `record_transcript`, stored as a new
  optional field. Old entries lack it; readers tolerate absence. Hub shows
  median of the last 20 entries that have it, "—" until data exists.
- `src/parley/settings_ui.py` — remove the Hotkeys tab (`_build_hotkeys_tab`
  and its pairs list); keybinds are hub-owned now.
- `src/parley/system_tray.py` — "Hub..." menu item; opens via a `hub_deps`
  dict injected from main (no import cycles).
- `src/parley/main.py` — build `hub_deps = {config_manager, state_manager,
  hotkey_listener, transforms_manager}` once all exist; hand to SystemTray;
  open the hub at startup when configured.
- `config.defaults.yaml` — `hub: { show_on_startup: true }`.

## Keybind edit flow (the headline feature)

Row "change" → modal capture card: listener paused
(`hotkey_listener.stop_listening()`), `keyboard.read_hotkey` captures the
pressed combo, listener restarted. Candidate normalized → conflict-checked
against the other core binds (inline error, keep old bind) → persisted via
`config_manager.update_user_setting('hotkey', name, combo)` → applied live via
`hotkey_listener.change_hotkey_config(name, combo)`. If apply raises, revert
the config value and show the error inline. Optional binds (auto-send, command,
rephrase, assistant, pause) get a "clear" affordance storing ''. recording_mode
is a two-state chip (push_to_talk / toggle) through the same persist+apply path.

## Error handling

Probes never raise (every failure = a red light + reason string). The prober
thread and the hub thread are daemons; any crash in the hub cannot touch the
dictation path. Capture is cancel-safe: the discard flag ignores a late
read_hotkey result. Rebind failures roll back config so yaml and the live
listener never disagree.

## Testing

Pure-logic pytest coverage (no UI): combo normalization + equality + conflict
matrix; latency field round-trip through record/load including old entries
without the field; probe classification with fake sockets/transports;
hub.show_on_startup default present. Repo baselines that must hold: full suite
green, `--doctor`/`--selftest` exit 0, warm-latency benchmark unaffected (the
dictation path gains two `time.time()` calls only). Live smoke: launch from
source, hub opens, screenshot; change a bind end-to-end and see it live.

## Out of scope

Porting General/Audio/Post-process tabs into the hub; backfilling latency for
old history; per-app transform hotkey editing; macOS hub; any Amadeus control
beyond the one reachability light.
