# history_window.py
# Searchable browser over `transcripts.jsonl`. Launched by `parley --history`
# or the tray "Transcript history..." item (both arrive here as a dedicated
# subprocess, so the mainloop blocks the calling thread by design — a spawned
# daemon thread would die the moment the CLI wrapper exits, making the tray
# click look dead). Pure Tkinter, no external deps. Filters rows live as the
# user types, with a click-to-copy action. An empty log still opens the window
# with a friendly hint instead of silently showing nothing.

import logging
import threading

logger = logging.getLogger(__name__)

# Singleton — re-opening the window just raises the existing one.
_lock = threading.Lock()
_instance = None


# Public entry point. Builds the window and blocks on its mainloop until the
# user closes it. The window manages its own lifecycle.
def show_history():
    global _instance
    with _lock:
        try:
            if _instance and _instance.winfo_exists():
                _instance.lift()
                _instance.focus_force()
                return
        except Exception:
            pass

    try:
        import tkinter as tk
        import pyperclip
    except ImportError:
        logger.warning("Tkinter not available for history window")
        return

    from . import theme
    from .theme import px, GAP_S, GAP_M

    from .transcript_log import load_transcripts
    all_entries = load_transcripts()

    theme.ensure_dpi_aware()
    root = tk.Tk()
    with _lock:
        _instance = root

    # Reset the singleton on close so a later open creates a fresh root
    # instead of probing a destroyed one.
    def _clear_ref():
        global _instance
        with _lock:
            _instance = None
    root.protocol("WM_DELETE_WINDOW", lambda: (_clear_ref(), root.destroy()))

    pal = theme.init_window(root, "Transcript History — Parley")
    root.geometry(f"{px(680)}x{px(520)}")
    root.resizable(True, True)

    # ── search bar ──
    top = tk.Frame(root, bg=pal['bg'])
    top.pack(fill='x', padx=px(GAP_M), pady=(px(GAP_M), px(4)))
    theme.caption(top, "Search", pal).pack(side='left')
    search_var = tk.StringVar()
    search_entry = theme.entry(top, textvariable=search_var, pal=pal)
    search_entry.pack(side='left', fill='x', expand=True, padx=(px(GAP_S), 0), ipady=px(4))
    search_entry.focus_set()

    # ── list card (or the empty-state hint when there is nothing to list) ──
    mid = theme.card(root, pal)
    mid.pack(fill='both', expand=True, padx=px(GAP_M), pady=px(4))
    list_frame = tk.Frame(mid.inner, bg=pal['bg_raised'])
    list_frame.pack(fill='both', expand=True, padx=px(4), pady=px(4))

    listbox = tk.Listbox(list_frame,
                         bg=pal['bg_raised'], fg=pal['fg'],
                         selectbackground=pal['selection'],
                         selectforeground=pal['fg'],
                         relief='flat', bd=0,
                         highlightthickness=0,
                         font=theme.font(9, mono=True),
                         activestyle='none')
    scroll = theme.scrollbar(list_frame, listbox.yview, pal)
    listbox.configure(yscrollcommand=scroll.set)

    if all_entries:
        scroll.pack(side='right', fill='y')
        listbox.pack(side='left', fill='both', expand=True)
    else:
        # A blank list looked broken — say what to do instead.
        tk.Label(list_frame, text="No transcripts yet — hold your hotkey and speak.",
                 bg=pal['bg_raised'], fg=pal['fg_dim'],
                 font=theme.font(10)).pack(expand=True)

    # ── preview pane ──
    preview_card = theme.card(root, pal)
    preview_card.pack(fill='x', padx=px(GAP_M), pady=(0, px(4)))
    preview_var = tk.StringVar(value='')
    preview = tk.Label(preview_card.inner, textvariable=preview_var,
                       bg=pal['bg_raised'], fg=pal['fg_dim'],
                       font=theme.font(9), anchor='w',
                       wraplength=px(600), justify='left',
                       padx=px(GAP_S), pady=px(4))
    preview.pack(fill='x')

    # ── status + buttons ──
    status_var = tk.StringVar()
    tk.Label(root, textvariable=status_var, bg=pal['bg'], fg=pal['fg_dim'],
             anchor='w', font=theme.font(9)).pack(fill='x', padx=px(GAP_M))

    btns = tk.Frame(root, bg=pal['bg'])
    btns.pack(fill='x', padx=px(GAP_M), pady=(px(4), px(GAP_M)))

    visible = []

    def copy_selected():
        try:
            idx = listbox.curselection()[0]
            text = visible[idx].get('text', '')
            pyperclip.copy(text)
            status_var.set(f'Copied {len(text)} chars ✓')
            root.after(2500, lambda: _update_status())
        except IndexError:
            pass

    theme.button(btns, 'Copy', copy_selected, pal, primary=True).pack(side='left')
    theme.button(btns, 'Close', root.destroy, pal).pack(side='right')

    def _update_status():
        status_var.set(f'{len(visible)} shown  ·  {len(all_entries)} total')

    def _refresh(query=''):
        q = query.lower().strip()
        del visible[:]
        visible.extend(
            e for e in all_entries
            if not q or q in (e.get('text') or '').lower()
        )
        listbox.delete(0, 'end')
        for e in visible:
            ts = (e.get('timestamp') or '')[:16].replace('T', ' ')
            app = e.get('app', '')
            app_hint = f' [{app}]' if app else ''
            text = (e.get('text') or '')
            snippet = text[:72] + ('…' if len(text) > 72 else '')
            listbox.insert('end', f'{ts}{app_hint}  {snippet}')
        _update_status()

    def _on_select(evt):
        try:
            idx = listbox.curselection()[0]
            text = visible[idx].get('text', '')
            preview_var.set(text[:300] + ('…' if len(text) > 300 else ''))
        except IndexError:
            preview_var.set('')

    listbox.bind('<<ListboxSelect>>', _on_select)
    search_var.trace_add('write', lambda *_: _refresh(search_var.get()))

    # Esc / Ctrl+W close the window, same as every other Parley surface.
    root.bind('<Escape>', lambda e: root.destroy())
    root.bind('<Control-w>', lambda e: root.destroy())

    if not all_entries:
        status_var.set('No transcripts yet — start dictating!')
    else:
        _refresh()

    root.mainloop()
    _clear_ref()
