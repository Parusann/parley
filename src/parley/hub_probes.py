# hub_probes.py
# Headless status checks behind the hub's lights. Every probe returns a bool
# (or (ok, detail) where noted) and never raises; network primitives are
# injectable so tests stay offline. Mirrors doctor.py's probe styles.

import socket
import threading
import urllib.request


def _tcp_connect(host: str, port: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port))


def _http_status(url: str, timeout: float) -> int:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status


def vulkan_server_up(host: str, port: int, connect=_tcp_connect) -> bool:
    try:
        return connect(host, port) == 0
    except OSError:
        return False


def ollama_up(endpoint: str, get_status=_http_status) -> bool:
    try:
        return get_status(endpoint.rstrip('/') + '/api/tags', timeout=1.5) == 200
    except Exception:
        return False


def amadeus_up(router_cfg: dict, get_status=_http_status) -> bool:
    """Reachability of the Switchboard, derived from the configured /ask endpoint."""
    endpoint = str((router_cfg or {}).get('endpoint', '') or '')
    if not endpoint:
        return False
    base = endpoint.rsplit('/', 1)[0]
    try:
        return get_status(base + '/health', timeout=2.0) == 200
    except Exception:
        return False


def mic_present(configured_name: str = '') -> tuple:
    """(ok, device label). Import inside so a broken audio stack can't kill the hub."""
    try:
        import sounddevice
        devices = [d for d in sounddevice.query_devices() if d.get('max_input_channels', 0) > 0]
        if not devices:
            return False, 'no input devices'
        if configured_name:
            for d in devices:
                if configured_name.lower() in str(d.get('name', '')).lower():
                    return True, d['name']
            return False, f'"{configured_name}" not found'
        default = sounddevice.query_devices(kind='input')
        return True, default.get('name', 'default input')
    except Exception as exc:
        return False, f'audio stack error: {exc}'


class Prober:
    """Refreshes {name: (ok, detail)} on a daemon thread while the hub is open."""

    def __init__(self, probes: dict, interval_s: float = 2.0):
        self._probes = probes
        self._interval = interval_s
        self._lock = threading.Lock()
        self._snapshot = {}
        self._stop = threading.Event()

    def refresh_once(self) -> None:
        results = {}
        for name, fn in self._probes.items():
            try:
                results[name] = fn()
            except Exception as exc:
                results[name] = (False, f'probe error: {exc}')
        with self._lock:
            self._snapshot = results

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._snapshot)

    def start(self) -> None:
        def _loop():
            while not self._stop.is_set():
                self.refresh_once()
                self._stop.wait(self._interval)

        threading.Thread(target=_loop, name='hub-prober', daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
