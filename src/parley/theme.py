# theme.py
# Parley's single source of visual truth. Every Tk surface (overlay, settings,
# history, cheat sheet, dialogs) pulls its palette, fonts, spacing, radii, and
# window chrome from here — no window defines its own hex values. Also owns the
# platform calls Tk won't make itself: per-monitor DPI awareness, dark title
# bars, rounded corners, Mica/acrylic backdrops, the overlay's acrylic
# blur-behind, and the OS reduced-motion / light-mode signals.
#
# Design language: "midnight glass" — a deep navy scene, simulated-glass cards
# (opaque fills + hairline borders + canvas-painted radii), and one gradient
# accent family (violet → blue → cyan) for anything alive: the mic orb, the
# waveform, focus, primary actions. Green survives only as the 'ok' status dot.

import ctypes
import functools
import math
import sys
import tkinter as tk
import tkinter.font as tkfont

IS_WIN = sys.platform == 'win32'

# ---------------------------------------------------------------------------
# Palette — midnight glass. One gradient family everywhere.
# ---------------------------------------------------------------------------

DARK = {
    'bg':        '#0b1026',  # window canvas / overlay acrylic color-key
    'bg2':       '#16204a',  # scene-gradient end (backdrops, large canvases)
    'bg_raised': '#131b3e',  # glass cards, list rows
    'bg_input':  '#1a234c',  # entries, text areas, chip fills
    'bg_hover':  '#202a58',  # hover wash on rows/buttons
    'border':    '#2e3966',  # hairline — reads as white @ ~10% over navy
    'fg':        '#eaeefb',  # primary text (~92% white, cool cast)
    'fg_dim':    '#9aa3c9',  # secondary text (~60%)
    'fg_faint':  '#626b93',  # tertiary/hints (~40%) — smallest text only
    'accent':          '#5b6cfa',  # flat accent (violet-blue midpoint)
    'accent_hover':    '#7381ff',
    'accent_pressed':  '#4553d6',
    'on_accent': '#ffffff',
    'error':     '#ff5d73',
    'warn':      '#f5a95c',
    'info':      '#38c6f4',
    'wave_live':   '#38c6f4',  # brightest waveform stop (gradient does the rest)
    'wave_quiet':  '#252e56',  # waveform bars near silence
    'selection':   '#2d3d7a',  # text selection / listbox selection
    'grad_hi':   '#7b5cff',  # gradient start — violet (orb core)
    'grad_mid':  '#3d7bff',  # gradient middle — blue (orb edge)
    'grad_lo':   '#38c6f4',  # gradient end — cyan (waveform tip)
    'ok':        '#38e08c',  # green "listening / ready" dot (legacy ink's heir)
    'glow':      '#5c6cff',  # glow layering color (always blend toward surface)
}

LIGHT = {
    'bg':        '#eef1fa',
    'bg2':       '#dde4f6',
    'bg_raised': '#ffffff',
    'bg_input':  '#f3f5fc',
    'bg_hover':  '#e6ebf9',
    'border':    '#d3daf0',
    'fg':        '#191f38',
    'fg_dim':    '#4e5877',
    'fg_faint':  '#8c94b4',
    'accent':          '#4a5ae8',
    'accent_hover':    '#5b6cfa',
    'accent_pressed':  '#3947c4',
    'on_accent': '#ffffff',
    'error':     '#d92d4c',
    'warn':      '#b26d0d',
    'info':      '#0d7fc4',
    'wave_live':   '#2f66e6',
    'wave_quiet':  '#c9d2ec',
    'selection':   '#ccd8ff',
    'grad_hi':   '#6a4df0',
    'grad_mid':  '#2f66e6',
    'grad_lo':   '#0a9bd6',
    'ok':        '#0eaf68',
    'glow':      '#4a5ae8',
}

# ---------------------------------------------------------------------------
# OS signals (queried once, cached) — light mode, reduced motion, DPI.
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def system_prefers_light() -> bool:
    """Windows personalization setting; dark stays the default anywhere else.

    PARLEY_THEME=dark|light overrides the OS signal (useful for screenshots
    and for users who want the pill to contrast with their apps).
    """
    override = __import__('os').environ.get('PARLEY_THEME', '').lower()
    if override in ('dark', 'light'):
        return override == 'light'
    if not IS_WIN:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return bool(winreg.QueryValueEx(k, "AppsUseLightTheme")[0])
    except OSError:
        return False


@functools.lru_cache(maxsize=1)
def reduced_motion() -> bool:
    """Honour the OS 'show animations' accessibility switch (SPI 0x1042)."""
    if not IS_WIN:
        return False
    try:
        enabled = ctypes.c_int(1)
        SPI_GETCLIENTAREAANIMATION = 0x1042
        ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0)
        return not bool(enabled.value)
    except Exception:
        return False


def palette(light: bool = None) -> dict:
    return LIGHT if (system_prefers_light() if light is None else light) else DARK


# ---------------------------------------------------------------------------
# Color math — the alpha-simulation primitives. Tk has no per-widget alpha,
# so every "translucent" effect (glow, sheen, trails) is a precomputed opaque
# blend toward the surface it sits on. Blends happen at build time, never in
# a per-frame draw path.
# ---------------------------------------------------------------------------

def mix(a: str, b: str, t: float) -> str:
    """Blend two #rrggbb colors; t=0 → a, t=1 → b."""
    av = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    bv = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return '#%02x%02x%02x' % tuple(round(x + (y - x) * t) for x, y in zip(av, bv))


def accent_gradient(t: float, pal: dict = None) -> str:
    """Sample the violet → blue → cyan family at t in [0,1]."""
    pal = pal or palette()
    t = min(1.0, max(0.0, t))
    if t < 0.5:
        return mix(pal['grad_hi'], pal['grad_mid'], t * 2)
    return mix(pal['grad_mid'], pal['grad_lo'], (t - 0.5) * 2)


def wave_ramp(n: int, pal: dict = None) -> list:
    """Per-bar colors for a scrolling waveform: the newest (rightmost) bar is
    the brightest gradient stop; history fades back toward the quiet color.
    Precompute once — indexing this list is the only per-frame cost."""
    pal = pal or palette()
    if n <= 1:
        return [pal['wave_live']] * max(1, n)
    return [mix(pal['wave_quiet'], accent_gradient(i / (n - 1), pal),
                0.35 + 0.65 * i / (n - 1))
            for i in range(n)]


# ---------------------------------------------------------------------------
# DPI — call ensure_dpi_aware() BEFORE creating any Tk root. px() scales
# design pixels to physical pixels so layouts stay the same physical size.
# ---------------------------------------------------------------------------

_dpi_ready = False


def ensure_dpi_aware() -> None:
    """Opt the process into per-monitor DPI awareness (best effort, idempotent).

    Without this, Windows bitmap-stretches every Tk window on scaled displays
    and text goes soft. Must run before the first HWND is created.
    """
    global _dpi_ready
    if _dpi_ready or not IS_WIN:
        _dpi_ready = True
        return
    try:
        # -4 = DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 (Win10 1703+)
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
    _dpi_ready = True


@functools.lru_cache(maxsize=1)
def dpi_scale() -> float:
    """Physical-pixels-per-design-pixel for the primary monitor (1.0, 1.5, ...)."""
    if not IS_WIN:
        return 1.0
    try:
        return ctypes.windll.user32.GetDpiForSystem() / 96.0
    except Exception:
        return 1.0


def px(n: float) -> int:
    """Scale a design-pixel measure to physical pixels (min 1)."""
    return max(1, round(n * dpi_scale()))


# ---------------------------------------------------------------------------
# Typography — Segoe UI Variable on Win11, Segoe UI otherwise; Cascadia mono.
# Display text (timers, hero titles) prefers the light Display cut so large
# numerals read airy instead of heavy. Sizes are points: Tk maps points
# through the DPI scale once `tk scaling` is set by init_window().
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _family_ui() -> str:
    try:
        fams = set(tkfont.families())
    except Exception:
        return 'Segoe UI'
    return 'Segoe UI Variable Text' if 'Segoe UI Variable Text' in fams else 'Segoe UI'


@functools.lru_cache(maxsize=1)
def _family_display() -> str:
    try:
        fams = set(tkfont.families())
    except Exception:
        return 'Segoe UI'
    for fam in ('Segoe UI Variable Display Light', 'Segoe UI Variable Display',
                'Segoe UI Light'):
        if fam in fams:
            return fam
    return _family_ui()


@functools.lru_cache(maxsize=1)
def _family_mono() -> str:
    try:
        fams = set(tkfont.families())
    except Exception:
        return 'Consolas'
    return 'Cascadia Mono' if 'Cascadia Mono' in fams else 'Consolas'


def font(size: int = 10, weight: str = 'normal', mono: bool = False, italic: bool = False):
    fam = _family_mono() if mono else _family_ui()
    opts = [fam, size]
    if weight == 'bold':
        opts.append('bold')
    if italic:
        opts.append('italic')
    return tuple(opts)


def font_display(size: int = 22):
    """Large light-weight display type for timers and hero titles."""
    return (_family_display(), size)


# Spacing scale (design px) — use with px(): px(GAP_M) etc.
GAP_XS, GAP_S, GAP_M, GAP_L, GAP_XL = 4, 8, 12, 16, 24

# Corner radii (design px). WINDOW applies only to frameless color-key windows
# (the overlay pill); titled windows get standard DWM rounding from the OS.
RADIUS_WINDOW = 24
RADIUS_CARD = 16
RADIUS_CTRL = 10
RADIUS_CHIP = 6


# ---------------------------------------------------------------------------
# Window chrome — dark title bar, rounded corners, Mica/acrylic backdrops,
# and the overlay's acrylic blur-behind. All DWM/ctypes calls live HERE.
# ---------------------------------------------------------------------------

def _dwm_set(hwnd: int, attr: int, value: int) -> None:
    v = ctypes.c_int(value)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))


def _colorref(hex_color: str) -> int:
    """#rrggbb → COLORREF (0x00BBGGRR) for DWM caption attributes."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return (b << 16) | (g << 8) | r


def _top_hwnd(root: tk.Misc) -> int:
    """The top-level *wrapper* hwnd. Tk's winfo_id() returns the client child;
    win32 styling (DWM, layered styles) must target the wrapper."""
    child = root.winfo_id()
    return ctypes.windll.user32.GetParent(child) or child


# DWMWA_SYSTEMBACKDROP_TYPE (attr 38) values, Win11 22H2+.
_BACKDROPS = {'mica': 2, 'acrylic': 3, 'tabbed': 4}


def apply_chrome(root: tk.Misc, dark: bool = None, backdrop: str = None) -> None:
    """Blend the OS title bar into the theme: dark mode, rounded corners,
    caption painted the canvas color, and (optionally) a Mica/acrylic
    backdrop. Every call is best-effort — Win10 silently keeps flat dark."""
    if not IS_WIN:
        return
    use_dark = (not system_prefers_light()) if dark is None else dark
    pal = palette(light=not use_dark)
    # Caption attributes set before the native window is truly shown don't
    # stick (verified: same values re-applied ~100 ms after map paint fine).
    # On first map, schedule ONE delayed re-apply. Idempotent otherwise.
    if not getattr(root, '_parley_chrome_hooked', False):
        def _reapply(_event=None):
            if getattr(root, '_parley_chrome_reapplied', False):
                return
            root._parley_chrome_reapplied = True
            try:
                root.after(120, lambda: apply_chrome(root, dark, backdrop))
            except Exception:
                pass
        try:
            root._parley_chrome_hooked = True
            root.bind('<Map>', _reapply, add='+')
        except Exception:
            pass
    try:
        root.update_idletasks()
        hwnd = _top_hwnd(root)
        _dwm_set(hwnd, 20, 1 if use_dark else 0)   # DWMWA_USE_IMMERSIVE_DARK_MODE
        _dwm_set(hwnd, 33, 2)                      # DWMWA_WINDOW_CORNER_PREFERENCE = ROUND
        # Caption color/text (Win11): title bar becomes part of the scene
        # instead of a stock grey strip.
        try:
            _dwm_set(hwnd, 35, _colorref(pal['bg']))   # DWMWA_CAPTION_COLOR
            _dwm_set(hwnd, 36, _colorref(pal['fg']))   # DWMWA_TEXT_COLOR
        except Exception:
            pass
        if backdrop in _BACKDROPS:
            try:
                _dwm_set(hwnd, 38, _BACKDROPS[backdrop])  # DWMWA_SYSTEMBACKDROP_TYPE
            except Exception:
                pass
    except Exception:
        pass


def init_window(root: tk.Tk, title: str = 'Parley', backdrop: str = None) -> dict:
    """Standard setup for every Parley window; returns the active palette.

    Sets Tk's point→pixel scaling to the real DPI, applies the canvas color,
    title, and native chrome. Call ensure_dpi_aware() before Tk() itself.
    """
    pal = palette()
    try:
        root.tk.call('tk', 'scaling', dpi_scale() * 96.0 / 72.0)
    except Exception:
        pass
    root.title(title)
    root.configure(bg=pal['bg'])
    # Brand the title bar / taskbar instead of Tk's default feather.
    if IS_WIN:
        icon = __import__('os').path.join(
            __import__('os').path.dirname(__file__),
            'platform', 'windows', 'assets', 'parley-icon.ico')
        try:
            root.iconbitmap(default=icon)
        except Exception:
            pass
    apply_chrome(root, backdrop=backdrop)
    return pal


# Overlay acrylic tint derived from the canvas color (AABBGGRR for DWM).
def acrylic_tint(alpha: int = 0xB3) -> int:
    pal = palette()
    r, g, b = (int(pal['bg'][i:i + 2], 16) for i in (1, 3, 5))
    return (alpha << 24) | (b << 16) | (g << 8) | r


def apply_pill_glass(root: tk.Misc, fallback_alpha: float = 0.93) -> bool:
    """Real glass for frameless pills (the level overlay): Win11 acrylic
    blur-behind + rounded corners. The palette 'bg' color is declared as the
    layered transparent color, so every bg pixel becomes tinted blur while
    bars/text stay solid. Returns True when acrylic engaged.

    If acrylic isn't available the window keeps an opaque bg with plain alpha
    translucency — never punch color-key holes without a blur backdrop.
    """
    if not IS_WIN:
        try:
            root.attributes('-alpha', fallback_alpha)
        except Exception:
            pass
        return False
    try:
        user32 = ctypes.windll.user32
        root.update_idletasks()
        hwnd = _top_hwnd(root)

        try:
            _dwm_set(hwnd, 33, 2)  # DWMWCP_ROUND (harmless no-op on Win10)
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

        accent = ACCENTPOLICY(4, 2, acrylic_tint(), 0)  # ACCENT_ENABLE_ACRYLICBLURBEHIND
        data = WCADATA(19,  # WCA_ACCENT_POLICY
                       ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p),
                       ctypes.sizeof(accent))
        ok = user32.SetWindowCompositionAttribute(ctypes.c_void_p(hwnd), ctypes.byref(data))
        if ok:
            root.attributes('-transparentcolor', palette()['bg'])
            return True
        root.attributes('-alpha', fallback_alpha)
        return False
    except Exception:
        try:
            root.attributes('-alpha', fallback_alpha)
        except Exception:
            pass
        return False


# ---------------------------------------------------------------------------
# Canvas primitives — rounded rects, gradients, glows, the mic orb. These are
# the building blocks every surface (and the widget kit below) draws with.
# All take PHYSICAL pixel coordinates; callers scale with px() first.
# ---------------------------------------------------------------------------

def rounded_rect(canvas: tk.Canvas, x0, y0, x1, y1, r, **kw):
    """Rounded rectangle as a smoothed polygon. Tk bezier-smooths the doubled
    corner points into arcs — not pixel-perfect circles, but clean at UI radii."""
    r = min(r, (x1 - x0) / 2, (y1 - y0) / 2)
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
           x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return canvas.create_polygon(pts, smooth=True, **kw)


def vgradient(canvas: tk.Canvas, x0, y0, x1, y1, c0, c1, steps: int = None, tags=None):
    """Vertical gradient fill painted as horizontal strips (build-time only —
    never call per frame)."""
    h = max(1, int(y1 - y0))
    steps = steps or max(2, h // 2)
    band = h / steps
    for i in range(steps):
        canvas.create_rectangle(x0, y0 + i * band, x1, y0 + (i + 1) * band + 1,
                                width=0, fill=mix(c0, c1, i / max(1, steps - 1)),
                                tags=tags)


def glow(canvas: tk.Canvas, cx, cy, r, color, surface, rings: int = 10,
         strength: float = 0.16, spread: float = None, tags=None):
    """Soft radial glow: concentric opaque rings blending color→surface,
    faintest outermost. Draw BEFORE whatever sits at the center."""
    spread = spread if spread is not None else r * 1.2
    for i in range(rings, 0, -1):
        rr = r + spread * i / rings
        a = strength * (1 - i / (rings + 1)) ** 2
        canvas.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, width=0,
                           fill=mix(surface, color, a), tags=tags)


def orb(canvas: tk.Canvas, cx, cy, r, pal: dict = None, surface: str = None,
        halo: bool = True, ring: bool = False, tags=None):
    """The signature mic orb: radial violet-core/blue-edge body with an
    optional glow halo and concentric dotted ring."""
    pal = pal or palette()
    surface = surface or pal['bg']
    if halo:
        glow(canvas, cx, cy, r, pal['glow'], surface, tags=tags)
    core = mix(pal['grad_hi'], '#ffffff', 0.22)
    step = max(1, int(r / 26))
    for k in range(int(r), 0, -step):
        t = 1 - k / r
        canvas.create_oval(cx - k, cy - k, cx + k, cy + k, width=0,
                           fill=mix(pal['grad_mid'], core, t), tags=tags)
    if ring:
        ring_r = r * 1.3
        dot = max(1.0, r / 40)
        for a_deg in range(0, 360, 9):
            ax = cx + ring_r * math.cos(math.radians(a_deg))
            ay = cy + ring_r * math.sin(math.radians(a_deg))
            canvas.create_oval(ax - dot, ay - dot, ax + dot, ay + dot,
                               width=0, fill=mix(surface, pal['accent'], 0.55), tags=tags)


def mic_glyph(canvas: tk.Canvas, cx, cy, r, color: str = '#ffffff', tags=None):
    """Microphone glyph (capsule + cradle + stem) sized to an orb of radius r."""
    w = max(2, r * 0.30)
    lw = max(1, int(r * 0.11))
    rounded_rect(canvas, cx - w, cy - r * 0.62, cx + w, cy + r * 0.10, w,
                 fill=color, width=0, tags=tags)
    canvas.create_arc(cx - r * 0.52, cy - r * 0.42, cx + r * 0.52, cy + r * 0.42,
                      style='arc', start=180, extent=180, outline=color,
                      width=lw, tags=tags)
    canvas.create_line(cx, cy + r * 0.42, cx, cy + r * 0.66, fill=color,
                       width=lw, tags=tags)
    canvas.create_line(cx - r * 0.28, cy + r * 0.66, cx + r * 0.28, cy + r * 0.66,
                       fill=color, width=lw, capstyle='round', tags=tags)


def _parent_bg(parent, pal) -> str:
    """The surface color a canvas widget sits on (its corners must match).
    Non-hex values (Tk system color names on an untheme d parent) fall back
    to the canvas color — mix() can only blend #rrggbb."""
    try:
        bg = parent.cget('background')
        if isinstance(bg, str) and len(bg) == 7 and bg.startswith('#'):
            return bg
        return pal['bg']
    except Exception:
        return pal['bg']


# ---------------------------------------------------------------------------
# Widget kit — rounded, hover-aware, keyboard-focusable controls drawn on
# small canvases so every surface shares one geometry language. Each widget
# repaints only on state change (hover/focus/press), never on a timer.
# ---------------------------------------------------------------------------

class _RoundControl(tk.Canvas):
    """Shared machinery: a canvas that repaints a rounded pill/box + label on
    state changes and behaves like a button for mouse and keyboard."""

    def __init__(self, parent, pal, w, h, radius, command=None, focusable=True):
        self._pal = pal
        self._surface = _parent_bg(parent, pal)
        super().__init__(parent, width=w, height=h, bg=self._surface,
                         highlightthickness=0, bd=0, cursor='hand2' if command else '')
        self._command = command
        self._hover = False
        self._pressed = False
        self._focused = False
        if command:
            self.bind('<Enter>', lambda e: self._set(hover=True))
            self.bind('<Leave>', lambda e: self._set(hover=False, pressed=False))
            self.bind('<ButtonPress-1>', lambda e: self._set(pressed=True))
            self.bind('<ButtonRelease-1>', self._click)
            if focusable:
                self.configure(takefocus=1)
                self.bind('<FocusIn>', lambda e: self._set(focused=True))
                self.bind('<FocusOut>', lambda e: self._set(focused=False))
                # 'break' stops bindtag propagation so a toplevel-level
                # <Return> binding (dialogs, add-word) can't double-fire.
                self.bind('<Return>', lambda e: (self._activate(), 'break')[1])
                self.bind('<space>', lambda e: (self._activate(), 'break')[1])

    def _set(self, **flags):
        for k, v in flags.items():
            setattr(self, '_' + k, v)
        self._redraw()

    def _click(self, event):
        inside = 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
        self._set(pressed=False)
        if inside:
            self._activate()

    def _activate(self):
        if self._command:
            self._command()

    def _redraw(self):
        raise NotImplementedError


class _PillButton(_RoundControl):
    def __init__(self, parent, text, command, pal, primary=False, danger=False,
                 width=None):
        self._text = text
        self._primary = primary
        self._danger = danger
        self._font = font(10, 'bold' if primary else 'normal')
        h = px(32)
        w = self._measure(width)
        super().__init__(parent, pal, w, h, h / 2, command=command)
        self._redraw()

    def _measure(self, width_chars):
        f = tkfont.Font(font=self._font)
        w = f.measure(self._text) + px(32)
        if width_chars:
            w = max(w, width_chars * f.measure('0') + px(32))
        return w

    # Callers restyle buttons mid-flight (e.g. Copy → "✓ Copied"); accept the
    # tk.Button options they use and repaint, delegating real Canvas options.
    def configure(self, cnf=None, **kw):
        text = kw.pop('text', None)
        kw.pop('state', None)
        if text is not None and text != self._text:
            self._text = text
            super().configure(width=self._measure(None))
        if kw or cnf:
            super().configure(cnf, **kw) if cnf else super().configure(**kw)
        self._redraw()
    config = configure

    def _redraw(self):
        pal = self._pal
        self.delete('all')
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        r = h / 2
        if self._primary:
            fill = pal['accent_pressed'] if self._pressed else (
                pal['accent_hover'] if self._hover else pal['accent'])
            fg = pal['on_accent']
            # Soft glow rim lifts the primary action off the glass.
            rounded_rect(self, px(1), px(1), w - px(1), h - px(1), r,
                         fill=mix(self._surface, pal['glow'], 0.35), width=0)
            rounded_rect(self, px(3), px(3), w - px(3), h - px(3), r - px(2),
                         fill=fill, width=0)
        else:
            fill = pal['bg_hover'] if (self._hover or self._pressed) else pal['bg_input']
            fg = pal['error'] if self._danger else pal['fg']
            outline = pal['accent'] if self._focused else pal['border']
            rounded_rect(self, px(1), px(1), w - px(1), h - px(1), r,
                         fill=fill, outline=outline, width=px(1))
        if self._primary and self._focused:
            rounded_rect(self, px(1), px(1), w - px(1), h - px(1), r,
                         fill='', outline=pal['on_accent'], width=px(1))
        self.create_text(w / 2, h / 2, text=self._text, font=self._font, fill=fg)


def button(parent, text, command, pal=None, primary=False, danger=False, width=None):
    """Rounded pill button with hover/press/focus states in theme colors."""
    pal = pal or palette()
    return _PillButton(parent, text, command, pal, primary, danger, width)


class _Checkbox(_RoundControl):
    """The one canonical checkbox: rounded accent box + check, label beside it.
    Toggles a tk.BooleanVar; keyboard-accessible."""

    def __init__(self, parent, text, variable, pal):
        self._text = text
        self._var = variable
        self._font = font(9)
        f = tkfont.Font(font=self._font)
        self._box = px(16)
        w = self._box + px(10) + f.measure(text) + px(4)
        h = max(self._box + px(4), f.metrics('linespace') + px(4))
        super().__init__(parent, pal, w, h, 0, command=self._toggle)
        try:
            self._var.trace_add('write', lambda *_: self._redraw())
        except Exception:
            pass
        self._redraw()

    def _toggle(self):
        self._var.set(not self._var.get())

    def _redraw(self):
        pal = self._pal
        self.delete('all')
        b = self._box
        h = self.winfo_reqheight()
        y0 = (h - b) / 2
        r = px(5)
        checked = False
        try:
            checked = bool(self._var.get())
        except Exception:
            pass
        if checked:
            fill = pal['accent_hover'] if self._hover else pal['accent']
            rounded_rect(self, px(1), y0, b + px(1), y0 + b, r, fill=fill, width=0)
            self.create_line(px(1) + b * 0.24, y0 + b * 0.52,
                             px(1) + b * 0.44, y0 + b * 0.72,
                             px(1) + b * 0.78, y0 + b * 0.28,
                             fill=pal['on_accent'], width=max(2, px(2)),
                             capstyle='round', joinstyle='round')
        else:
            fill = pal['bg_hover'] if self._hover else pal['bg_input']
            rounded_rect(self, px(1), y0, b + px(1), y0 + b, r, fill=fill,
                         outline=pal['accent'] if self._focused else pal['border'],
                         width=px(1))
        self.create_text(b + px(10), h / 2, text=self._text, font=self._font,
                         fill=pal['fg'], anchor='w')


def checkbox(parent, text, variable, pal=None):
    """Canonical themed checkbox bound to a tk.BooleanVar."""
    pal = pal or palette()
    return _Checkbox(parent, text, variable, pal)


class _Scrollbar(tk.Canvas):
    """Slim rounded scrollbar implementing Tk's set()/command protocol, so it
    drops in wherever tk.Scrollbar went: sb = scrollbar(parent, widget.yview);
    widget.configure(yscrollcommand=sb.set)."""

    def __init__(self, parent, command, pal):
        self._pal = pal
        self._surface = _parent_bg(parent, pal)
        super().__init__(parent, width=px(10), bg=self._surface,
                         highlightthickness=0, bd=0)
        self._command = command
        self._first, self._last = 0.0, 1.0
        self._hover = False
        self._drag_off = None
        self.bind('<Configure>', lambda e: self._redraw())
        self.bind('<Enter>', lambda e: self._set_hover(True))
        self.bind('<Leave>', lambda e: self._set_hover(False))
        self.bind('<ButtonPress-1>', self._press)
        self.bind('<B1-Motion>', self._drag)
        self.bind('<ButtonRelease-1>', lambda e: setattr(self, '_drag_off', None))

    def set(self, first, last):
        self._first, self._last = float(first), float(last)
        self._redraw()

    def _set_hover(self, on):
        self._hover = on
        self._redraw()

    def _thumb_geom(self):
        h = self.winfo_height()
        span = max(self._last - self._first, 0.0)
        th = max(px(24), span * h)
        ty = self._first * h
        ty = min(ty, h - th)
        return ty, th

    def _press(self, event):
        ty, th = self._thumb_geom()
        if ty <= event.y <= ty + th:
            self._drag_off = event.y - ty
        else:
            self._drag_off = th / 2
            self._moveto(event.y - self._drag_off)

    def _drag(self, event):
        if self._drag_off is not None:
            self._moveto(event.y - self._drag_off)

    def _moveto(self, thumb_top):
        # Map thumb travel (0..h-th) onto first (0..1-span) so the tail stays
        # reachable even when the thumb is clamped to its minimum height.
        h = max(1, self.winfo_height())
        _, th = self._thumb_geom()
        span = max(self._last - self._first, 0.0)
        travel = max(1.0, h - th)
        frac = max(0.0, min(1.0, thumb_top / travel)) * max(0.0, 1.0 - span)
        if self._command:
            self._command('moveto', frac)

    def _redraw(self):
        self.delete('all')
        if self._last - self._first >= 1.0:
            return  # content fits — stay invisible, keep layout stable
        w = self.winfo_width()
        h = self.winfo_height()
        pal = self._pal
        rounded_rect(self, px(2), 0, w - px(2), h, (w - px(4)) / 2,
                     fill=mix(self._surface, pal['border'], 0.45), width=0)
        ty, th = self._thumb_geom()
        color = mix(pal['border'], pal['fg_faint'], 0.8 if self._hover else 0.5)
        rounded_rect(self, px(2), ty, w - px(2), ty + th, (w - px(4)) / 2,
                     fill=color, width=0)


def scrollbar(parent, command, pal=None):
    """Slim rounded vertical scrollbar (Tk set/command protocol)."""
    pal = pal or palette()
    return _Scrollbar(parent, command, pal)


class _Card(tk.Canvas):
    """Simulated glass card: rounded raised fill, hairline border, subtle top
    sheen. Pack children into .inner (a plain Frame on the raised surface)."""

    PAD = 6  # design px between the rounded edge and .inner (keeps .inner's
             # square corners safely inside the arc: needs > 0.3 × radius)

    def __init__(self, parent, pal, radius=RADIUS_CARD):
        self._pal = pal
        self._r = px(radius)
        self._pad = px(self.PAD)
        self._surface = _parent_bg(parent, pal)
        self._req = (0, 0)
        super().__init__(parent, bg=self._surface, highlightthickness=0, bd=0)
        self.inner = tk.Frame(self, bg=pal['bg_raised'])
        self._win = self.create_window(self._pad, self._pad, window=self.inner,
                                       anchor='nw')
        self.inner.bind('<Configure>', self._sync_request)
        self.bind('<Configure>', self._redraw)

    # Canvas requests exactly the space .inner wants (plus padding), so cards
    # size to content like a Frame would.
    def _sync_request(self, _event=None):
        req = (self.inner.winfo_reqwidth() + 2 * self._pad,
               self.inner.winfo_reqheight() + 2 * self._pad)
        if req != self._req:
            self._req = req
            self.configure(width=req[0], height=req[1])

    def _redraw(self, event):
        w, h = event.width, event.height
        self.itemconfigure(self._win, width=max(1, w - 2 * self._pad),
                           height=max(1, h - 2 * self._pad))
        self.delete('deco')
        pal = self._pal
        rounded_rect(self, px(1), px(1), w - px(1), h - px(1), self._r,
                     fill=pal['bg_raised'], outline=pal['border'], width=px(1),
                     tags='deco')
        # Top sheen — the light edge that sells "glass" (invisible on white).
        self.create_line(px(1) + self._r, px(2), w - px(1) - self._r, px(2),
                         fill=mix(pal['bg_raised'], '#ffffff', 0.10),
                         width=px(1), tags='deco')
        self.tag_lower('deco')


def card(parent, pal=None, radius=RADIUS_CARD, **kw):
    """Rounded glass card for grouping content; children go in .inner."""
    pal = pal or palette()
    return _Card(parent, pal, radius=radius)


class _KeyChip(_RoundControl):
    """Keycap-styled chip in mono type, with a shaded base for physical depth."""

    def __init__(self, parent, text, pal, fg=None):
        self._text = text
        self._fg = fg or pal['fg']
        self._font = font(9, 'bold', mono=True)
        f = tkfont.Font(font=self._font)
        w = f.measure(text) + px(18)
        h = px(22)
        super().__init__(parent, pal, w + px(2), h + px(3), px(RADIUS_CHIP))
        self._redraw()

    # cheat_sheet mutes unbound keys via configure(fg=...); accept it.
    def configure(self, cnf=None, **kw):
        fg = kw.pop('fg', None)
        text = kw.pop('text', None)
        if fg is not None:
            self._fg = fg
        if text is not None:
            self._text = text
            f = tkfont.Font(font=self._font)
            super().configure(width=f.measure(text) + px(20))
        if kw or cnf:
            super().configure(cnf, **kw) if cnf else super().configure(**kw)
        self._redraw()
    config = configure

    def _redraw(self):
        pal = self._pal
        self.delete('all')
        w = self.winfo_reqwidth() - px(2)
        h = self.winfo_reqheight() - px(3)
        r = px(RADIUS_CHIP)
        rounded_rect(self, px(1), px(3), w + px(1), h + px(3), r,
                     fill=mix(pal['bg_input'], '#000000', 0.4), width=0)
        rounded_rect(self, px(1), px(1), w + px(1), h + px(1), r,
                     fill=pal['bg_input'], outline=pal['border'], width=px(1))
        self.create_text((w + px(2)) / 2, (h + px(2)) / 2, text=self._text,
                         font=self._font, fill=self._fg)


def key_chip(parent, text, pal=None):
    """Keyboard-key chip (cheat sheet, first-run, hints) in mono type."""
    pal = pal or palette()
    return _KeyChip(parent, text, pal)


class _Chip(tk.Canvas):
    """Static glass pill with an optional status dot — status lines, badges."""

    def __init__(self, parent, text, pal, dot=None, mono=False):
        self._pal = pal
        self._surface = _parent_bg(parent, pal)
        self._font = font(9, mono=mono)
        f = tkfont.Font(font=self._font)
        self._text, self._dot = text, dot
        dot_w = px(14) if dot else 0
        w = f.measure(text) + px(24) + dot_w
        h = px(24)
        super().__init__(parent, width=w, height=h, bg=self._surface,
                         highlightthickness=0, bd=0)
        self._redraw()

    def _redraw(self):
        pal = self._pal
        self.delete('all')
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        rounded_rect(self, px(1), px(1), w - px(1), h - px(1), h / 2,
                     fill=pal['bg_input'], outline=pal['border'], width=px(1))
        x = px(12)
        if self._dot:
            self.create_oval(x, h / 2 - px(3.5), x + px(7), h / 2 + px(3.5),
                             fill=self._dot, width=0)
            x += px(14)
        self.create_text(x, h / 2, text=self._text, font=self._font,
                         fill=pal['fg_dim'], anchor='w')


def chip(parent, text, pal=None, dot=None, mono=False):
    """Glass pill with optional colored status dot."""
    pal = pal or palette()
    return _Chip(parent, text, pal, dot=dot, mono=mono)


# One consistent "restart Parley to apply" affordance across all surfaces.
RESTART_NOTE = 'Restart Parley to apply'


def restart_chip(parent, pal=None, text: str = RESTART_NOTE):
    pal = pal or palette()
    return chip(parent, text, pal, dot=pal['warn'])


# Themed modal dialog — replaces tkinter.messagebox so alerts stop breaking
# the design language. Blocks until dismissed; returns the clicked button's
# label, or None if closed with Esc / the title-bar ✕.
def dialog(parent, title, message, buttons=('OK',), default=0, danger=False,
           tone=None, chip_text=None, pal=None):
    """tone: 'error'|'warn'|'info'|'ok' draws a status dot beside the message.
    danger: the default button uses the danger treatment (destructive confirm).
    chip_text: optional pill under the message (e.g. theme.RESTART_NOTE)."""
    pal = pal or palette()
    win = tk.Toplevel(parent)
    win.withdraw()
    win.title(title)
    win.configure(bg=pal['bg'])
    win.transient(parent)
    win.resizable(False, False)

    body = tk.Frame(win, bg=pal['bg'])
    body.pack(fill='both', expand=True, padx=px(GAP_L), pady=px(GAP_L))

    msg_row = tk.Frame(body, bg=pal['bg'])
    msg_row.pack(fill='x')
    if tone in ('error', 'warn', 'info', 'ok'):
        dot = tk.Canvas(msg_row, width=px(10), height=px(10), bg=pal['bg'],
                        highlightthickness=0, bd=0)
        dot.create_oval(px(1), px(1), px(9), px(9), fill=pal[tone], width=0)
        dot.pack(side='left', anchor='n', pady=px(4), padx=(0, px(GAP_S)))
    tk.Label(msg_row, text=message, bg=pal['bg'], fg=pal['fg'],
             font=font(10), anchor='w', justify='left',
             wraplength=px(340)).pack(side='left', fill='x', expand=True)

    if chip_text:
        chip(body, chip_text, pal, dot=pal['warn']).pack(anchor='w',
                                                         pady=(px(GAP_S), 0))

    result = {'value': None, 'chosen': False}

    # First selection wins: a Return press on a focused button fires both the
    # button's own binding and the toplevel's — the second call must no-op.
    def choose(label):
        if result['chosen']:
            return
        result['chosen'] = True
        result['value'] = label
        try:
            win.destroy()
        except Exception:
            pass

    btn_row = tk.Frame(body, bg=pal['bg'])
    btn_row.pack(fill='x', pady=(px(GAP_L), 0))
    for i, label in reversed(list(enumerate(buttons))):
        is_default = (i == default)
        b = button(btn_row, label, lambda l=label: choose(l), pal,
                   primary=is_default and not danger,
                   danger=is_default and danger)
        b.pack(side='right', padx=(px(GAP_S), 0))
        if is_default:
            b.focus_set()

    win.bind('<Escape>', lambda e: choose(None))
    win.bind('<Return>', lambda e: choose(buttons[default]))
    win.protocol('WM_DELETE_WINDOW', lambda: choose(None))

    # Center over the parent window, then show.
    win.update_idletasks()
    w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    try:
        x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - h) // 2
    except Exception:
        x, y = px(200), px(200)
    win.geometry(f"+{max(0, x)}+{max(0, y)}")
    win.deiconify()
    apply_chrome(win)
    try:
        win.grab_set()
    except Exception:
        pass
    parent.wait_window(win)
    return result['value']


def entry(parent, textvariable=None, pal=None, mono=False, **kw):
    """Borderless entry on an input surface with an accent focus ring."""
    pal = pal or palette()
    e = tk.Entry(
        parent, textvariable=textvariable,
        bg=pal['bg_input'], fg=pal['fg'],
        insertbackground=pal['fg'],
        selectbackground=pal['selection'], selectforeground=pal['fg'],
        relief='flat', bd=0, font=font(10, mono=mono),
        highlightthickness=px(1),
        highlightbackground=pal['border'], highlightcolor=pal['accent'],
        **kw,
    )
    return e


def heading(parent, text, pal=None, size=14):
    pal = pal or palette()
    return tk.Label(parent, text=text, font=font(size, 'bold'),
                    bg=_parent_bg(parent, pal), fg=pal['fg'], anchor='w')


def caption(parent, text, pal=None, wrap=None, bg=None):
    pal = pal or palette()
    kw = {'wraplength': px(wrap)} if wrap else {}
    return tk.Label(parent, text=text, font=font(9),
                    bg=bg or _parent_bg(parent, pal), fg=pal['fg_dim'], anchor='w',
                    justify='left', **kw)
