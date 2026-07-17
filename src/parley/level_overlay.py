# level_overlay.py
# Frameless always-on-top glass pill shown while recording/processing: a
# gradient mic orb that pulses with your voice, a scrolling waveform colored
# along the violet→blue→cyan accent family, and a status / streaming-text
# label that fits by measurement (never a fixed character clip). On Windows 11
# the glass is real acrylic blur-behind (theme.apply_pill_glass); elsewhere it
# falls back to plain translucency. Runs its own tk mainloop on a daemon
# thread; all public methods are thread-safe via root.after(). All colors and
# metrics come from theme.py; the waveform ramp and every glow blend are
# precomputed once — the per-frame draw cost is unchanged from the flat design.

import logging
import math
import threading
from collections import deque
from typing import Callable, Optional

from . import theme

logger = logging.getLogger(__name__)


class LevelOverlay:
    LEVEL_AMP = 25.0
    UPDATE_HZ = 30
    FLASH_MS = 400
    ERROR_HOLD_MS = 2400  # failures need reading time; tray balloons are unreliable
    SLIDE_PX = 14         # show/hide travel distance (design px)
    SLIDE_STEPS = 6       # × ~15 ms per step ≈ 90 ms of motion

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
        self._pal = pal
        self.BG = pal['bg']
        self.BAR = pal['wave_live']
        self.DIM = pal['wave_quiet']
        self.TEXT = pal['fg']
        self.TEXT_DIM = pal['fg_dim']
        self.ERROR = pal['error']

        # Physical-pixel metrics (theme.px handles monitor scale).
        self.WIDTH = theme.px(384)
        self.HEIGHT = theme.px(48)
        self.ORB_AREA_PX = theme.px(44)
        self.WAVE_AREA_PX = theme.px(150)
        self.BOTTOM_OFFSET_PX = theme.px(96)
        self.BAR_PITCH = theme.px(5)   # px between waveform bar centers
        self.BAR_W = theme.px(3)       # waveform bar width
        self.ORB_R = theme.px(13)      # mic orb radius

        self.root: Optional[object] = None
        self._orb_canvas = None
        self._level_canvas = None
        self._text_var = None
        self._text_label = None
        self._text_font = None      # tkfont.Font, created on the tk thread
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
        # Gradient comet trail: the newest bar carries the brightest family
        # stop, history fades toward the quiet color. Precomputed by theme so
        # _draw never blends colors per frame.
        self._ramp = theme.wave_ramp(self._wave.maxlen, pal)
        self._pulse_phase = 0.0
        # Room the streaming label actually has, in physical px — the fit
        # measurement target that replaces the old fixed 31-char clip.
        self._text_avail_px = (self.WIDTH - self.ORB_AREA_PX - self.WAVE_AREA_PX
                               - theme.px(10) - theme.px(14))
        # Final on-screen position (set by _position); slide animation offsets
        # from it and always lands back on it.
        self._fx = self._fy = 0
        # Pulse ring blends, precomputed like the ramp — never mix() per frame.
        self._pulse_colors = [(2, theme.mix(self.BG, pal['glow'], 0.10)),
                              (1, theme.mix(self.BG, pal['glow'], 0.18)),
                              (0, theme.mix(self.BG, pal['glow'], 0.30))]
        # Tracks pending after() callbacks so a new mode switch cancels the old
        # animation loop instead of stacking a second one (which would multiply
        # redraw/CPU load over many record cycles). Same discipline for the
        # typewriter chain and the flash hide-timer.
        self._update_after_id = None
        self._slide_after_id = None
        self._type_after_id = None
        self._flash_after_id = None
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

    # Called from the streaming/recognizer thread — all state mutation is
    # marshalled onto the tk thread so it can't race a typewriter tick.
    def set_streaming_text(self, text: str):
        new_text = (text or '').strip()
        self._call(lambda: self._apply_streaming_text(new_text))

    def _apply_streaming_text(self, new_text: str):
        if new_text == self._streaming_text:
            return
        if not new_text.startswith(self._streaming_text):
            self._displayed_chars = 0
        self._streaming_text = new_text
        # Reduced motion: no typewriter reveal — text lands whole.
        if self._reduced_motion:
            self._displayed_chars = len(new_text)
            self._refresh_text()
        else:
            self._cancel_typewriter()
            self._tick_typewriter()

    def _cancel_typewriter(self):
        if self._type_after_id is not None:
            try:
                self.root.after_cancel(self._type_after_id)
            except Exception:
                pass
            self._type_after_id = None

    # Fit text to the label's real pixel width: show the freshest tail with a
    # leading ellipsis when it overflows. Binary search on the font measure —
    # runs only when text changes, never per frame.
    def _fit_tail(self, text: str) -> str:
        if not self._text_font or not text:
            return text
        # Prefer the label's real mapped width over the build-time estimate —
        # off-by-a-few estimates clip the final characters.
        try:
            w = self._text_label.winfo_width()
            if w > 10:
                self._text_avail_px = w - theme.px(2)
        except Exception:
            pass
        if self._text_font.measure(text) <= self._text_avail_px:
            return text
        lo, hi = 1, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self._text_font.measure('…' + text[-mid:]) <= self._text_avail_px:
                lo = mid
            else:
                hi = mid - 1
        return '…' + text[-lo:]

    def _tick_typewriter(self):
        if not self._text_var or self._mode not in ('recording', 'processing'):
            return
        target = self._streaming_text
        if self._displayed_chars >= len(target):
            return
        self._displayed_chars = min(len(target), self._displayed_chars + 3)
        try:
            self._text_var.set(self._fit_tail(target[:self._displayed_chars]))
            self._text_label.configure(fg=self.TEXT)
        except Exception:
            return
        if self._displayed_chars < len(target):
            try:
                self._type_after_id = self.root.after(30, self._tick_typewriter)
            except Exception:
                self._type_after_id = None
        else:
            self._type_after_id = None

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
        prev = self._mode
        self._mode = mode
        self._cancel_slide()
        self._cancel_flash_hold()
        if mode == 'hidden':
            # Text is cleared in _withdraw (after the slide) so the pill
            # doesn't flip to 'Listening…' while sliding away.
            self._slide_out()
        else:
            try:
                self.root.deiconify()
            except Exception:
                pass
            if prev == 'hidden':
                self._slide_in()
            else:
                self._snap_to_final()
            self._smoothed = 0.0
            self._pulse_phase = 0.0
            self._wave.clear()
            if mode == 'recording':
                # A new dictation starts clean even when the previous flash
                # never passed through 'hidden'.
                self._cancel_typewriter()
                self._streaming_text = ''
                self._displayed_chars = 0
            # Cancel any in-flight loop before starting a fresh one.
            if self._update_after_id is not None:
                try:
                    self.root.after_cancel(self._update_after_id)
                except Exception:
                    pass
                self._update_after_id = None
            self._update_loop()
            self._refresh_text()

    # --- show/hide slide (the only animation not tied to the level loop) ----
    # The pill slides toward/away from its screen edge. Geometry moves the
    # whole window — acrylic included — so there is never a frosted ghost the
    # way an alpha fade would leave behind. Gated on reduced motion.

    def _slide_dir(self) -> int:
        return -1 if self.POSITIONS[self.position].startswith('T') else 1

    def _cancel_slide(self):
        if self._slide_after_id is not None:
            try:
                self.root.after_cancel(self._slide_after_id)
            except Exception:
                pass
            self._slide_after_id = None

    def _snap_to_final(self):
        try:
            self.root.geometry(f"+{self._fx}+{self._fy}")
        except Exception:
            pass

    def _slide_in(self):
        if self._reduced_motion:
            self._snap_to_final()
            return
        travel = theme.px(self.SLIDE_PX) * self._slide_dir()
        self._animate_slide(start_off=travel, end_off=0, step=0, on_done=None)

    def _slide_out(self):
        if self._reduced_motion or not self._is_visible():
            self._withdraw()
            return
        travel = theme.px(self.SLIDE_PX) * self._slide_dir()
        self._animate_slide(start_off=0, end_off=travel, step=0,
                            on_done=self._withdraw)

    def _is_visible(self) -> bool:
        try:
            return self.root.state() != 'withdrawn'
        except Exception:
            return False

    def _cancel_flash_hold(self):
        if self._flash_after_id is not None:
            try:
                self.root.after_cancel(self._flash_after_id)
            except Exception:
                pass
            self._flash_after_id = None

    def _withdraw(self):
        try:
            self.root.withdraw()
        except Exception:
            pass
        self._snap_to_final()
        self._cancel_typewriter()
        self._streaming_text = ''
        self._displayed_chars = 0
        self._refresh_text()

    def _animate_slide(self, start_off, end_off, step, on_done):
        # Ease-out cubic between the two offsets; ~15 ms cadence keeps the
        # motion smooth without competing with the 30 Hz level loop.
        if step > self.SLIDE_STEPS:
            if end_off == 0:
                self._snap_to_final()
            self._slide_after_id = None
            if on_done:
                on_done()
            return
        t = step / self.SLIDE_STEPS
        ease = 1 - (1 - t) ** 3
        off = round(start_off + (end_off - start_off) * ease)
        try:
            self.root.geometry(f"+{self._fx}+{self._fy + off}")
            self._slide_after_id = self.root.after(
                15, lambda: self._animate_slide(start_off, end_off, step + 1, on_done))
        except Exception:
            self._slide_after_id = None
            if on_done:
                on_done()

    def _refresh_text(self):
        if not self._text_var:
            return
        # Idle prompts sit quieter than live dictated words.
        if self._mode == 'processing':
            self._text_var.set('Transcribing…')
            self._text_label.configure(fg=self.TEXT_DIM)
        elif self._streaming_text:
            self._text_var.set(self._fit_tail(self._streaming_text))
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
            # A slide-out may be mid-flight (hide() just before this flash) —
            # cancel it or its deferred _withdraw would hide the flash.
            self._cancel_slide()
            self._cancel_flash_hold()
            if message and self._text_var:
                try:
                    self._text_var.set(self._fit_tail(message))
                    self._text_label.configure(fg=self.ERROR if color == self.ERROR else self.TEXT)
                except Exception:
                    pass
            try:
                self.root.deiconify()
            except Exception:
                pass
            self._snap_to_final()
            self._draw(1.0, override_color=color)
            # Hold a message flash longer so it's actually readable.
            hold = self.ERROR_HOLD_MS if message else self.FLASH_MS
            try:
                self._flash_after_id = self.root.after(
                    hold, lambda: self._set_mode('hidden'))
            except Exception:
                self._flash_after_id = None
        self._call(do)

    def _run(self):
        try:
            import tkinter as tk
            import tkinter.font as tkfont
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

            self._orb_canvas = tk.Canvas(outer, width=self.ORB_AREA_PX, height=self.HEIGHT,
                                         bg=self.BG, highlightthickness=0, borderwidth=0)
            self._orb_canvas.pack(side='left', padx=(theme.px(8), 0))
            self._draw_orb()

            self._level_canvas = tk.Canvas(outer, width=self.WAVE_AREA_PX, height=self.HEIGHT,
                                           bg=self.BG, highlightthickness=0, borderwidth=0)
            self._level_canvas.pack(side='left', padx=(theme.px(2), 0))

            self._text_var = tk.StringVar(value='')
            self._text_label = tk.Label(outer, textvariable=self._text_var,
                                        bg=self.BG, fg=self.TEXT_DIM,
                                        font=theme.font(9),
                                        anchor='w')
            self._text_label.pack(side='left', fill='x', expand=True,
                                  padx=(theme.px(8), theme.px(12)))
            self._text_font = tkfont.Font(font=theme.font(9))

            self._position()
            theme.apply_pill_glass(self.root)
            self._make_click_through()
            self._ready.set()
            self.root.mainloop()
        except Exception as e:
            logger.warning(f"Level overlay crashed: {e}")
            self._available = False
            self._ready.set()

    # The static mic orb (body + glyph) is drawn once; only the pulse ring
    # behind it is repainted by the level loop.
    def _draw_orb(self):
        c = self._orb_canvas
        if not c:
            return
        cx = self.ORB_AREA_PX / 2 + theme.px(2)
        cy = self.HEIGHT / 2
        theme.orb(c, cx, cy, self.ORB_R, self._pal, surface=self.BG,
                  halo=False, tags='orb')
        theme.mic_glyph(c, cx, cy, self.ORB_R * 0.95, tags='orb')

    # Voice-reactive glow behind the orb: 3 rings whose radius breathes with
    # the smoothed level. Cheap (3 ovals) and skipped entirely under reduced
    # motion or while a flash is showing.
    def _draw_pulse(self, level: float):
        c = self._orb_canvas
        if not c or self._reduced_motion:
            return
        c.delete('pulse')
        cx = self.ORB_AREA_PX / 2 + theme.px(2)
        cy = self.HEIGHT / 2
        base = self.ORB_R + theme.px(2)
        swell = theme.px(6) * min(1.0, level)
        for i, fill in self._pulse_colors:
            r = base + swell + theme.px(2) * i
            c.create_oval(cx - r, cy - r, cx + r, cy + r, width=0,
                          fill=fill, tags='pulse')
        c.tag_lower('pulse', 'orb')

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

            self._fx, self._fy = x, y
            self.root.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")
        except Exception as e:
            logger.debug(f"Position failed: {e}")

    def _make_click_through(self):
        if not self.click_through or not theme.IS_WIN:
            return
        try:
            import ctypes
            hwnd = theme._top_hwnd(self.root)
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
        self._draw_pulse(self._smoothed)

        try:
            self._update_after_id = self.root.after(int(1000 / self.UPDATE_HZ), self._update_loop)
        except Exception:
            self._update_after_id = None

    # Draws the scrolling waveform: newest level enters at the right edge and
    # carries the brightest gradient stop; history fades toward the quiet
    # color. Rounded line caps make each bar a soft pill rather than a hard
    # block. override_color (flash) paints a full static arch instead.
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
