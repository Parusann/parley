# hook_events.py
# Low-level keyboard event tap for the hub's keybind capture: a raw
# WH_KEYBOARD_LL hook pumping messages on its own thread, exposing
# hook(callback)/unhook(handle) with events shaped like {event_type, name}.
# Deliberately NOT the `keyboard` package: its hook never receives events in
# a process where global_hotkeys is active (reproduced 2026-07-17), and
# Parley always runs global_hotkeys.

import ctypes
import ctypes.wintypes as wintypes
import logging
import threading

from .keyboard import VK_MAP

logger = logging.getLogger(__name__)

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105
WM_QUIT = 0x0012
MAPVK_VK_TO_CHAR = 2

# Private DLL instances: ctypes.windll.* is a process-wide cache whose
# function objects (argtypes/restype) are shared mutable state — the hotkey
# libraries in this process configure the same functions differently.
_user32 = ctypes.WinDLL('user32', use_last_error=True)
_kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

# 64-bit-correct signatures: LPARAM/LRESULT are pointer-sized and signed —
# the default c_long marshaling overflows and breaks the hook chain.
_HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)
_user32.CallNextHookEx.argtypes = [
    wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
]
_user32.CallNextHookEx.restype = ctypes.c_ssize_t
_user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD
]
_user32.SetWindowsHookExW.restype = wintypes.HHOOK


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ('vkCode', wintypes.DWORD),
        ('scanCode', wintypes.DWORD),
        ('flags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong)),
    ]


# VK -> name in config vocabulary. Invert the send-side VK_MAP (first name
# wins: 'ctrl' not 'control'), then collapse the side-specific and generic
# modifier VKs the LL hook actually delivers.
_VK_NAMES: dict = {}
for _name, _vk in VK_MAP.items():
    _VK_NAMES.setdefault(_vk, _name)
_VK_NAMES.update({
    0x10: 'shift', 0xA0: 'shift', 0xA1: 'shift',
    0x11: 'ctrl', 0xA2: 'ctrl', 0xA3: 'ctrl',
    0x12: 'alt', 0xA4: 'alt', 0xA5: 'alt',
    0x5B: 'win', 0x5C: 'win',
})


def _vk_name(vk: int) -> str:
    name = _VK_NAMES.get(vk)
    if name:
        return name
    ch = _user32.MapVirtualKeyW(vk, MAPVK_VK_TO_CHAR) & 0xFFFF
    if ch:
        return chr(ch).lower()
    return f'vk{vk}'


class Event:
    """Shape-compatible with the keyboard package's events (name, event_type)."""

    __slots__ = ('event_type', 'name')

    def __init__(self, event_type: str, name: str):
        self.event_type = event_type
        self.name = name


def hook(callback):
    """Install a LL keyboard hook on a fresh pump thread; returns a handle
    for unhook(). The callback receives Event objects for every key down/up."""
    ready = threading.Event()
    handle = {'thread_id': None, 'hhook': None, 'proc': None, 'thread': None}

    def _pump():
        def _proc(n_code, w_param, l_param):
            try:
                if n_code >= 0 and l_param:
                    kbd = ctypes.cast(l_param, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                    if w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                        callback(Event('down', _vk_name(kbd.vkCode)))
                    elif w_param in (WM_KEYUP, WM_SYSKEYUP):
                        callback(Event('up', _vk_name(kbd.vkCode)))
            except Exception:
                logger.exception("hook callback failed")
            return _user32.CallNextHookEx(None, n_code, w_param, l_param)

        proc = _HOOKPROC(_proc)
        handle['proc'] = proc  # keep alive — GC'd callbacks crash the chain
        handle['thread_id'] = _kernel32.GetCurrentThreadId()
        handle['hhook'] = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, proc, None, 0)
        ready.set()
        if not handle['hhook']:
            logger.error("SetWindowsHookExW failed: %s", ctypes.get_last_error())
            return
        msg = wintypes.MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))
        _user32.UnhookWindowsHookEx(handle['hhook'])

    thread = threading.Thread(target=_pump, name='hub-keyhook', daemon=True)
    handle['thread'] = thread
    thread.start()
    ready.wait(timeout=2.0)
    return handle


def unhook(handle) -> None:
    """End the pump (which unhooks) and let the daemon thread finish."""
    try:
        if handle and handle.get('thread_id'):
            _user32.PostThreadMessageW(handle['thread_id'], WM_QUIT, 0, 0)
    except Exception:
        logger.exception("unhook failed")
