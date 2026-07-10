# theme.py
# Parley's single source of visual truth. Every Tk surface (overlay, settings,
# history, cheat sheet, dialogs) pulls its palette, fonts, spacing, and window
# chrome from here — no window defines its own hex values. Also owns the
# platform calls Tk won't make itself: per-monitor DPI awareness, dark title
# bars, rounded corners, and the OS reduced-motion / light-mode signals.

import ctypes
import functools
import sys
import tkinter as tk
import tkinter.font as tkfont

IS_WIN = sys.platform == 'win32'

# ---------------------------------------------------------------------------
# Palette — "green ink on dark paper". One accent everywhere.
# ---------------------------------------------------------------------------

DARK = {
    'bg':        '#0e1412',  # window canvas (near-black, green undertone)
    'bg_raised': '#161d1a',  # cards, list rows
    'bg_input':  '#1b2420',  # entries, text areas
    'bg_hover':  '#1e2823',  # hover wash on rows/buttons
    'border':    '#2a342f',  # hairline separators
    'fg':        '#e8edea',  # primary text (paper white)
    'fg_dim':    '#9ba8a1',  # secondary text
    'fg_faint':  '#66716b',  # tertiary/hints — smallest text only
    'accent':          '#4cc38a',  # Parley green ink
    'accent_hover':    '#5ad69b',
    'accent_pressed':  '#3fae7a',
    'on_accent': '#0a0f0d',  # text on accent fills (near-black, 8.4:1)
    'error':     '#f2555a',
    'warn':      '#e5a458',
    'info':      '#6cb6ff',
    'wave_live':   '#4cc38a',  # overlay bars while voice is present
    'wave_quiet':  '#2e3a34',  # overlay bars near silence
    'selection':   '#28453a',  # text selection / listbox selection
}

LIGHT = {
    'bg':        '#f4f7f5',
    'bg_raised': '#ffffff',
    'bg_input':  '#ffffff',
    'bg_hover':  '#e9efeb',
    'border':    '#d5ded8',
    'fg':        '#1a211d',
    'fg_dim':    '#55605a',
    'fg_faint':  '#8a948e',
    'accent':          '#1f7a4d',
    'accent_hover':    '#25905b',
    'accent_pressed':  '#186340',
    'on_accent': '#ffffff',
    'error':     '#c4373d',
    'warn':      '#9a6a24',
    'info':      '#20609c',
    'wave_live':   '#1f7a4d',
    'wave_quiet':  '#c2cfc7',
    'selection':   '#cde8da',
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
# Sizes are points: Tk maps points through the DPI scale once `tk scaling`
# is set by init_window().
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _family_ui() -> str:
    try:
        fams = set(tkfont.families())
    except Exception:
        return 'Segoe UI'
    return 'Segoe UI Variable Text' if 'Segoe UI Variable Text' in fams else 'Segoe UI'


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


# Spacing scale (design px) — use with px(): px(GAP_M) etc.
GAP_XS, GAP_S, GAP_M, GAP_L, GAP_XL = 4, 8, 12, 16, 24


# ---------------------------------------------------------------------------
# Window chrome — dark title bar + rounded corners, and per-window setup.
# ---------------------------------------------------------------------------

def _dwm_set(hwnd: int, attr: int, value: int) -> None:
    v = ctypes.c_int(value)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))


def apply_chrome(root: tk.Misc, dark: bool = None) -> None:
    """Match the OS title bar to the theme and round the corners (Win11)."""
    if not IS_WIN:
        return
    use_dark = (not system_prefers_light()) if dark is None else dark
    try:
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        _dwm_set(hwnd, 20, 1 if use_dark else 0)   # DWMWA_USE_IMMERSIVE_DARK_MODE
        _dwm_set(hwnd, 33, 2)                      # DWMWA_WINDOW_CORNER_PREFERENCE = ROUND
    except Exception:
        pass


def init_window(root: tk.Tk, title: str = 'Parley') -> dict:
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
    apply_chrome(root)
    return pal


# ---------------------------------------------------------------------------
# Widget kit — flat, hover-aware, keyboard-focusable buttons and inputs.
# Plain tk widgets (no ttk dependency) so every surface can use them.
# ---------------------------------------------------------------------------

def button(parent, text, command, pal=None, primary=False, danger=False, width=None):
    """Flat button with hover/press/focus states in theme colors."""
    pal = pal or palette()
    if primary:
        bg, bg_h, bg_p, fg = pal['accent'], pal['accent_hover'], pal['accent_pressed'], pal['on_accent']
    elif danger:
        bg, bg_h, bg_p, fg = pal['bg_raised'], pal['bg_hover'], pal['bg_hover'], pal['error']
    else:
        bg, bg_h, bg_p, fg = pal['bg_raised'], pal['bg_hover'], pal['bg_hover'], pal['fg']
    btn = tk.Button(
        parent, text=text, command=command,
        bg=bg, fg=fg, activebackground=bg_p, activeforeground=fg,
        relief='flat', bd=0, cursor='hand2',
        font=font(10, 'bold' if primary else 'normal'),
        padx=px(14), pady=px(6),
        highlightthickness=px(1),
        highlightbackground=pal['border'], highlightcolor=pal['accent'],
    )
    if width:
        btn.configure(width=width)
    btn.bind('<Enter>', lambda e: btn.configure(bg=bg_h))
    btn.bind('<Leave>', lambda e: btn.configure(bg=bg))
    btn.bind('<Return>', lambda e: (command(), 'break')[1])
    return btn


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


def card(parent, pal=None, **kw):
    """Raised panel with a hairline border for grouping content."""
    pal = pal or palette()
    outer = tk.Frame(parent, bg=pal['border'], **kw)
    inner = tk.Frame(outer, bg=pal['bg_raised'])
    inner.pack(fill='both', expand=True, padx=px(1), pady=px(1))
    outer.inner = inner
    return outer


def key_chip(parent, text, pal=None):
    """Keyboard-key chip (cheat sheet, first-run) in mono type."""
    pal = pal or palette()
    return tk.Label(
        parent, text=text, font=font(9, 'bold', mono=True),
        bg=pal['bg_input'], fg=pal['accent'],
        padx=px(8), pady=px(3),
        highlightthickness=px(1), highlightbackground=pal['border'],
    )


def heading(parent, text, pal=None, size=14):
    pal = pal or palette()
    return tk.Label(parent, text=text, font=font(size, 'bold'),
                    bg=pal['bg'], fg=pal['fg'], anchor='w')


def caption(parent, text, pal=None, wrap=None, bg=None):
    pal = pal or palette()
    kw = {'wraplength': px(wrap)} if wrap else {}
    return tk.Label(parent, text=text, font=font(9),
                    bg=bg or pal['bg'], fg=pal['fg_dim'], anchor='w',
                    justify='left', **kw)


# Overlay acrylic tint derived from the canvas color (AABBGGRR for DWM).
def acrylic_tint(alpha: int = 0xB3) -> int:
    pal = palette()
    r, g, b = (int(pal['bg'][i:i + 2], 16) for i in (1, 3, 5))
    return (alpha << 24) | (b << 16) | (g << 8) | r
