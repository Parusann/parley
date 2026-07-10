# first_run.py
# Shows a one-time welcome window on the very first launch.
# A flag file in %APPDATA%\parley marks completion so the window never
# re-appears unless the user deletes that file. The window is intentionally
# minimal: 3 tips + a privacy promise, no settings, no wizard. The full
# wizard lives in setup_wizard.py for users who run `--setup` explicitly.

import logging
import threading
from pathlib import Path

from .utils import get_user_app_data_path

logger = logging.getLogger(__name__)

# Sentinel file written after the user dismisses the welcome window.
_FLAG_FILE = 'first_run_complete.txt'


# Returns True only when the user has never completed first-run before.
# Cheap call — just a filesystem stat — safe to invoke on every launch.
def is_first_run() -> bool:
    return not (Path(get_user_app_data_path()) / _FLAG_FILE).exists()


# Called from the welcome window's Close handler. Writes the sentinel so we
# don't re-show on next launch.
def mark_first_run_complete():
    try:
        path = Path(get_user_app_data_path())
        path.mkdir(parents=True, exist_ok=True)
        (path / _FLAG_FILE).write_text("ok", encoding='utf-8')
    except Exception as e:
        logger.debug(f"Could not write first-run flag: {e}")


# Spawns the welcome window on a daemon thread so it doesn't block the main
# app from starting up. `hotkey_label` is the user's *current* configured
# recording hotkey, displayed in the tip text so it's accurate.
def show_welcome_window(on_close=None, hotkey_label: str = "Ctrl+Win"):
    threading.Thread(
        target=_run_welcome,
        args=(on_close, hotkey_label),
        daemon=True,
        name='welcome-window',
    ).start()


def _run_welcome(on_close, hotkey_label):
    try:
        import tkinter as tk
    except ImportError:
        logger.warning("Tkinter unavailable — skipping welcome window")
        if on_close:
            on_close()
        return

    from . import theme
    from .theme import px, GAP_XS, GAP_M, GAP_L, GAP_XL

    theme.ensure_dpi_aware()
    root = tk.Tk()
    pal = theme.init_window(root, "Welcome to Parley")
    root.geometry(f"{px(560)}x{px(480)}")
    root.resizable(False, False)
    try:
        root.attributes('-topmost', True)
    except Exception:
        pass

    container = tk.Frame(root, bg=pal['bg'])
    container.pack(fill='both', expand=True, padx=px(GAP_XL), pady=px(20))

    theme.heading(container, "Welcome to Parley", pal, size=16).pack(anchor='w')
    theme.caption(container, "Free, fully offline AI dictation. Three quick things:",
                  pal).pack(anchor='w', pady=(px(2), px(GAP_M)))

    items = [
        ("1", f"Hold {hotkey_label} anywhere, speak, release.",
         "Your words appear at the cursor in any app — chat, code, browser, terminal."),
        ("2", "The tray icon shows status & settings.",
         "On Windows the tray hides icons by default — click the ^ arrow on the taskbar "
         "and drag the Parley icon out for one-click access."),
        ("3", "Run --doctor or --selftest if anything's off.",
         "Both ship with the app and surface common issues (mic permissions, missing model, etc.)."),
    ]

    for num, title, body in items:
        row = theme.card(container, pal)
        row.pack(fill='x', pady=px(GAP_XS))
        inner = tk.Frame(row.inner, bg=pal['bg_raised'])
        inner.pack(fill='x', padx=px(GAP_M), pady=px(10))
        # Numbered accent chip — same visual language as the hotkey chips.
        theme.key_chip(inner, num, pal).pack(side='left', anchor='n')
        body_frame = tk.Frame(inner, bg=pal['bg_raised'])
        body_frame.pack(side='left', fill='x', expand=True, padx=(px(10), 0))
        tk.Label(body_frame, text=title, bg=pal['bg_raised'], fg=pal['fg'],
                 font=theme.font(10, 'bold'),
                 anchor='w', justify='left', wraplength=px(430)).pack(fill='x')
        theme.caption(body_frame, body, pal, wrap=430,
                      bg=pal['bg_raised']).pack(fill='x', pady=(px(2), 0))

    tk.Label(container,
             text="No audio or transcripts ever leave your machine. Promise.",
             bg=pal['bg'], fg=pal['fg_dim'],
             font=theme.font(9, italic=True)).pack(anchor='w', pady=(px(GAP_M), 0))

    # Opt-in autostart: offered here, off unless the user ticks it. Only shown on
    # platforms where we can actually wire it up (Windows / macOS).
    from . import autostart
    autostart_var = tk.BooleanVar(value=False)
    if autostart.is_supported():
        cb = tk.Checkbutton(
            container, variable=autostart_var,
            text="  Start Parley automatically when I log in",
            bg=pal['bg'], fg=pal['fg'], selectcolor=pal['bg_input'],
            activebackground=pal['bg'], activeforeground=pal['fg'],
            font=theme.font(9), anchor='w', bd=0, highlightthickness=0,
        )
        cb.pack(anchor='w', pady=(px(10), 0))

    btn_frame = tk.Frame(container, bg=pal['bg'])
    btn_frame.pack(fill='x', pady=(px(GAP_L), 0))

    def _done():
        mark_first_run_complete()
        if autostart_var.get():
            try:
                autostart.enable()
            except Exception as e:
                logger.debug(f"Autostart enable from welcome failed: {e}")
        try:
            root.destroy()
        except Exception:
            pass
        if on_close:
            on_close()

    theme.button(btn_frame, "Got it — let's dictate", _done, pal,
                 primary=True).pack(side='right')

    root.protocol("WM_DELETE_WINDOW", _done)
    root.mainloop()
