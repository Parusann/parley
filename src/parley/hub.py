# hub.py
# The Parley Hub — the window that opens with the app: live status lights,
# real dictation latency + recent transcripts, quick actions, and the keybind
# editor (press-to-capture, applied instantly through HotkeyListener — no
# restart). Hosted like cheat_sheet.py: in-process daemon thread with its own
# tk root, singleton-guarded, so it holds live refs to the running app.

import logging
import statistics
import subprocess
import sys
import threading

from . import hub_probes, transcript_log
from .keybind_capture import capture_combo, find_conflict

logger = logging.getLogger(__name__)

# Singleton guard — tray clicks and the startup open share one window.
_thread_lock = threading.Lock()
_current_root = None

# (config key under hotkey.*, human label, clearable)
BIND_ROWS = [
    ('recording_hotkey', 'Record', False),
    ('stop_key', 'Stop', False),
    ('auto_send_key', 'Auto-send', True),
    ('cancel_combination', 'Cancel recording', True),
    ('command_hotkey', 'Command mode', True),
    ('rephrase_hotkey', 'Rephrase', True),
    ('assistant_hotkey', 'Ask Amadeus', True),
    ('pause_hotkey', 'Pause all hotkeys', True),
]


def show_hub(deps: dict) -> None:
    """Open (or lift) the hub. deps: config_manager, state_manager,
    hotkey_listener, transforms_manager, restart_app (callable)."""
    global _current_root
    with _thread_lock:
        try:
            if _current_root and _current_root.winfo_exists():
                _current_root.after(0, _current_root.deiconify)
                _current_root.after(0, _current_root.lift)
                return
        except Exception:
            pass

    threading.Thread(target=_run, args=(deps,), daemon=True, name='parley-hub').start()


def _spawn(flag: str) -> None:
    # The settings/history windows are their own processes (their mainloop
    # blocks by design); mirror system_tray._run_module_in_window.
    try:
        subprocess.Popen(
            [sys.executable, '-m', 'parley.main', flag],
            creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0),
        )
    except Exception as e:
        logger.error(f"Failed to launch {flag}: {e}")


def _median_latency_ms(entries: list) -> int | None:
    samples = [e['latency_ms'] for e in entries[:20] if isinstance(e.get('latency_ms'), int)]
    return int(statistics.median(samples)) if samples else None


def _run(deps):
    global _current_root
    try:
        import tkinter as tk
    except ImportError:
        logger.warning("Tkinter not available; hub disabled")
        return

    from . import theme
    from .theme import GAP_L, GAP_M, GAP_S, px

    cm = deps['config_manager']
    state_manager = deps.get('state_manager')
    listener = deps.get('hotkey_listener')

    root = tk.Tk()
    with _thread_lock:
        _current_root = root
    pal = theme.init_window(root, 'Parley Hub')

    cfg = cm.config or {}
    srv = (cfg.get('whisper') or {}).get('server') or {}
    vulkan_host = str(srv.get('host') or '127.0.0.1')
    try:
        vulkan_port = int(srv.get('port') or 8181)
    except (TypeError, ValueError):
        vulkan_port = 8181
    ollama_endpoint = str((((cfg.get('postprocess') or {}).get('ollama')) or {})
                          .get('endpoint') or 'http://127.0.0.1:11434')
    router_cfg = cfg.get('router') or {}
    audio_device = str((cfg.get('audio') or {}).get('device_name') or '')

    prober = hub_probes.Prober(probes={
        'vulkan': lambda: (hub_probes.vulkan_server_up(vulkan_host, vulkan_port),
                           f'{vulkan_host}:{vulkan_port}'),
        'ollama': lambda: (hub_probes.ollama_up(ollama_endpoint), ollama_endpoint),
        'amadeus': lambda: (hub_probes.amadeus_up(router_cfg),
                            str(router_cfg.get('endpoint') or 'router disabled')),
        'mic': lambda: hub_probes.mic_present(audio_device),
    })
    prober.start()

    outer = tk.Frame(root, bg=pal['bg'])
    outer.pack(fill='both', expand=True, padx=px(GAP_L), pady=px(GAP_M))

    theme.heading(outer, 'Parley Hub', pal, size=16).pack(anchor='w')
    theme.caption(outer, 'Everything at a glance — keybinds apply instantly.',
                  pal).pack(anchor='w', pady=(0, px(GAP_M)))

    columns = tk.Frame(outer, bg=pal['bg'])
    columns.pack(fill='both', expand=True)
    left = tk.Frame(columns, bg=pal['bg'])
    left.pack(side='left', fill='both', expand=True, padx=(0, px(GAP_M)))
    right = tk.Frame(columns, bg=pal['bg'])
    right.pack(side='left', fill='both', expand=True)

    # ── Status card ──────────────────────────────────────────────
    status_card = theme.card(left, pal)
    status_card.pack(fill='x', pady=(0, px(GAP_M)))
    theme.heading(status_card.inner, 'Status', pal, size=11).pack(anchor='w')

    status_rows = {}

    def _status_row(parent, key, label):
        row = tk.Frame(parent, bg=pal['bg_raised'])
        row.pack(fill='x', pady=px(2))
        dot = tk.Canvas(row, width=px(10), height=px(10), bg=pal['bg_raised'],
                        highlightthickness=0)
        dot.pack(side='left', padx=(0, px(GAP_S)))
        oid = dot.create_oval(px(1), px(1), px(9), px(9), fill=pal['fg_faint'], outline='')
        tk.Label(row, text=label, font=theme.font(10), bg=pal['bg_raised'],
                 fg=pal['fg']).pack(side='left')
        detail = tk.Label(row, text='checking…', font=theme.font(9), bg=pal['bg_raised'],
                          fg=pal['fg_dim'], anchor='e')
        detail.pack(side='right')
        status_rows[key] = (dot, oid, detail)

    _status_row(status_card.inner, 'model', 'Whisper model')
    _status_row(status_card.inner, 'vulkan', 'Vulkan server')
    _status_row(status_card.inner, 'mic', 'Microphone')
    _status_row(status_card.inner, 'ollama', 'Ollama cleanup')
    _status_row(status_card.inner, 'amadeus', 'Amadeus link')

    def _paint_status():
        snap = prober.snapshot()
        # Model light comes from the app itself, not a network probe.
        model_name = str(((cfg.get('whisper') or {}).get('model')) or '?')
        loading = bool(state_manager and state_manager.get_current_state() == 'model_loading')
        snap = dict(snap)
        snap['model'] = (not loading, model_name + (' (loading…)' if loading else ''))
        for key, (dot, oid, detail) in status_rows.items():
            ok, text = snap.get(key, (False, 'checking…'))
            dot.itemconfigure(oid, fill=pal['ok'] if ok else pal['error'])
            detail.configure(text=str(text)[:44])

    # ── Dictations card ──────────────────────────────────────────
    dict_card = theme.card(left, pal)
    dict_card.pack(fill='both', expand=True, pady=(0, px(GAP_M)))
    theme.heading(dict_card.inner, 'Dictations', pal, size=11).pack(anchor='w')
    latency_label = tk.Label(dict_card.inner, text='— ms · warm median',
                             font=theme.font(13, 'bold'), bg=pal['bg_raised'], fg=pal['fg'])
    latency_label.pack(anchor='w', pady=(px(2), px(GAP_S)))
    recents_frame = tk.Frame(dict_card.inner, bg=pal['bg_raised'])
    recents_frame.pack(fill='both', expand=True)

    def _copy(text):
        try:
            root.clipboard_clear()
            root.clipboard_append(text)
        except Exception:
            pass

    def _paint_recents():
        entries = transcript_log.load_transcripts()
        med = _median_latency_ms(entries)
        latency_label.configure(
            text=(f'{med} ms · warm median' if med is not None else '— ms · warm median'))
        for child in recents_frame.winfo_children():
            child.destroy()
        if not entries:
            theme.caption(recents_frame, 'No dictations yet.', pal,
                          bg=pal['bg_raised']).pack(anchor='w')
        for e in entries[:5]:
            row = tk.Frame(recents_frame, bg=pal['bg_raised'])
            row.pack(fill='x', pady=px(1))
            ts = str(e.get('timestamp', ''))[11:16]
            lat = e.get('latency_ms')
            note = f'{ts}' + (f' · {lat} ms' if isinstance(lat, int) else '')
            tk.Label(row, text=note, font=theme.font(8, mono=True), bg=pal['bg_raised'],
                     fg=pal['fg_dim']).pack(side='left')
            snippet = str(e.get('text', ''))
            snippet = snippet[:46] + ('…' if len(snippet) > 46 else '')
            tk.Label(row, text=snippet, font=theme.font(9), bg=pal['bg_raised'],
                     fg=pal['fg'], anchor='w').pack(side='left', padx=px(GAP_S))
            theme.button(row, 'Copy', lambda t=e.get('text', ''): _copy(t),
                         pal).pack(side='right')

    # ── Actions card ─────────────────────────────────────────────
    actions_card = theme.card(left, pal)
    actions_card.pack(fill='x')
    theme.heading(actions_card.inner, 'Actions', pal, size=11).pack(anchor='w')
    grid = tk.Frame(actions_card.inner, bg=pal['bg_raised'])
    grid.pack(fill='x', pady=(px(GAP_S), 0))

    def _open_cheat_sheet():
        try:
            from .cheat_sheet import show_cheat_sheet
            show_cheat_sheet(cm, deps.get('transforms_manager'))
        except Exception as e:
            logger.error(f"Cheat sheet failed: {e}")

    def _open_add_word():
        try:
            from .dictionary import show_add_word_dialog
            show_add_word_dialog()
        except Exception as e:
            logger.error(f"Add-word dialog failed: {e}")

    def _toggle_pause():
        if listener is not None:
            listener.toggle_pause()
            paused = bool(getattr(listener, 'is_paused', False))
            pause_btn.configure(text='Resume hotkeys' if paused else 'Pause hotkeys')

    actions = [
        ('All settings…', lambda: _spawn('--settings')),
        ('History…', lambda: _spawn('--history')),
        ('Cheat sheet', _open_cheat_sheet),
        ('Add word…', _open_add_word),
    ]
    for i, (label, fn) in enumerate(actions):
        theme.button(grid, label, fn, pal).grid(row=i // 2, column=i % 2,
                                                sticky='ew', padx=px(2), pady=px(2))
    grid.columnconfigure(0, weight=1)
    grid.columnconfigure(1, weight=1)

    pause_row = tk.Frame(actions_card.inner, bg=pal['bg_raised'])
    pause_row.pack(fill='x', pady=(px(2), 0))
    initially_paused = bool(getattr(listener, 'is_paused', False))
    pause_btn = theme.button(
        pause_row, 'Resume hotkeys' if initially_paused else 'Pause hotkeys',
        _toggle_pause, pal)
    pause_btn.pack(side='left', expand=True, fill='x', padx=px(2))
    restart = deps.get('restart_app')
    if restart:
        theme.button(pause_row, 'Restart Parley', restart, pal,
                     danger=True).pack(side='left', expand=True, fill='x', padx=px(2))

    # ── Keybinds card (right column) ─────────────────────────────
    binds_card = theme.card(right, pal)
    binds_card.pack(fill='both', expand=True)
    theme.heading(binds_card.inner, 'Keybinds', pal, size=11).pack(anchor='w')
    theme.caption(binds_card.inner, 'Click Change, then press the new keys. '
                  'Esc cancels. Changes apply immediately.',
                  pal, bg=pal['bg_raised']).pack(anchor='w', pady=(0, px(GAP_S)))

    bind_rows = {}

    def _current_binds() -> dict:
        return {name: str(getattr(listener, name, '') or '') for name, _, _ in BIND_ROWS}

    def _paint_bind(name):
        chip_holder, err_label = bind_rows[name][2], bind_rows[name][3]
        for child in chip_holder.winfo_children():
            child.destroy()
        combo = str(getattr(listener, name, '') or '')
        if combo:
            theme.key_chip(chip_holder, combo, pal).pack(side='left')
        else:
            tk.Label(chip_holder, text='unset', font=theme.font(9, italic=True),
                     bg=pal['bg_raised'], fg=pal['fg_faint']).pack(side='left')
        err_label.configure(text='')

    def _set_error(name, message):
        bind_rows[name][3].configure(text=message or '')

    def _apply_bind(name, combo):
        """Persist + live-apply one bind; roll back so yaml and the live
        listener can never disagree. Runs on the tk thread."""
        old = str(getattr(listener, name, '') or '')
        if combo == old:
            return _paint_bind(name)
        clash = find_conflict(name, combo, _current_binds()) if combo else None
        if clash:
            return _set_error(name, f'already used by {clash}')
        cm.update_user_setting('hotkey', name, combo)
        try:
            listener.change_hotkey_config(name, combo if combo else None)
        except Exception as exc:
            cm.update_user_setting('hotkey', name, old)
            try:
                listener.change_hotkey_config(name, old if old else None)
            except Exception:
                pass
            return _set_error(name, f'could not apply: {exc}')
        _paint_bind(name)

    def _begin_capture(name):
        if listener is None:
            return
        _set_error(name, 'press keys… (Esc cancels)')
        cancel = threading.Event()
        listener.stop_listening()

        def done(combo):
            def _on_ui():
                listener.start_listening()
                if combo:
                    _apply_bind(name, combo)
                else:
                    _paint_bind(name)  # Esc — keep the old bind
            try:
                root.after(0, _on_ui)
            except Exception:
                listener.start_listening()

        capture_combo(done, cancel)

    for name, label, clearable in BIND_ROWS:
        row = tk.Frame(binds_card.inner, bg=pal['bg_raised'])
        row.pack(fill='x', pady=px(2))
        tk.Label(row, text=label, font=theme.font(10), bg=pal['bg_raised'], fg=pal['fg'],
                 width=16, anchor='w').pack(side='left')
        chip_holder = tk.Frame(row, bg=pal['bg_raised'])
        chip_holder.pack(side='left', fill='x', expand=True)
        controls = tk.Frame(row, bg=pal['bg_raised'])
        controls.pack(side='right')
        err_label = tk.Label(row, text='', font=theme.font(8), bg=pal['bg_raised'],
                             fg=pal['warn'])
        err_label.pack(side='right', padx=px(GAP_S))
        theme.button(controls, 'Change', lambda n=name: _begin_capture(n),
                     pal).pack(side='left', padx=px(1))
        if clearable:
            theme.button(controls, 'Clear', lambda n=name: _apply_bind(n, ''),
                         pal).pack(side='left', padx=px(1))
        bind_rows[name] = (row, controls, chip_holder, err_label)
        _paint_bind(name)

    # Recording mode — the same persist+apply path as a bind.
    mode_row = tk.Frame(binds_card.inner, bg=pal['bg_raised'])
    mode_row.pack(fill='x', pady=(px(GAP_S), 0))
    tk.Label(mode_row, text='Recording mode', font=theme.font(10), bg=pal['bg_raised'],
             fg=pal['fg'], width=16, anchor='w').pack(side='left')
    mode_label = tk.Label(mode_row, text='', font=theme.font(9, mono=True),
                          bg=pal['bg_raised'], fg=pal['fg_dim'])
    mode_label.pack(side='left')

    def _paint_mode():
        mode_label.configure(text=str(getattr(listener, 'recording_mode', 'toggle')))

    def _toggle_mode():
        current = str(getattr(listener, 'recording_mode', 'toggle'))
        new = 'toggle' if current == 'push_to_talk' else 'push_to_talk'
        cm.update_user_setting('hotkey', 'recording_mode', new)
        try:
            listener.change_hotkey_config('recording_mode', new)
        except Exception as exc:
            cm.update_user_setting('hotkey', 'recording_mode', current)
            logger.error(f"Recording-mode change failed: {exc}")
        _paint_mode()

    theme.button(mode_row, 'Switch', _toggle_mode, pal).pack(side='right')
    _paint_mode()

    # ── Footer ───────────────────────────────────────────────────
    footer = tk.Frame(outer, bg=pal['bg'])
    footer.pack(fill='x', pady=(px(GAP_M), 0))
    show_var = tk.BooleanVar(value=bool((cfg.get('hub') or {}).get('show_on_startup', True)))

    def _save_show():
        cm.update_user_setting('hub', 'show_on_startup', bool(show_var.get()))

    show_var.trace_add('write', lambda *_: _save_show())
    theme.checkbox(footer, 'Show hub when Parley starts', show_var, pal).pack(side='left')

    # ── Refresh loop + lifecycle ─────────────────────────────────
    def _tick():
        if not root.winfo_exists():
            return
        _paint_status()
        _paint_recents()
        root.after(2000, _tick)

    _paint_recents()
    root.after(300, _tick)

    def _close(*_):
        prober.stop()
        try:
            root.destroy()
        except Exception:
            pass

    root.protocol('WM_DELETE_WINDOW', _close)
    root.bind('<Escape>', _close)
    root.bind('<Control-w>', _close)

    try:
        root.mainloop()
    finally:
        prober.stop()
        with _thread_lock:
            _current_root = None
