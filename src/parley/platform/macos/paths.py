import subprocess
from pathlib import Path

def get_app_data_path():
    return Path.home() / '.parley'

def get_legacy_app_data_path():
    # Pre-1.0 releases stored settings under `.whisperkey`; used once for migration.
    return Path.home() / '.whisperkey'

def open_file(path):
    subprocess.run(['open', str(path)])
