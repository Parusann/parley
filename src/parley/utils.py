import os
import subprocess
import sys
import importlib.resources
import tomllib
from pathlib import Path

class OptionalComponent:
    def __init__(self, component):
        self._component = component
    
    def __getattr__(self, name):
        if self._component and hasattr(self._component, name):
            attr = getattr(self._component, name)
            return attr
        else:
            # Return a no-op function for missing methods/attributes
            return lambda *args, **kwargs: None


def beautify_hotkey(hotkey_string: str) -> str:
    if not hotkey_string:
        return ""

    return hotkey_string.upper()

def parse_hotkey(hotkey_string: str) -> list:
    if not hotkey_string:
        return []
    return hotkey_string.lower().split('+')

def is_installed_package():
    # Check if running from an installed package
    return 'site-packages' in __file__

def get_user_app_data_path():
    # On Linux CI / unsupported platforms, .platform doesn't ship a `paths`
    # module. Fall back to ~/.parley so tests and tools that just need a
    # directory keep working — actual app functionality is Windows/macOS only.
    try:
        from .platform import paths
        parley_dir = paths.get_app_data_path()
        _migrate_legacy_config_dir(paths, parley_dir)
    except ImportError:
        parley_dir = Path.home() / ".parley"
    parley_dir.mkdir(parents=True, exist_ok=True)
    return str(parley_dir)

def _migrate_legacy_config_dir(paths, new_dir: Path):
    # One-time, non-destructive: pre-1.0 installs kept settings in `whisperkey`.
    # Copy (never move) so a rollback to an old install still finds its config.
    if new_dir.exists():
        return
    legacy = getattr(paths, 'get_legacy_app_data_path', lambda: None)()
    if legacy and legacy.is_dir():
        import shutil
        try:
            shutil.copytree(legacy, new_dir)
        except OSError:
            pass  # fall through to a fresh dir; defaults regenerate

def open_file(path):
    # Best-effort on Linux: log only, don't crash. Real Windows/macOS path goes
    # through the platform module's xdg-open / explorer.exe wrapper.
    try:
        from .platform import paths
        paths.open_file(path)
    except ImportError:
        import logging
        logging.getLogger(__name__).warning(f"open_file('{path}'): unsupported platform")

def resolve_asset_path(relative_path: str) -> str:
    if not relative_path or os.path.isabs(relative_path):
        return relative_path

    if is_installed_package():
        files = importlib.resources.files("parley")
        return str(files / relative_path)

    return str(Path(__file__).parent / relative_path)

def setup_portaudio_path():
    # Called first in main.py - platform module imports break WASAPI
    if sys.platform != 'win32':
        return
    assets_dir = Path(resolve_asset_path('platform/windows/assets'))
    if assets_dir.exists():
        os.environ['PATH'] = str(assets_dir) + os.pathsep + os.environ.get('PATH', '')

def restart_or_exit(message_restart, message_exit):
    pyapp_exe = os.environ.get('PYAPP', '')
    if os.path.isfile(pyapp_exe):
        print(message_restart)
        subprocess.Popen([pyapp_exe], creationflags=subprocess.CREATE_NEW_CONSOLE)
    else:
        print(message_exit)
    sys.exit(0)


def get_version():
    if is_installed_package():
        import importlib.metadata
        return importlib.metadata.version("parley")

    pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"
    with open(pyproject_path, 'rb') as f:
        data = tomllib.load(f)
        return f"{data['project']['version']}-dev"