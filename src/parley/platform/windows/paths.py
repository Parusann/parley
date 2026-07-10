import os
from pathlib import Path

def get_app_data_path():
    return Path(os.getenv('APPDATA')) / 'parley'

def get_legacy_app_data_path():
    # Pre-1.0 releases stored settings under `whisperkey`; used once for migration.
    return Path(os.getenv('APPDATA')) / 'whisperkey'

def open_file(path):
    os.startfile(path)
