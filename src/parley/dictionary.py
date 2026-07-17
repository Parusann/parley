import logging
import threading
from pathlib import Path
from typing import List, Optional

from ruamel.yaml import YAML

from .utils import get_user_app_data_path

logger = logging.getLogger(__name__)
USER_SETTINGS = "user_settings.yaml"

# Singleton guard for the add-word dialog: re-invoking (tray click / repeated
# CLI) raises the existing window instead of stacking new Tk roots + threads.
_dialog_lock = threading.Lock()
_dialog_root = None


def list_hotwords() -> List[str]:
    user_path = Path(get_user_app_data_path()) / USER_SETTINGS
    if not user_path.exists():
        return []
    try:
        with open(user_path, encoding='utf-8') as f:
            data = YAML().load(f) or {}
        return list((data.get('whisper') or {}).get('hotwords') or [])
    except Exception as e:
        logger.warning(f"Could not read hotwords: {e}")
        return []


def add_word(word: str) -> bool:
    word = (word or '').strip()
    if not word:
        return False
    user_path = Path(get_user_app_data_path()) / USER_SETTINGS
    yaml = YAML()
    if user_path.exists():
        with open(user_path, encoding='utf-8') as f:
            data = yaml.load(f) or {}
    else:
        data = {}
    whisper = data.setdefault('whisper', {})
    current = list(whisper.get('hotwords') or [])
    if word in current:
        print(f"   '{word}' already in dictionary")
        return False
    current.append(word)
    whisper['hotwords'] = current
    with open(user_path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f)
    print(f"   ✓ Added '{word}' to whisper.hotwords ({len(current)} total)")
    return True


def remove_word(word: str) -> bool:
    word = (word or '').strip()
    if not word:
        return False
    user_path = Path(get_user_app_data_path()) / USER_SETTINGS
    if not user_path.exists():
        return False
    yaml = YAML()
    with open(user_path, encoding='utf-8') as f:
        data = yaml.load(f) or {}
    whisper = data.get('whisper') or {}
    current = list(whisper.get('hotwords') or [])
    if word not in current:
        print(f"   '{word}' not found in dictionary")
        return False
    current.remove(word)
    whisper['hotwords'] = current
    data['whisper'] = whisper
    with open(user_path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f)
    print(f"   ✓ Removed '{word}' from dictionary ({len(current)} left)")
    return True


def show_dictionary() -> int:
    words = list_hotwords()
    if not words:
        print("Dictionary is empty. Add words with: parley --add-word NAME")
        return 0
    print(f"\nParley — Dictionary ({len(words)} word{'s' if len(words) != 1 else ''})\n")
    for w in sorted(words, key=str.lower):
        print(f"  • {w}")
    print()
    print("Edit via:")
    print("  parley --add-word NAME")
    print("  parley --remove-word NAME")
    print(f"  Or open {Path(get_user_app_data_path()) / USER_SETTINGS} directly\n")
    return 0


def show_add_word_dialog(on_added=None):
    global _dialog_root
    with _dialog_lock:
        try:
            if _dialog_root is not None and _dialog_root.winfo_exists():
                _dialog_root.lift()
                _dialog_root.focus_force()
                return
        except Exception:
            pass

    def run():
        global _dialog_root
        try:
            import tkinter as tk
        except ImportError:
            logger.info("Tkinter unavailable for add-word dialog")
            return
        try:
            from . import theme
            from .theme import px, GAP_S, GAP_M

            theme.ensure_dpi_aware()
            root = tk.Tk()
            with _dialog_lock:
                _dialog_root = root
            pal = theme.init_window(root, "Parley — Add Word")

            w, h = px(400), px(210)
            try:
                root.update_idletasks()
                sw = root.winfo_screenwidth()
                sh = root.winfo_screenheight()
                root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")
            except Exception:
                root.geometry(f"{w}x{h}")

            outer = tk.Frame(root, bg=pal['bg'], padx=px(14), pady=px(GAP_M))
            outer.pack(fill='both', expand=True)

            theme.heading(outer, "Add to your hotword dictionary",
                          pal, size=12).pack(fill='x')
            theme.caption(outer, "Names, jargon, acronyms — anything Whisper mishears",
                          pal).pack(fill='x', pady=(px(2), px(10)))

            entry_var = tk.StringVar()
            entry = theme.entry(outer, textvariable=entry_var, pal=pal)
            entry.configure(font=theme.font(11))
            entry.pack(fill='x', ipady=px(6), pady=(0, px(GAP_M)))
            entry.focus_set()

            status_row = tk.Frame(outer, bg=pal['bg'])
            status_row.pack(fill='x', pady=(0, px(GAP_S)))
            status_var = tk.StringVar(value="")
            status = theme.caption(status_row, "", pal)
            status.configure(textvariable=status_var)
            status.pack(side='left')
            # The one shared restart affordance — appears after the first add.
            restart_note = theme.restart_chip(status_row, pal)

            def commit(_=None):
                word = entry_var.get().strip()
                if not word:
                    status_var.set("Type a word first.")
                    return
                ok = add_word(word)
                if ok:
                    status_var.set(f"Added '{word}'.")
                    restart_note.pack(side='left', padx=(px(GAP_S), 0))
                    entry_var.set("")
                    if on_added:
                        try: on_added(word)
                        except Exception: pass
                else:
                    status_var.set(f"'{word}' is already in the dictionary.")

            def close(_=None):
                global _dialog_root
                with _dialog_lock:
                    _dialog_root = None
                try: root.destroy()
                except Exception: pass

            buttons = tk.Frame(outer, bg=pal['bg'])
            buttons.pack(fill='x')

            theme.button(buttons, "Close", close, pal).pack(side='right', padx=(px(GAP_S), 0))
            theme.button(buttons, "Add", commit, pal, primary=True).pack(side='right')

            root.protocol("WM_DELETE_WINDOW", close)
            root.bind('<Return>', commit)
            root.bind('<Escape>', close)
            root.mainloop()
        except Exception as e:
            logger.warning(f"Add-word dialog failed: {e}")
        finally:
            with _dialog_lock:
                _dialog_root = None

    threading.Thread(target=run, daemon=True, name='add-word-dialog').start()
