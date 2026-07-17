# settings_ui.py
# GUI settings editor — launched by `parley --settings` or the tray
# "Settings..." item. Tkinter-only, no extra deps. Reads/writes user_settings.yaml
# via ConfigManager, preserving comments and overrides-only semantics.
#
# Layout:
#   • Top: search box (Ctrl+F to focus) that hides/shows rows across all tabs
#   • Middle: Notebook with 3 tabs — General, Audio, Post-process
#     (keybinds live in the Hub window, where they apply without a restart)
#   • Bottom-left: Backup… / Restore… / Reset-to-defaults
#   • Bottom-right: Save / Cancel
#
# Each setting row is its own packed Frame so the search filter can pack_forget/pack
# it without disturbing the rest. row_index[path] = {widgets, label, tab_index}
# is the structure that powers search.

import logging

# Imported lazily up here so module-level helper functions can use it. Falls
# back to None on systems without Tkinter — the run_settings_window() entry
# point checks again before touching anything.
try:
    from tkinter import ttk
except ImportError:
    ttk = None

logger = logging.getLogger(__name__)


# Reset user settings to defaults while PRESERVING whisper.hotwords, which live
# inside user_settings.yaml. The reset dialog promises the dictionary survives, so
# we can't just delete the file. Module-level (not a Tk closure) so it's testable.
def reset_settings_preserving_hotwords(settings_path) -> list:
    from ruamel.yaml import YAML
    import os
    preserved = []
    if os.path.exists(settings_path):
        try:
            with open(settings_path, encoding='utf-8') as f:
                existing = YAML().load(f) or {}
            preserved = list((existing.get('whisper') or {}).get('hotwords') or [])
        except Exception:
            preserved = []
        os.remove(settings_path)
    if preserved:
        with open(settings_path, 'w', encoding='utf-8') as f:
            YAML().dump({'whisper': {'hotwords': preserved}}, f)
    return preserved


# Public entry point. Loads the live config, builds the window, blocks on
# mainloop. Designed to be invoked as a subprocess (`python -m parley.main
# --settings`) from the tray so the running app's Tk root isn't disturbed.
def run_settings_window():
    try:
        import tkinter as tk
        from tkinter import ttk, filedialog
    except ImportError:
        logger.error("Tkinter not available")
        return

    from . import theme
    from .theme import px, GAP_S

    from .config_manager import ConfigManager
    cm = ConfigManager(quiet=True)

    theme.ensure_dpi_aware()
    root = tk.Tk()
    pal = theme.init_window(root, "Parley — Settings")
    root.geometry(f"{px(600)}x{px(680)}")
    root.minsize(px(560), px(560))

    _apply_style(root)

    search_bar = tk.Frame(root, bg=pal['bg'])
    search_bar.pack(fill='x', padx=px(10), pady=(px(10), px(4)))
    theme.caption(search_bar, 'Search', pal).pack(side='left')
    search_var = tk.StringVar()
    search_entry = theme.entry(search_bar, textvariable=search_var, pal=pal)
    search_entry.pack(side='left', fill='x', expand=True, padx=(px(GAP_S), 0), ipady=px(3))
    hint_var = tk.StringVar(value='Type to search settings…')
    tk.Label(search_bar, textvariable=hint_var, bg=pal['bg'], fg=pal['fg_dim'],
             font=theme.font(8)).pack(side='left', padx=(px(GAP_S), 0))

    nb = ttk.Notebook(root)
    nb.pack(fill='both', expand=True, padx=8, pady=4)

    vars_ = {}
    row_index = {}
    _TAB_COUNTER['n'] = 0

    _build_general_tab(nb, cm, vars_, row_index)
    _build_audio_tab(nb, cm, vars_, row_index)
    _build_postprocess_tab(nb, cm, vars_, row_index)

    sep = tk.Frame(root, bg=pal['border'], height=px(1))
    sep.pack(fill='x', padx=px(GAP_S))

    bottom = tk.Frame(root, bg=pal['bg'])
    bottom.pack(fill='x', padx=px(GAP_S), pady=px(GAP_S))

    def save():
        _save_all(cm, vars_)
        theme.dialog(
            root, "Saved",
            "Settings saved.\n\n"
            "Formatting and post-processing changes apply on your next "
            "dictation. Model and audio-device changes need a restart "
            "(tray → Restart Parley). Keybinds live in the Hub and apply "
            "instantly.",
            tone='ok', chip_text=theme.RESTART_NOTE,
        )
        root.destroy()

    def reset_defaults():
        if theme.dialog(
            root, "Reset all settings?",
            "This will reset every setting to its default value.\n\n"
            "Your hotwords, commands, transforms, and transcript history "
            "will NOT be affected.",
            buttons=('Reset', 'Cancel'), danger=True, tone='warn',
        ) != 'Reset':
            return
        try:
            from .utils import get_user_app_data_path
            import os
            settings_path = os.path.join(get_user_app_data_path(), 'user_settings.yaml')
            reset_settings_preserving_hotwords(settings_path)
            theme.dialog(
                root, "Reset complete",
                "Settings reset to defaults (your dictionary was kept).",
                tone='ok', chip_text=theme.RESTART_NOTE,
            )
            root.destroy()
        except Exception as e:
            theme.dialog(root, "Reset failed", str(e), tone='error')

    def export_settings_action():
        target = filedialog.askdirectory(
            title="Choose a folder for the settings backup",
            parent=root,
        )
        if not target:
            return
        try:
            from .settings_io import export_settings
            if export_settings(target) == 0:
                theme.dialog(root, "Exported",
                             f"Settings backed up to:\n{target}", tone='ok')
            else:
                theme.dialog(root, "Export failed",
                             "See console for details.", tone='error')
        except Exception as e:
            theme.dialog(root, "Export failed", str(e), tone='error')

    def import_settings_action():
        source = filedialog.askdirectory(
            title="Choose a settings backup folder to restore",
            parent=root,
        )
        if not source:
            return
        if theme.dialog(
            root, "Restore settings?",
            f"Restore settings from:\n{source}\n\n"
            "Your current settings will be overwritten.",
            buttons=('Restore', 'Cancel'), danger=True, tone='warn',
        ) != 'Restore':
            return
        try:
            from .settings_io import import_settings
            if import_settings(source) == 0:
                theme.dialog(root, "Restored", "Settings restored.",
                             tone='ok', chip_text=theme.RESTART_NOTE)
                root.destroy()
            else:
                theme.dialog(root, "Restore failed",
                             "See console for details.", tone='error')
        except Exception as e:
            theme.dialog(root, "Restore failed", str(e), tone='error')

    left = tk.Frame(bottom, bg=pal['bg'])
    left.pack(side='left')
    theme.button(left, 'Backup…', export_settings_action, pal).pack(side='left', padx=(0, px(4)))
    theme.button(left, 'Restore…', import_settings_action, pal).pack(side='left', padx=(0, px(4)))
    theme.button(left, 'Reset to defaults', reset_defaults, pal, danger=True).pack(side='left')

    right = tk.Frame(bottom, bg=pal['bg'])
    right.pack(side='right')
    theme.button(right, 'Save', save, pal, primary=True).pack(side='right', padx=(px(4), 0))
    theme.button(right, 'Cancel', root.destroy, pal).pack(side='right')

    # Snapshot each row widget's real pack geometry now, while everything is laid
    # out exactly as the builders intended. The search filter restores from this
    # instead of guessing at padx/pady (footnotes and note-rows differ), and
    # re-packs in creation order so filtering never reshuffles rows.
    pack_specs = {}
    for rec in row_index.values():
        for w in rec['widgets']:
            try:
                info = {k: v for k, v in w.pack_info().items() if k != 'in'}
                pack_specs[w] = info
            except Exception:
                pack_specs[w] = {'fill': 'x', 'padx': px(12), 'pady': px(2)}

    def _show(w):
        try:
            w.pack(**pack_specs.get(w, {'fill': 'x', 'padx': px(12), 'pady': px(2)}))
        except Exception:
            pass

    def on_search(*_):
        query = search_var.get().lower().strip()
        # Forget everything first, then re-pack the visible rows in their original
        # creation order — guarantees stable ordering regardless of match set.
        for rec in row_index.values():
            for w in rec['widgets']:
                try: w.pack_forget()
                except Exception: pass

        if not query:
            hint_var.set('')
            for rec in row_index.values():
                for w in rec['widgets']:
                    _show(w)
            return

        matched_count = 0
        matched_tabs = set()
        for path, rec in row_index.items():
            haystack = (path + ' ' + rec.get('label', '')).lower()
            if query in haystack:
                matched_count += 1
                matched_tabs.add(rec['tab_index'])
                for w in rec['widgets']:
                    _show(w)
        if matched_tabs:
            try:
                nb.select(min(matched_tabs))
            except Exception:
                pass
        hint_var.set(f'{matched_count} match{"" if matched_count == 1 else "es"}')

    search_var.trace_add('write', on_search)
    root.bind('<Control-f>', lambda e: search_entry.focus_set())
    root.bind('<Escape>', lambda e: root.destroy())

    root.mainloop()


# Every ttk color/font flows from theme.palette()/theme.font() on top of the
# clam base theme — clam is the only built-in that respects fieldbackground.
def _apply_style(root):
    try:
        import tkinter.ttk as ttk
        from . import theme
        from .theme import px
        pal = theme.palette()
        style = ttk.Style(root)
        style.theme_use('clam')
        # clam paints 3D bevels with lightcolor/darkcolor — flatten them to the
        # canvas so no stock grey/white edges survive in either theme.
        style.configure('.', background=pal['bg'], foreground=pal['fg'],
                        bordercolor=pal['border'],
                        lightcolor=pal['bg'], darkcolor=pal['bg'],
                        troughcolor=pal['bg_raised'],
                        focuscolor=pal['accent'],
                        selectbackground=pal['selection'],
                        selectforeground=pal['fg'],
                        borderwidth=0, font=theme.font(9))
        style.configure('TNotebook', background=pal['bg'], borderwidth=0,
                        tabmargins=[px(8), px(6), px(8), 0])
        style.configure('TNotebook.Tab', background=pal['bg'], foreground=pal['fg_dim'],
                        lightcolor=pal['bg'], darkcolor=pal['bg'],
                        padding=[px(14), px(6)], font=theme.font(10))
        style.map('TNotebook.Tab',
                  background=[('selected', pal['bg_raised']), ('active', pal['bg_hover'])],
                  foreground=[('selected', pal['fg']), ('active', pal['fg'])],
                  lightcolor=[('selected', pal['bg_raised'])])
        style.configure('TFrame', background=pal['bg'])
        style.configure('TLabel', background=pal['bg'], foreground=pal['fg'])
        style.configure('TCheckbutton', background=pal['bg'], foreground=pal['fg'],
                        indicatorbackground=pal['bg_input'],
                        indicatorforeground=pal['on_accent'],
                        font=theme.font(9))
        style.map('TCheckbutton',
                  background=[('active', pal['bg'])],
                  indicatorbackground=[('selected', pal['accent'])])
        style.configure('TEntry', fieldbackground=pal['bg_input'], foreground=pal['fg'],
                        insertcolor=pal['fg'], bordercolor=pal['border'],
                        lightcolor=pal['bg_input'], darkcolor=pal['bg_input'])
        style.configure('TCombobox', fieldbackground=pal['bg_input'], foreground=pal['fg'],
                        background=pal['bg_raised'], arrowcolor=pal['fg_dim'],
                        bordercolor=pal['border'],
                        lightcolor=pal['bg_input'], darkcolor=pal['bg_input'],
                        selectbackground=pal['bg_input'], selectforeground=pal['fg'])
        style.map('TCombobox',
                  fieldbackground=[('readonly', pal['bg_input'])],
                  foreground=[('readonly', pal['fg'])])
        style.configure('TSeparator', background=pal['border'])
        # The combobox dropdown is a plain Tk listbox — style it via options.
        root.option_add('*TCombobox*Listbox.background', pal['bg_raised'])
        root.option_add('*TCombobox*Listbox.foreground', pal['fg'])
        root.option_add('*TCombobox*Listbox.selectBackground', pal['selection'])
        root.option_add('*TCombobox*Listbox.selectForeground', pal['fg'])
    except Exception:
        pass


_TAB_COUNTER = {'n': 0}


def _frame(nb, title, row_index):
    import tkinter as tk
    from . import theme
    outer = ttk.Frame(nb)
    nb.add(outer, text=title)
    tab_idx = _TAB_COUNTER['n']
    _TAB_COUNTER['n'] += 1
    inner = tk.Frame(outer, bg=theme.palette()['bg'])
    inner.pack(fill='both', expand=True)
    return {'outer': outer, 'inner': inner, 'tab_index': tab_idx}


def _row(tab, path, label, widget_fn, row_index, note=None):
    import tkinter as tk
    from . import theme
    from .theme import px
    pal = theme.palette()
    parent = tab['inner']
    row_frame = tk.Frame(parent, bg=pal['bg'])
    row_frame.pack(fill='x', padx=px(12), pady=px(2))
    tk.Label(row_frame, text=label, fg=pal['fg_dim'], bg=pal['bg'],
             font=theme.font(9), anchor='w', width=24).pack(side='left')
    w = widget_fn(row_frame)
    w.pack(side='right', fill='x', expand=True)
    row_index[path] = {'widgets': [row_frame], 'label': label, 'tab_index': tab['tab_index']}
    if note:
        note_frame = tk.Frame(parent, bg=pal['bg'])
        note_frame.pack(fill='x', padx=px(12), pady=(0, px(4)))
        tk.Label(note_frame, text=note, fg=pal['fg_dim'], bg=pal['bg'],
                 font=theme.font(8), anchor='w').pack(side='left')
        row_index[path]['widgets'].append(note_frame)
    return w


def _check(tab, path, label, var, row_index):
    import tkinter as tk
    from . import theme
    from .theme import px
    parent = tab['inner']
    row_frame = tk.Frame(parent, bg=theme.palette()['bg'])
    row_frame.pack(fill='x', padx=px(12), pady=px(2))
    cb = theme.checkbox(row_frame, label, var)
    cb.pack(side='left', anchor='w')
    row_index[path] = {'widgets': [row_frame], 'label': label, 'tab_index': tab['tab_index']}
    return cb


def _separator(tab):
    import tkinter as tk
    from . import theme
    from .theme import px
    parent = tab['inner']
    sep = tk.Frame(parent, bg=theme.palette()['border'], height=px(1))
    sep.pack(fill='x', padx=px(12), pady=px(8))


def _footnote(tab, text):
    import tkinter as tk
    from . import theme
    from .theme import px
    pal = theme.palette()
    parent = tab['inner']
    fn = tk.Frame(parent, bg=pal['bg'])
    fn.pack(fill='x', padx=px(12), pady=(px(8), px(4)))
    tk.Label(fn, text=text, fg=pal['fg_dim'], bg=pal['bg'],
             font=theme.font(8), anchor='w',
             justify='left', wraplength=px(520)).pack(side='left')


def _combo(parent, var, values):
    cb = ttk.Combobox(parent, textvariable=var, values=values,
                      state='readonly', width=22)
    return cb


def _entry(parent, var):
    from . import theme
    return theme.entry(parent, textvariable=var, mono=True, width=24)


def _v(cfg, *path, default=''):
    obj = cfg
    for k in path:
        if not isinstance(obj, dict):
            return default
        obj = obj.get(k, default)
    return obj if obj is not None else default


def _build_general_tab(nb, cm, vars_, row_index):
    import tkinter as tk
    tab = _frame(nb, 'General', row_index)
    cfg = cm.config
    whisper = cfg.get('whisper', {})

    models = list((whisper.get('models') or {}).keys())
    v = tk.StringVar(value=_v(cfg, 'whisper', 'model', default='tiny'))
    vars_['whisper.model'] = v
    _row(tab, 'whisper.model', 'Model', lambda p: _combo(p, v, models), row_index)

    langs = ['auto', 'en', 'es', 'fr', 'de', 'it', 'pt', 'nl', 'pl',
             'ru', 'ja', 'zh', 'ko', 'hi', 'ar']
    v = tk.StringVar(value=_v(cfg, 'whisper', 'language', default='auto'))
    vars_['whisper.language'] = v
    _row(tab, 'whisper.language', 'Language', lambda p: _combo(p, v, langs), row_index)

    devs = ['cpu', 'cuda']
    v = tk.StringVar(value=_v(cfg, 'whisper', 'device', default='cpu'))
    vars_['whisper.device'] = v
    _row(tab, 'whisper.device', 'Compute device',
         lambda p: _combo(p, v, devs), row_index)

    ctypes = ['int8', 'float16', 'float32']
    v = tk.StringVar(value=_v(cfg, 'whisper', 'compute_type', default='int8'))
    vars_['whisper.compute_type'] = v
    _row(tab, 'whisper.compute_type', 'Compute type',
         lambda p: _combo(p, v, ctypes), row_index)

    v = tk.StringVar(value=str(_v(cfg, 'whisper', 'beam_size', default=5)))
    vars_['whisper.beam_size'] = v
    _row(tab, 'whisper.beam_size', 'Beam size (1–10)',
         lambda p: _entry(p, v), row_index)

    v = tk.StringVar(value=str(_v(cfg, 'whisper', 'initial_prompt', default='')))
    vars_['whisper.initial_prompt'] = v
    _row(tab, 'whisper.initial_prompt', 'Initial prompt',
         lambda p: _entry(p, v), row_index)

    v = tk.BooleanVar(value=bool(_v(cfg, 'whisper', 'prompt_from_selection', default=False)))
    vars_['whisper.prompt_from_selection'] = v
    _check(tab, 'whisper.prompt_from_selection',
           'Seed prompt from selected text at recording start', v, row_index)

    v = tk.BooleanVar(value=bool(_v(cfg, 'clipboard', 'auto_paste', default=True)))
    vars_['clipboard.auto_paste'] = v
    _check(tab, 'clipboard.auto_paste',
           'Auto-paste at cursor after transcription', v, row_index)

    v = tk.BooleanVar(value=bool(_v(cfg, 'audio', 'continuous_mode', default=False)))
    vars_['audio.continuous_mode'] = v
    _check(tab, 'audio.continuous_mode',
           'Continuous dictation mode (auto-restarts recording)', v, row_index)


def _build_audio_tab(nb, cm, vars_, row_index):
    import tkinter as tk
    tab = _frame(nb, 'Audio', row_index)
    cfg = cm.config
    ns = (cfg.get('audio') or {}).get('noise_suppression') or {}

    v = tk.BooleanVar(value=bool(ns.get('enabled', False)))
    vars_['audio.noise_suppression.enabled'] = v
    _check(tab, 'audio.noise_suppression.enabled',
           'Noise suppression  (pip install noisereduce)', v, row_index)

    v = tk.StringVar(value=str(ns.get('strength', 0.75)))
    vars_['audio.noise_suppression.strength'] = v
    _row(tab, 'audio.noise_suppression.strength',
         'Noise strength (0.0 – 1.0)', lambda p: _entry(p, v), row_index)

    v = tk.BooleanVar(value=bool(_v(cfg, 'audio', 'pause_media_on_record', default=False)))
    vars_['audio.pause_media_on_record'] = v
    _check(tab, 'audio.pause_media_on_record',
           'Pause media player when recording starts', v, row_index)

    v = tk.StringVar(value=str(_v(cfg, 'audio', 'max_duration', default=900)))
    vars_['audio.max_duration'] = v
    _row(tab, 'audio.max_duration', 'Max recording duration (s)',
         lambda p: _entry(p, v), row_index)

    v = tk.BooleanVar(value=bool(_v(cfg, 'vad', 'vad_realtime_enabled', default=True)))
    vars_['vad.vad_realtime_enabled'] = v
    _check(tab, 'vad.vad_realtime_enabled',
           'Auto-stop on silence (realtime VAD)', v, row_index)

    v = tk.StringVar(value=str(_v(cfg, 'vad', 'vad_silence_timeout_seconds', default=30.0)))
    vars_['vad.vad_silence_timeout_seconds'] = v
    _row(tab, 'vad.vad_silence_timeout_seconds', 'Silence timeout (s)',
         lambda p: _entry(p, v), row_index)

    v = tk.BooleanVar(value=bool(_v(cfg, 'audio_feedback', 'enabled', default=True)))
    vars_['audio_feedback.enabled'] = v
    _check(tab, 'audio_feedback.enabled',
           'Audio feedback sounds (start / stop beeps)', v, row_index)


def _build_postprocess_tab(nb, cm, vars_, row_index):
    import tkinter as tk
    tab = _frame(nb, 'Post-process', row_index)
    cfg = cm.config
    pp = cfg.get('postprocess') or {}

    checks = [
        ('postprocess.strip_filler_words', 'Strip filler words  (um, uh, like, you know)', 'strip_filler_words'),
        ('postprocess.capitalize_first', 'Capitalize first letter', 'capitalize_first'),
        ('postprocess.ensure_punctuation', 'Ensure sentence ends with punctuation', 'ensure_punctuation'),
        ('postprocess.strip_trailing_period', 'Strip trailing period', 'strip_trailing_period'),
        ('postprocess.inline_formatting', 'Inline voice formatting  (say "comma", "period")', 'inline_formatting'),
        ('postprocess.inline_formatting_absorb_punctuation',
         'Absorb Whisper punctuation around spoken cues  (fixes "hello,, world")', 'inline_formatting_absorb_punctuation'),
        ('postprocess.inline_formatting_extend',
         'Keep English cue words when adding your own', 'inline_formatting_extend'),
    ]
    for path, label, cfg_key in checks:
        v = tk.BooleanVar(value=bool(pp.get(cfg_key, False)))
        vars_[path] = v
        _check(tab, path, label, v, row_index)

    _footnote(tab, 'Custom phrase→symbol mappings (for other languages) live under '
                   'inline_formatting_replacements in the settings file — see the FAQ.')

    _separator(tab)

    ollama = pp.get('ollama') or {}

    v = tk.BooleanVar(value=bool(ollama.get('enabled', False)))
    vars_['postprocess.ollama.enabled'] = v
    _check(tab, 'postprocess.ollama.enabled',
           'Ollama polish  (local LLM punctuation cleanup)', v, row_index)

    v = tk.StringVar(value=str(ollama.get('endpoint', 'http://localhost:11434')))
    vars_['postprocess.ollama.endpoint'] = v
    _row(tab, 'postprocess.ollama.endpoint', 'Ollama endpoint',
         lambda p: _entry(p, v), row_index)

    v = tk.StringVar(value=str(ollama.get('model', 'llama3.2')))
    vars_['postprocess.ollama.model'] = v
    _row(tab, 'postprocess.ollama.model', 'Ollama model',
         lambda p: _entry(p, v), row_index)

    v = tk.StringVar(value=str(ollama.get('timeout', 5)))
    vars_['postprocess.ollama.timeout'] = v
    _row(tab, 'postprocess.ollama.timeout', 'Ollama timeout (s)',
         lambda p: _entry(p, v), row_index)


# Flatten the {dotted.path: tkinter.Var} dict back into ConfigManager updates.
# Two-level paths (e.g. "whisper.model") become direct update_user_setting calls.
# Three-level paths (e.g. "postprocess.ollama.enabled") get batched per parent
# so we issue a single update per nested dict, preserving sibling keys.
def _save_all(cm, vars_):
    pending_nested = {}

    for path, var in vars_.items():
        raw = var.get()
        value = _coerce(var, raw, path)
        parts = path.split('.')

        if len(parts) == 2:
            try:
                cm.update_user_setting(parts[0], parts[1], value)
            except Exception as e:
                logger.warning(f"Could not save {path}: {e}")

        elif len(parts) == 3:
            key = (parts[0], parts[1])
            if key not in pending_nested:
                parent = cm.config.get(parts[0], {})
                pending_nested[key] = dict(parent.get(parts[1]) or {})
            pending_nested[key][parts[2]] = value

    for (section, sub_key), sub_dict in pending_nested.items():
        try:
            cm.update_user_setting(section, sub_key, sub_dict)
        except Exception as e:
            logger.warning(f"Could not save {section}.{sub_key}: {e}")


# Settings whose values are genuinely numeric. ONLY these are int/float-coerced;
# everything else stays a string so free-text fields (initial_prompt, hotkeys,
# ollama model/endpoint) aren't silently turned into numbers when they happen to
# look numeric — e.g. initial_prompt "2024" must stay the string "2024".
_NUMERIC_PATHS = {
    'whisper.beam_size',
    'audio.max_duration',
    'audio.noise_suppression.strength',
    'vad.vad_silence_timeout_seconds',
    'postprocess.ollama.timeout',
}


# Turn the string-based Tkinter value back into the right Python type, keyed by
# the setting's path so coercion is by declared type, not by guessing from the
# string contents.
def _coerce(var, raw, path):
    import tkinter as tk
    if isinstance(var, tk.BooleanVar):
        return bool(raw)
    if path in _NUMERIC_PATHS:
        try:
            return int(raw)
        except (ValueError, TypeError):
            pass
        try:
            return float(raw)
        except (ValueError, TypeError):
            pass
    return raw
