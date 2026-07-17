# Parley UI/UX Redesign — Fable 5 Prompt

## Operator notes (read before running — not part of the prompt)

- **Where to run:** a fresh Claude Code session with the working directory set to the `parley` repo root. Parley's `CLAUDE.md` auto-loads there; this prompt assumes it.
- **Effort:** set effort to `xhigh` — a flagship visual redesign is capability-sensitive work. Expect long turns (minutes each); don't interrupt a turn that looks quiet.
- **Attach the reference image** (the glassmorphic "Whisper" concept screenshot) with your first message if you can. The prompt contains a written distillation of it that is authoritative on its own, so the run still works without the image.
- **If running via the API/SDK instead of Claude Code:** configure fallback to `claude-opus-4-8` on `stop_reason: "refusal"` (safety classifiers can fire on benign work); drop Fable-specific params like `effort` on the fallback call. In Claude Code no fallback model can be configured; if a turn ends with a refusal on this benign work, retry the request, and if it persists run that step via the API with the Opus 4.8 fallback above.
- The agent will stop and wait for you at exactly two moments: **design-direction approval** (end of Phase 0, with live style-tile screenshots) and **the live-test ask at close-out, before the single commit**. Everything else runs autonomously.

---

## The prompt (paste everything below this line)

I'm redesigning the UI/UX of Parley, my local push-to-talk Whisper dictation app for Windows, which I use every day and am about to take public. It works great but looks like a developer tool; I want it to feel like a polished flagship product — very elegant, impressive, something people want to screenshot. With that in mind: restyle Parley's entire UI to the design language specified below, adapted intelligently to what Parley actually is.

### The target design language

I have a reference image (attached if present — a glassmorphic dark concept UI for a Whisper app). Whether or not the image is attached, this written spec is authoritative:

- **Mood:** dark, luminous, premium. Deep midnight-navy scene (background range roughly `#0B1026` → `#16204A`) with translucent panels over it.
- **Glass treatment:** panels read as translucent dark blue with background blur, a ~1px white border at 8–12% opacity, large corner radii (window ~24px, cards ~16–20px, buttons/pills fully rounded), soft shadows for depth layering.
- **Accent system:** one gradient family, violet → blue → cyan (roughly `#7B5CFF` → `#3D7BFF` → `#38C6F4`), used for the active/primary element, the mic orb, the live waveform, focus states, and glows. A small green dot (`#38E08C`-ish) means "model loaded / listening".
- **Type:** geometric humanist sans (Segoe UI Variable fits), sentence case everywhere. Large light-weight display text for titles and timers; medium weight for labels; text at ~92% / ~60% / ~40% white for primary/secondary/tertiary.
- **Signature elements from the reference:**
  - A glowing **microphone orb**: circular, violet-blue radial gradient, concentric dotted ring, soft outer glow — pulses while listening.
  - A symmetric **live waveform** in gradient-colored bars reacting to voice level.
  - A quiet **status line** ("Listening…" / "Transcribing…") and a thin **timer** (`00:00:12`).
  - A **live transcription** area: relaxed-leading light text with a streaming caret.
  - **Glass chips/pills** with icon + label; keycap-styled hint captions ("Press and hold `Space` to talk · Release to transcribe").
  - A compact **floating pill overlay**: mic glyph in a gradient ring, mini waveform, status + timer, close ✕.
- **Motion:** everything breathes — pulsing glow, animated waveform, smooth hover transitions, gentle fade/slide on show/hide. Subtle and cheap (must never stutter or stall the dictation path), and always gated on `theme.reduced_motion()`.

The reference shows a single-window app with a sidebar. Parley is not that — it's a tray-resident, hotkey-driven multi-window app, and that's the right UX for dictation. Do not build a unified main window; translate the design language onto Parley's real surfaces below. The floating pill in the reference's corner is literally Parley's level overlay — that's the hero.

### What Parley actually is (map the design onto this, don't fight it)

All visual truth lives in `src/parley/theme.py` (~325 lines): DARK/LIGHT palette dicts (18 keys — bg/bg_raised/bg_input/bg_hover/border, fg/fg_dim/fg_faint, accent trio + on_accent, error/warn/info, wave_live/wave_quiet, selection), `px()` DPI scaling, `font()` (Segoe UI Variable / Cascadia Mono), `GAP_XS…GAP_XL` spacing, `apply_chrome()`/`init_window()` (DWM dark title bar attr 20 + Win11 rounded corners attr 33 via a generic `_dwm_set()` wrapper), `reduced_motion()`, `acrylic_tint()` (AABBGGRR), and a flat widget kit: `button() / entry() / card() / key_chip() / heading() / caption()`. There are currently **no** radius tokens, no gradient/glow/canvas helpers, no shadow tokens, no checkbox or scrollbar in the kit. Current identity is "green ink on dark paper" (dark accent `#4cc38a`).

The surfaces, in order of how much they matter:

1. **`level_overlay.py` — the hero, on screen during every dictation.** Frameless always-on-top 356×48 pill: scrolling waveform canvas + status/streaming text. Already has real Win11 acrylic blur-behind (SetWindowCompositionAttribute ACCENTPOLICY) + color-key transparency + DWM rounded corners — but that DWM code is inlined here (~line 338) instead of living in theme.py. Quirks to fix (sanctioned below): streaming text hard-truncates to ~31 chars; no show/hide animation. Known but out of scope: the six fixed positions computed on the primary monitor only (no multi-monitor work this pass).
2. **`system_tray.py` — ambient, always visible.** pystray; consumes zero theme.py; 16×16 solid-color PIL fallback icons hardcode non-palette RGB; 15+ item menu; recording tooltip repaints a unicode level bar every 150 ms. The menu itself is OS-native (leave it), but icons and menu organization/naming are in scope.
3. **`fallback_window.py` — hot path, error case.** "Dictation captured" window when text delivery fails. Text widget has no scrollbar past its 500px height clamp (sanctioned fix below).
4. **`history_window.py`** — Listbox + completely unstyled stock scrollbar, plain text rows, no Esc binding.
5. **`settings_ui.py`** — ttk Notebook (4 tabs) restyled via clam, search bar, native messageboxes that break the theme (sanctioned fix below), aggressive `-topmost`. Its free-text numeric entries stay free-text this pass — restyle only, no new input controls or validation.
6. **`cheat_sheet.py`** — hotkey list; bare `bg_raised` rows (inconsistent with `card()`); **no scrolling — transform lists overflow and clip**.
7. **`dictionary.py` add-word dialog** — 400×195. It shows no word list; that stays as-is this pass (restyle only, no new capability).
8. **`first_run.py`** — one-time welcome; fixed 560×480; hand-styled checkbox (a second checkbox style, distinct from settings' ttk one).

Launch model you must respect: there is **no shared Tk root**. Settings and history run as separate subprocesses (`python -m parley.main --settings/--history`); overlay, first-run, cheat sheet, fallback, and add-word each create their own `tk.Tk()` on a daemon thread inside the main process. Each surface resolves the palette at build time.

Known cross-surface inconsistencies worth erasing while you're in there: two different checkbox renderings (settings' clam-styled ttk one vs first_run's hand-colored tk one); four different "list row" treatments; Esc closes some windows but not history; `-topmost` on windows that shouldn't fight window management; "restart Parley to apply" surfaced as bare text in three different ways.

### Hard constraints

- **Baselines that must never regress** (from CLAUDE.md): full test suite green; warm dictation latency ≤ ~140 ms median (`python tools/benchmark_latency.py`); `--doctor` and `--selftest` exit 0; the pyapp exe still builds and launches. The overlay sits on the hot path — its animation loops must not add per-utterance latency or main-thread stalls.
- **Theme single-source rule:** every color/font/spacing/radius flows through `theme.py`. Never hardcode hex in a window. Absorb `level_overlay.py`'s inline DWM/acrylic code into theme.py as part of this work.
- **Stay on Tkinter + ctypes. No new GUI dependencies.** The exe is a tiny pyapp Rust bootstrapper that pip-installs at first launch; PySide6/pywebview would balloon the installed footprint and first-launch install for no need — the acrylic/DWM path already proven in the overlay gets us the glass look. Extending `_dwm_set()` with `DWMWA_SYSTEMBACKDROP_TYPE` (attr 38) for Mica/acrylic window backdrops is in bounds. Everything must degrade gracefully on Windows 10 (the project docs promise Win10+): try/except no-op, flat dark fallback.
- **What "glass" means per surface — don't fight Tkinter:** real blur exists only at the window level. The overlay keeps its ACCENTPOLICY acrylic; titled windows may use attr-38 Mica/acrylic backdrops. Cards *inside* a window are simulated glass — opaque palette fills, 1px low-alpha-look borders, canvas-painted radii. Tkinter has no per-widget transparency or within-window blur; do not attempt layered-alpha or region hacks. The ~24px window radius applies only to frameless color-key windows like the overlay; titled windows get standard DWM rounding.
- **Preserve advertised behavior:** dark **and** light themes (`palette()` auto-selects from the OS; `PARLEY_THEME` override) — design a light twin of the new language, don't drop it; per-monitor DPI awareness via `px()`; `reduced_motion()` gates all animation; keyboard navigability and focus rings.
- **Don't break the thin test contract:** tests only import-check UI modules and call `settings_ui.reset_settings_preserving_hotwords`, `settings_ui._coerce`, and `first_run`'s flag functions — keep those names/signatures. Tests give zero visual safety net, which is why verification below is screenshot-based.
- **Sanctioned UX fixes (this exact list, nothing more):** cheat-sheet scrolling; history Esc binding + styled scrollbar; overlay streaming-text truncation + show/hide animation polish; fallback-window scrollbar; themed replacements for settings' native messageboxes; consistent Esc/topmost behavior across windows; a unified restart-pending affordance; tray icon palette + menu grouping/naming.
- **Out of scope:** a unified main window; STT/engine logic; config schema changes; multi-monitor overlay positioning; a word list in the dictionary dialog; new input controls (sliders/spinboxes) in settings; anything else not in the sanctioned list.

### Phase 0 — direction proposal (checkpoint)

Read `theme.py` and the overlay end-to-end first. Then make one decision explicit before touching anything else: the new palette. The reference's midnight-navy + violet/blue/cyan gradient family replaces "green ink on dark paper" — propose the actual token values (a full DARK dict, its LIGHT twin, plus the new radius/glow/gradient tokens you'll add), and say what the green identity loses or keeps (e.g. keep green only as the "listening" status dot, per the reference).

Build a **live style tile**: a small throwaway window (`.temp/style_tile.py`) rendered with the new theme — glass card over a backdrop, gradient mic orb with glow, waveform strip, primary/default/danger buttons, entry with focus ring, key chip, checkbox, scrollbar, chip/pill. Make the script self-capturing so it runs unattended: after `update()`, compute its own bbox from `winfo_rootx/rooty/width/height`, `ImageGrab.grab(bbox)` (Pillow is already a dependency) to `.temp/style_tile_{dark,light}.png`, then exit. Run it twice — `PARLEY_THEME=dark` and `PARLEY_THEME=light` — and show me both images with the palette table and your one recommended direction. **Stop there and wait for my approval before implementing.** If I ask for tweaks, iterate on the style tile, not on the app.

### Implementation order (after approval)

1. **`theme.py` foundation:** new palettes; radius/elevation/glow/gradient tokens; canvas helpers (rounded-rect fill, vertical/radial gradient, glow layering); kit upgrades — rounded canvas-based `button()`, `checkbox()`, styled scrollbar, `chip()`, `orb()`/waveform helpers as needed; `apply_chrome()` grows the backdrop option; overlay's acrylic/ACCENTPOLICY code moves here.
2. **Hero surfaces:** `level_overlay.py` (pill redesign: gradient waveform, orb/status treatment, fade/slide show-hide gated on reduced-motion, smarter streaming-text handling), then `fallback_window.py`.
3. **Occasional surfaces:** `history_window.py`, `settings_ui.py`, `cheat_sheet.py`, `dictionary.py` dialog, `first_run.py` — erasing the inconsistency list as you go.
4. **Tray:** palette-derived state icons, menu grouping/naming pass.
5. **Close-out:** regenerate all five README screenshots (`docs/screenshots/{overlay,settings,history,cheat_sheet,first_run}-dark.png`), update CHANGELOG, run the full baseline suite (tests, latency benchmark, doctor, selftest), and ask me for the live test before any commit.

Make no commits during steps 1–4 — keep everything in the working tree. The single live-test checkpoint at close-out covers the whole change (this is how CLAUDE.md's ask-before-commit rule applies to this run).

### Verification

After each completed surface, launch it for real (`parley.cmd`, or `python -m parley.main --settings` / `--history` / `--cheat-sheet`; the overlay via a short driver script in `.temp/`), screenshot it with the same self-capture pattern as the style tile, and compare against the design spec — in both dark and light via `PARLEY_THEME`. If any attached monitor already runs at non-100% scaling, verify on it; do not change display scaling yourself — if none is available, note that in the surface report and move on. Check `reduced_motion()` behavior by monkeypatching `theme.reduced_motion` to return `True` in a driver script (do not change OS accessibility settings) and confirming every animation you added goes still. Run the test suite after each surface, and the latency benchmark after any overlay change.

### Working rules

When you have enough information to act, act. Do not re-derive facts already established in the conversation, re-litigate a decision the user has already made, or narrate options you will not pursue in user-facing messages. If you are weighing a choice, give a recommendation, not an exhaustive survey. This does not apply to thinking blocks.

Don't add features, refactor, or introduce abstractions beyond what the task requires. A bug fix doesn't need surrounding cleanup and a one-shot operation usually doesn't need a helper. Don't design for hypothetical future requirements: do the simplest thing that works well. Avoid premature abstraction and half-finished implementations. Don't add error handling, fallbacks, or validation for scenarios that cannot happen. Trust internal code and framework guarantees. Only validate at system boundaries (user input, external APIs). Don't use feature flags or backwards-compatibility shims when you can just change the code.

Pause for the user only when the work genuinely requires them: a destructive or irreversible action, a real scope change, or input that only they can provide. If you hit one of these, ask and end the turn, rather than ending on a promise.

Before reporting progress, audit each claim against a tool result from this session. Only report work you can point to evidence for; if something is not yet verified, say so explicitly. Report outcomes faithfully: if tests fail, say so with the output; if a step was skipped, say that; when something is done and verified, state it plainly without hedging.

Delegate independent subtasks to subagents and keep working while they run. Intervene if a subagent goes off track or is missing relevant context.

Establish a method for checking your own work at an interval of one completed surface as you build. Run this every one completed surface, verifying your work with subagents against the specification.

Terse shorthand is fine between tool calls (that's you thinking out loud, and brevity there is good). Your final summary is different: it's for a reader who didn't see any of that.

If you've been working for a while without the user watching (overnight, across many tool calls, since they last spoke), your final message is their first look at any of it. Write it as a re-grounding, not a continuation of your working thread: the outcome first, then the one or two things you need from them, each explained as if new. The vocabulary you built up while working is yours, not theirs; leave it behind unless you re-introduce it.

When you write the summary at the end, drop the working shorthand. Write complete sentences. Spell out terms. Don't use arrow chains, hyphen-stacked compounds, or labels you made up earlier. When you mention files, commits, flags, or other identifiers, give each one its own plain-language clause. Open with the outcome: one sentence on what happened or what you found. Then the supporting detail. If you have to choose between short and clear, choose clear.

### Success criteria

- Side-by-side, every surface reads as the same product as the reference image's design language — glass, gradient accent family, rounded geometry, luminous dark palette — while remaining honest Tkinter (no fake screenshots, no mockups shipped as product).
- The level overlay is screenshot-worthy: gradient waveform, glowing treatment, smooth show/hide, no streaming-text hard-clipping at 31 chars.
- Zero hardcoded hex outside `theme.py`; the DWM/acrylic leak in the overlay is gone; checkboxes, list rows, scrollbars, and Esc behavior are each consistent across all surfaces.
- Dark and light both look intentional; Windows 10 falls back gracefully; reduced-motion disables every animation you added.
- All four baselines pass, the five README screenshots are regenerated, and I've been asked for a live test before anything is committed.
