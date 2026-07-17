# fallback_window.py
# Last-resort capture window shown when delivery fails (no focused text field,
# paste blocked, etc.). The transcript is displayed pre-selected and is already
# on the clipboard, so a dictation is never lost — worst case it's one Ctrl+V
# away. One instance at a time; a duplicate show() is a safe no-op.

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# Design-pixel dimensions — scaled through theme.px() at render time.
WIDTH = 520
MIN_HEIGHT = 230
MAX_HEIGHT = 500


class FallbackWindow:
    def __init__(self):
        self._available = self._check_tk()
        # Guard against stacking: only one fallback window (and its Tk root) is
        # alive at a time. This both avoids a window pile-up on repeated failed
        # deliveries and keeps us to a single transient Tk root coexisting with
        # the always-on level overlay. The transcript is copied to the clipboard
        # by the caller BEFORE show() is called, so skipping a duplicate never
        # loses the text — it's always recoverable with Ctrl+V.
        self._lock = threading.Lock()
        self._open = False

    def _check_tk(self) -> bool:
        try:
            import tkinter  # noqa
            return True
        except ImportError:
            return False

    def show(self, transcript: str, reason: Optional[str] = None):
        if not self._available or not transcript:
            return
        with self._lock:
            if self._open:
                logger.info("Fallback window already open; skipping duplicate "
                            "(transcript is already on the clipboard).")
                return
            self._open = True
        thread = threading.Thread(
            target=self._run_window,
            args=(transcript, reason or "No text field was focused — your dictation is safe here."),
            daemon=True,
            name='fallback-window',
        )
        thread.start()

    def _run_window(self, transcript: str, reason: str):
        try:
            import tkinter as tk
            import pyperclip

            from . import theme
            from .theme import px, GAP_S, GAP_M

            theme.ensure_dpi_aware()
            root = tk.Tk()
            pal = theme.init_window(root, "Parley — Dictation captured")
            # This is a delivery-failure alert — the one window that SHOULD
            # fight for attention, so it keeps -topmost (others don't).
            try:
                root.attributes('-topmost', True)
            except Exception:
                pass

            # Size to the transcript (in design px), clamped so a one-liner
            # doesn't get a cavernous window and an essay doesn't fill the screen.
            line_count = max(3, min(transcript.count('\n') + 1 + len(transcript) // 60, 18))
            # ~150 design px of fixed chrome (heading, reason, buttons, padding)
            # plus ~21 px per wrapped text line at the 10 pt body size.
            height = min(MAX_HEIGHT, max(MIN_HEIGHT, 150 + line_count * 21))

            w, h = px(WIDTH), px(height)
            try:
                root.update_idletasks()
                sw = root.winfo_screenwidth()
                sh = root.winfo_screenheight()
                root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")
            except Exception:
                root.geometry(f"{w}x{h}")

            outer = tk.Frame(root, bg=pal['bg'], bd=0, highlightthickness=0)
            outer.pack(fill='both', expand=True, padx=px(14), pady=px(GAP_M))

            theme.heading(outer, "Dictation captured", pal, size=12).pack(fill='x')

            theme.caption(outer, reason, pal,
                          wrap=WIDTH - 32).pack(fill='x', pady=(px(2), px(GAP_S)))

            text_card = theme.card(outer, pal)
            text_card.pack(fill='both', expand=True, pady=(0, px(10)))
            text_frame = tk.Frame(text_card.inner, bg=pal['bg_raised'])
            text_frame.pack(fill='both', expand=True, padx=px(4), pady=px(4))

            text_widget = tk.Text(
                text_frame, wrap='word',
                bg=pal['bg_raised'], fg=pal['fg'],
                insertbackground=pal['fg'],
                selectbackground=pal['selection'], selectforeground=pal['fg'],
                bd=0, highlightthickness=0,
                padx=px(8), pady=px(GAP_S),
                font=theme.font(10),
                relief='flat',
                # Request only as many rows as the transcript needs — Text's
                # default 24-row request would starve the button row below it.
                height=line_count,
            )
            # Long transcripts overflow the 500px window clamp — give the text
            # a themed scrollbar instead of silently hiding the tail.
            scroll = theme.scrollbar(text_frame, text_widget.yview, pal)
            text_widget.configure(yscrollcommand=scroll.set)
            scroll.pack(side='right', fill='y')
            text_widget.pack(side='left', fill='both', expand=True)
            text_widget.insert('1.0', transcript)
            text_widget.tag_add('sel', '1.0', 'end-1c')

            button_row = tk.Frame(outer, bg=pal['bg'])
            button_row.pack(fill='x')

            status_var = tk.StringVar(value="Already on your clipboard — just paste anywhere with Ctrl+V.")
            status = tk.Label(button_row, textvariable=status_var,
                              bg=pal['bg'], fg=pal['fg_dim'], font=theme.font(9))
            status.pack(side='left')

            def _copy_again(_=None):
                try:
                    pyperclip.copy(transcript)
                    status_var.set("Copied! Paste anywhere with Ctrl+V.")
                    btn_copy.configure(text="✓ Copied")
                    root.after(1200, lambda: btn_copy.configure(text="Copy"))
                except Exception as e:
                    status_var.set(f"Copy failed: {e}")

            def _dismiss(_=None):
                try:
                    root.destroy()
                except Exception:
                    pass

            btn_close = theme.button(button_row, "Close", _dismiss, pal)
            btn_close.pack(side='right', padx=(px(GAP_S), 0))

            btn_copy = theme.button(button_row, "Copy", _copy_again, pal, primary=True)
            btn_copy.pack(side='right')

            root.bind('<Escape>', _dismiss)
            root.bind('<Control-c>', _copy_again)
            text_widget.focus_set()

            try:
                pyperclip.copy(transcript)
            except Exception:
                pass

            root.mainloop()
        except Exception as e:
            logger.warning(f"Fallback window failed: {e}")
        finally:
            with self._lock:
                self._open = False
