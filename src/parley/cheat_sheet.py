# cheat_sheet.py
# Pops up a window showing the user's currently configured hotkeys. Users forget
# their bindings mid-week, especially after editing user_settings.yaml. Reading
# the live config (not the defaults) means what the user sees is always what
# the app actually responds to. Also lists transform hotkeys when transforms_manager
# is supplied.

import logging
import threading

logger = logging.getLogger(__name__)

# Singleton guard — re-invoking the tray menu item or the CLI flag should just
# raise the existing window instead of stacking duplicates.
_thread_lock = threading.Lock()
_current_root = None


# Public entry point. Pass config_manager and transforms_manager from the running
# app when calling from the tray; the CLI path (--cheat-sheet) constructs a fresh
# ConfigManager since no app instance is running.
def show_cheat_sheet(config_manager=None, transforms_manager=None):
    global _current_root
    with _thread_lock:
        try:
            if _current_root and _current_root.winfo_exists():
                _current_root.lift()
                _current_root.focus_force()
                return
        except Exception:
            pass

    threading.Thread(
        target=_run,
        args=(config_manager, transforms_manager),
        daemon=True,
        name='cheat-sheet',
    ).start()


def _run(config_manager, transforms_manager):
    global _current_root
    try:
        import tkinter as tk
    except ImportError:
        logger.warning("Tkinter not available; cheat sheet disabled")
        return

    from . import theme
    from .theme import px, GAP_S, GAP_M, GAP_L

    if config_manager is None:
        try:
            from .config_manager import ConfigManager
            config_manager = ConfigManager(quiet=True)
        except Exception as e:
            logger.error(f"Could not load config: {e}")
            return

    cfg = config_manager.config or {}
    hk = cfg.get('hotkey', {}) or {}

    items = [
        ("Record / dictate", hk.get('recording_hotkey'),
         "Hold to start recording (release to stop in push-to-talk mode)"),
        ("Stop & paste", hk.get('stop_key'),
         "Stop recording and deliver text to the cursor"),
        ("Stop & auto-send (Enter)", hk.get('auto_send_key'),
         "Stop, paste, and press Enter — perfect for chat apps"),
        ("Cancel recording", hk.get('cancel_combination'),
         "Discard the current recording without transcribing"),
        ("Voice command mode", hk.get('command_hotkey'),
         "Say a trigger phrase from commands.yaml to run shortcuts/macros"),
        ("AI rephrase (PTT)", hk.get('rephrase_hotkey'),
         "Select text, hold, speak your instruction, release — local Ollama rewrites it"),
        ("Pause all hotkeys", hk.get('pause_hotkey'),
         "Disable every Parley hotkey until pressed again"),
    ]

    transforms = []
    if transforms_manager:
        try:
            transforms = [
                (t.get('name'), t.get('hotkey'), t.get('prompt', '')[:60])
                for t in transforms_manager.list_transforms()
                if t.get('hotkey')
            ]
        except Exception:
            pass

    theme.ensure_dpi_aware()
    root = tk.Tk()
    with _thread_lock:
        _current_root = root

    pal = theme.init_window(root, "Parley — Hotkey Cheat Sheet")
    root.geometry(f"{px(660)}x{px(560)}")
    root.resizable(False, True)

    header = tk.Frame(root, bg=pal['bg'])
    header.pack(fill='x', padx=px(20), pady=(px(18), px(4)))
    theme.heading(header, "Your current hotkeys", pal).pack(anchor='w')
    hdr_row = tk.Frame(header, bg=pal['bg'])
    hdr_row.pack(fill='x', pady=(0, px(4)))
    theme.caption(hdr_row, "Change these in Settings → Hotkeys.",
                  pal).pack(side='left')
    theme.restart_chip(hdr_row, pal).pack(side='left', padx=(px(GAP_S), 0))

    # Scrollable body — transform lists can overflow any fixed height, so
    # rows live on a canvas-backed frame with the themed scrollbar.
    wrap = tk.Frame(root, bg=pal['bg'])
    wrap.pack(fill='both', expand=True, padx=px(20), pady=(px(6), px(10)))
    scroll_canvas = tk.Canvas(wrap, bg=pal['bg'], highlightthickness=0, bd=0)
    scroll = theme.scrollbar(wrap, scroll_canvas.yview, pal)
    scroll_canvas.configure(yscrollcommand=scroll.set)
    scroll.pack(side='right', fill='y')
    scroll_canvas.pack(side='left', fill='both', expand=True)
    body = tk.Frame(scroll_canvas, bg=pal['bg'])
    body_item = scroll_canvas.create_window(0, 0, window=body, anchor='nw')
    body.bind('<Configure>',
              lambda e: scroll_canvas.configure(scrollregion=scroll_canvas.bbox('all')))
    scroll_canvas.bind('<Configure>',
                       lambda e: scroll_canvas.itemconfigure(body_item, width=e.width))
    # Sign-symmetric for precision touchpads (±30 deltas), not just ±120 mice.
    root.bind('<MouseWheel>',
              lambda e: scroll_canvas.yview_scroll(-1 if e.delta > 0 else 1, 'units'))

    # One rounded card row per binding: name | key chip | what it does.
    def _row(parent, label, key, desc):
        row = theme.card(parent, pal, radius=theme.RADIUS_CTRL)
        row.pack(fill='x', pady=px(3))
        inner = tk.Frame(row.inner, bg=pal['bg_raised'])
        inner.pack(fill='x', padx=px(GAP_M), pady=px(GAP_S))
        tk.Label(inner, text=label, bg=pal['bg_raised'], fg=pal['fg'],
                 font=theme.font(10, 'bold'),
                 anchor='w', width=24).pack(side='left')
        key_text = (key or '—').upper() if key else '—'
        chip = theme.key_chip(inner, key_text, pal)
        if not key:
            # Unbound actions get a muted chip so the eye lands on real keys.
            chip.configure(fg=pal['fg_faint'])
        chip.pack(side='left', padx=(0, px(10)))
        # Wrap short of the widest chip row so long descriptions never clip.
        theme.caption(inner, desc, pal, wrap=240,
                      bg=pal['bg_raised']).pack(side='left', fill='x', expand=True)

    for label, key, desc in items:
        _row(body, label, key, desc)

    if transforms:
        theme.heading(body, "Transform hotkeys", pal, size=11).pack(
            anchor='w', pady=(px(GAP_L), px(4)))
        for name, key, prompt in transforms:
            _row(body, name.title(), key, prompt + ('…' if len(prompt) >= 60 else ''))

    footer = tk.Frame(root, bg=pal['bg'])
    footer.pack(fill='x', padx=px(20), pady=(0, px(GAP_L)))
    theme.caption(footer, "Tip: press Ctrl+W or Esc to close this window.",
                  pal).pack(side='left')
    theme.button(footer, "Close", root.destroy, pal).pack(side='right')

    root.bind('<Escape>', lambda e: root.destroy())
    root.bind('<Control-w>', lambda e: root.destroy())

    def _clear_ref():
        global _current_root
        with _thread_lock:
            _current_root = None
    root.protocol("WM_DELETE_WINDOW", lambda: (_clear_ref(), root.destroy()))

    root.mainloop()
    _clear_ref()
