# level_overlay.py
# Frameless always-on-top "glass" pill shown while recording/processing: a
# scrolling voice waveform (recent mic-level history) beside a status /
# streaming-text label. On Windows 11 it uses real acrylic blur-behind and
# rounded corners; elsewhere it falls back to a plain translucent panel.
# Runs its own tk mainloop on a daemon thread; all public methods are
# thread-safe via root.after(). All colors/metrics come from theme.py; the
# waveform fades older bars toward the quiet color (comet trail) using a
# color ramp precomputed once — the per-frame draw cost is unchanged.

import logging
import math
import platform
import threading
from collections import deque
from typing import Callable, Optional

from . import theme

logger = logging.getLogger(__name__)


def _lerp_hex(a: str, b: str, t: float) -> str:
    """Blend two #rrggbb colors; used once at init to build the trail ramp."""
    av = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    bv = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return '#%02x%02x%02x' % tuple(round(x + (y - x) * t) for x, y in zip(av, bv))


class LevelOverlay:
    LEVEL_AMP = 25.0
    UPDATE_HZ = 30
    FLASH_MS = 400
    ERROR_HOLD_MS = 2400  # failures need reading time; tray balloons are unreliable

    POSITIONS = {
        'bottom-center': 'BC',
        'bottom-right': 'BR',
        'bottom-left': 'BL',
        'top-center': 'TC',
        'top-right': 'TR',
        'top-left': 'TL',
    }

    def __init__(self, level_provider: Callable[[], float],
                 click_through: bool = True,
                 position: str = 'bottom-center'):
        self.level_provider = level_provider
        self.click_through = click_through
        self.position = position if position in self.POSITIONS else 'bottom-center'

        pal = theme.palette()
        self.BG = pal['bg']
        self.BAR = pal['wave_live']
        self.DIM = pal['wave_quiet']
        self.TEXT = pal['fg']
        self.TEXT_DIM = pal['fg_dim']
        self.ERROR = pal['error']

        # Physical-pixel metrics (theme.px handles monitor scale).
        self.WIDTH = theme.px(356)
        self.HEIGHT = theme.px(48)
        self.WAVE_AREA_PX = theme.px(168)
        self.BOTTOM_OFFSET_PX = theme.px(96)
        self.BAR_PITCH = theme.px(5)   # px between waveform bar centers
        self.BAR_W = theme.px(3)       # waveform bar width

        self.root: Optional[object] = None
        self._level_canvas = None
        self._text_var = None
        self._text_label = None
        self._thread = None
        self._ready = threading.Event()
        self._mode = 'hidden'
        self._smoothed = 0.0
        self._streaming_text = ''
        self._displayed_chars = 0
        self._flash_color = None
        self._reduced_motion = theme.reduced_motion()
        # Rolling mic-level history that becomes the scrolling waveform.
        self._wave = deque(maxlen=max(8, (self.WAVE_AREA_PX - theme.px(12)) // self.BAR_PITCH))
        # Comet-trail ramp: oldest bar sits near the quiet color, newest is full
        # accent. Precomputed so _draw never blends colors per frame.
        n = self._wave.maxlen
        self._ramp = [_lerp_hex(self.DIM, self.BAR, 0.35 + 0.65 * i / max(1, n - 1))
                      for i in range(n)]
        self._pulse_phase = 0.0
        # Tracks the pending _update_loop after() callback so a new mode switch
        # cancels the old animation loop instead of stacking a second one (which
        # would multiply redraw/CPU load over many record cycles).
        self._update_after_id = None
        self._available = self._check_tk()

    def _check_tk(self) -> bool:
        try:
            import tkinter  # noqa
            return True
        except ImportError:
            logger.info("Tkinter not available; level overlay disabled")
            return False

    def start(self):
        if not self._available or (self._thread and self._thread.is_alive()):
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name='level-overlay')
        self._thread.start()
        self._ready.wait(timeout=2.0)

    def show_recording(self):
        self._call(lambda: self._set_mode('recording'))

    def show_processing(self):
        self._call(lambda: self._set_mode('processing'))

    def hide(self):
        self._call(lambda: self._set_mode('hidden'))

    def flash_success(self):
        self._flash(self.BAR)

    # message: shown in the pill during the flash (held longer so it's readable).
    # Used to surface failures where the user is already looking, since Windows
    # often suppresses tray balloons.
    def flash_failure(self, message: str = None):
        self._flash(self.ERROR, message)

    def set_streaming_text(self, text: str):
        new_text = (text or '').strip()
        if new_text == self._streaming_text:
            return
        if not new_text.startswith(self._streaming_text):
            self._displayed_chars = 0
        self._streaming_text = new_text
        # Reduced motion: no typewriter reveal — text lands whole.
        if self._reduced_motion:
            self._displayed_chars = len(new_text)
            self._call(self._refresh_text)
        else:
            self._call(self._tick_typewriter)

    def _tick_typewriter(self):
        if not self._text_var or self._mode not in ('recording', 'processing'):
            return
        target = self._streaming_text
        if self._displayed_chars >= len(target):
            return
        self._displayed_chars = min(len(target), self._displayed_chars + 3)
        visible = target[:self._displayed_chars]
        display = '…' + visible[-31:] if len(visible) > 32 else visible
        try:
            self._text_var.set(display)
            self._text_label.configure(fg=self.TEXT)
        except Exception:
            return
        if self._displayed_chars < len(target):
            try:
                self.root.after(30, self._tick_typewriter)
            except Exception:
                pass

    def set_position(self, name: str):
        if name not in self.POSITIONS:
            return
        self.position = name
        self._call(self._position)

    def shutdown(self):
        if not self.root:
            return
        try:
            self.root.after(0, self.root.destroy)
        except Exception:
            pass

    def _call(self, fn):
        if not self._available or not self.root:
            return
        try:
            self.root.after(0, fn)
        except Exception as e:
            logger.debug(f"Overlay call failed: {e}")

    def _set_mode(self, mode: str):
        self._mode = mode
        if mode == 'hidden':
            self._streaming_text = ''
            self._displayed_chars = 0
            self._refresh_text()
            try: self.root.withdraw()
            except Exception: pass
        else:
            try: self.root.deiconify()
            except Exception: pass
            self._smoothed = 0.0
            self._pulse_phase = 0.0
            self._wave.clear()
            if mode == 'recording':
                self._displayed_chars = 0
            # Cancel any in-flight loop before starting a fresh one.
            if self._update_after_id is not None:
                try: self.root.after_cancel(self._update_after_id)
                except Exception: pass
                self._update_after_id = None
            self._update_loop()
            self._refresh_text()

    def _refresh_text(self):
        if not self._text_var:
            return
        # Idle prompts sit quieter than live dictated words.
        if self._mode == 'processing':
            self._text_var.set('Transcribing…')
            self._text_label.configure(fg=self.TEXT_DIM)
        elif self._streaming_text:
            text = self._streaming_text
            if len(text) > 32:
                text = '…' + text[-31:]
            self._text_var.set(text)
            self._text_label.configure(fg=self.TEXT)
        else:
            self._text_var.set('Listening…')
            self._text_label.configure(fg=self.TEXT_DIM)

    def _flash(self, color: str, message: str = None):
        if not self._available:
            return

        def do():
            if not self.root:
                return
            self._mode = 'flash'
            self._flash_color = color
            if message and self._text_var:
                try:
                    self._text_var.set(message)
                    self._text_label.configure(fg=self.ERROR if color == self.ERROR else self.TEXT)
                except Exception: pass
            try: self.root.deiconify()
            except Exception: pass
            self._draw(1.0, override_color=color)
            # Hold a message flash longer so it's actually readable.
            hold = self.ERROR_HOLD_MS if message else self.FLASH_MS
            try:
                self.root.after(hold, lambda: self._set_mode('hidden'))
            except Exception:
                pass
        self._call(do)

    def _run(self):
        try:
            import tkinter as tk
            self.root = tk.Tk()
            self.root.withdraw()
            self.root.overrideredirect(True)
            self.root.attributes('-topmost', True)
            try:
                self.root.tk.call('tk', 'scaling', theme.dpi_scale() * 96.0 / 72.0)
            except Exception:
                pass

            outer = tk.Frame(self.root, bg=self.BG, bd=0, highlightthickness=0)
            outer.pack(fill='both', expand=True)

            self._level_canvas = tk.Canvas(outer, width=self.WAVE_AREA_PX, height=self.HEIGHT,
                                           bg=self.BG, highlightthickness=0, borderwidth=0)
            self._level_canvas.pack(side='left', padx=(theme.px(12), 0))

            self._text_var = tk.StringVar(value='')
            self._text_label = tk.Label(outer, textvariable=self._text_var,
                                        bg=self.BG, fg=self.TEXT_DIM,
                                        font=theme.font(9),
                                        anchor='w')
            self._text_label.pack(side='left', fill='x', expand=True,
                                  padx=(theme.px(10), theme.px(12)))

            self._position()
            self._apply_glass()
            self._make_click_through()
            self._ready.set()
            self.root.mainloop()
        except Exception as e:
            logger.warning(f"Level overlay crashed: {e}")
            self._available = False
            self._ready.set()

    def _position(self):
        try:
            self.root.update_idletasks()
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()

            margin = self.BOTTOM_OFFSET_PX
            top_margin = theme.px(60)
            x_margin = theme.px(30)

            pos = self.POSITIONS[self.position]
            if pos.startswith('B'):
                y = screen_h - margin
            else:
                y = top_margin
            if pos.endswith('C'):
                x = (screen_w - self.WIDTH) // 2
            elif pos.endswith('R'):
                x = screen_w - self.WIDTH - x_margin
            else:
                x = x_margin

            self.root.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")
        except Exception as e:
            logger.debug(f"Position failed: {e}")

    # All win32 styling must target the top-level *wrapper* hwnd. Tk's
    # winfo_id() returns the client child; giving that child WS_EX_LAYERED
    # without initializing a layered surface makes every widget invisible.
    def _top_hwnd(self):
        import ctypes
        child = self.root.winfo_id()
        return ctypes.windll.user32.GetParent(child) or child

    # Glass styling. Windows 11: acrylic blur-behind + rounded corners. The BG
    # color is declared as the layered transparent color, so every BG pixel is
    # replaced by the tinted blur while bars/text stay solid. If acrylic isn't
    # available the window keeps an opaque BG with plain alpha translucency —
    # never punch color-key holes without a blur backdrop behind them.
    def _apply_glass(self):
        if platform.system() != 'Windows':
            try: self.root.attributes('-alpha', 0.93)
            except Exception: pass
            return
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = self._top_hwnd()

            # Rounded corners (Win11+; harmless no-op on Win10)
            try:
                pref = ctypes.c_int(2)  # DWMWCP_ROUND
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref),
                                                           ctypes.sizeof(pref))
            except Exception:
                pass

            class ACCENTPOLICY(ctypes.Structure):
                _fields_ = [('AccentState', ctypes.c_uint),
                            ('AccentFlags', ctypes.c_uint),
                            ('GradientColor', ctypes.c_uint),
                            ('AnimationId', ctypes.c_uint)]

            class WCADATA(ctypes.Structure):
                _fields_ = [('Attribute', ctypes.c_uint),
                            ('Data', ctypes.c_void_p),
                            ('SizeOfData', ctypes.c_size_t)]

            accent = ACCENTPOLICY(4, 2, theme.acrylic_tint(), 0)  # ACCENT_ENABLE_ACRYLICBLURBEHIND
            data = WCADATA(19,  # WCA_ACCENT_POLICY
                           ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p),
                           ctypes.sizeof(accent))
            ok = user32.SetWindowCompositionAttribute(ctypes.c_void_p(hwnd), ctypes.byref(data))
            if ok:
                self.root.attributes('-transparentcolor', self.BG)
            else:
                self.root.attributes('-alpha', 0.93)
        except Exception as e:
            logger.debug(f"Glass styling unavailable, using plain translucency: {e}")
            try: self.root.attributes('-alpha', 0.93)
            except Exception: pass

    def _make_click_through(self):
        if not self.click_through or platform.system() != 'Windows':
            return
        try:
            import ctypes
            hwnd = self._top_hwnd()
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x80000
            WS_EX_TRANSPARENT = 0x20
            LWA_ALPHA = 0x2
            user32 = ctypes.windll.user32
            current = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, current | WS_EX_LAYERED | WS_EX_TRANSPARENT)
            # A layered window renders nothing until its surface is initialized;
            # only do it here if tk hasn't already (via -alpha/-transparentcolor),
            # as SetLayeredWindowAttributes would clobber a color-key setup.
            if not (self.root.attributes('-alpha') < 1.0
                    or self.root.attributes('-transparentcolor')):
                user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
        except Exception as e:
            logger.debug(f"Click-through setup failed: {e}")

    def _update_loop(self):
        if self._mode not in ('recording', 'processing') or not self.root:
            return
        try:
            level = float(self.level_provider())
        except Exception:
            level = 0.0

        target = min(1.0, max(0.0, level * self.LEVEL_AMP))
        if self._mode == 'processing':
            if self._reduced_motion:
                # No decorative ripple: draw one calm static frame and stop.
                self._wave.extend([0.35] * self._wave.maxlen)
                self._draw(0.35)
                self._update_after_id = None
                return
            # No mic level while transcribing: show a calm travelling ripple.
            self._pulse_phase += 0.35
            target = 0.30 + 0.22 * (1 + math.sin(self._pulse_phase)) / 2
        self._smoothed = self._smoothed * 0.45 + target * 0.55
        self._wave.append(self._smoothed)
        self._draw(self._smoothed)

        try:
            self._update_after_id = self.root.after(int(1000 / self.UPDATE_HZ), self._update_loop)
        except Exception:
            self._update_after_id = None

    # Draws the scrolling waveform: newest level enters at the right edge and
    # carries the brightest color; history fades toward the quiet color.
    # Rounded line caps make each bar a soft pill rather than a hard block.
    # override_color (flash) paints a full static arch instead of live history.
    def _draw(self, level: float, override_color: Optional[str] = None):
        if not self._level_canvas:
            return
        try:
            c = self._level_canvas
            c.delete('all')

            pad = theme.px(6)
            mid = self.HEIGHT // 2
            n = self._wave.maxlen
            max_half = (self.HEIGHT - theme.px(16)) / 2.0
            quiet_half = max(1.0, theme.px(1))

            if override_color:
                vals = [0.35 + 0.65 * math.sin(math.pi * i / (n - 1)) for i in range(n)]
                colors = [override_color] * n
            else:
                vals = list(self._wave)
                vals = [0.0] * (n - len(vals)) + vals
                colors = self._ramp

            x = pad + self.BAR_W / 2.0
            for i, v in enumerate(vals):
                if v > 0.045:
                    # sqrt ease keeps quiet speech visible without clipping loud peaks
                    half = max(quiet_half, math.sqrt(v) * max_half)
                    fill = colors[i]
                else:
                    half = quiet_half
                    fill = self.DIM
                c.create_line(x, mid - half, x, mid + half,
                              fill=fill, width=self.BAR_W, capstyle='round')
                x += self.BAR_PITCH
        except Exception:
            pass
