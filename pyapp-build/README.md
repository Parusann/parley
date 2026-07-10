# Building `parley.exe`

Builds the standalone Windows executables with [pyapp](https://github.com/ofek/pyapp), a Rust wrapper that bootstraps a private Python and pip-installs the app at first launch. The pyapp source is pinned to an exact version and SHA-256-verified by the setup script.

## One-time setup

```powershell
.\pyapp-build\setup-build-env.ps1
```

The setup script:
- Checks for Rust (offers to install via rustup if missing — one-time ~600 MB)
- Downloads, verifies, and extracts the pinned pyapp source to `pyapp-build\pyapp-source\`
- Creates `pyapp-build\build-config.json` from defaults

Re-open the terminal after installing Rust so `cargo` lands on PATH.

## Build

```powershell
.\pyapp-build\build-pyapp.ps1
```

`-Clean` forces a full Rust rebuild.

Produces two executables in `dist\`:
- `parley.exe` — console (visible terminal)
- `parley-hideable.exe` — GUI subsystem (for `console.start_hidden` / minimize-to-tray)

## How it works

At first launch, `parley.exe` runs the embedded pyapp logic:
1. Creates a private venv under `%LOCALAPPDATA%\pyapp\data\parley\<hash>\<version>\`
2. Runs `pip install parley==<your version>` into that venv
3. Spawns the actual Python entry point from that venv

So the user never needs Python installed system-wide — they just double-click the exe.

## Two distribution flows

**A. PyPI (once published):**
- Publish `parley` to PyPI
- Build the exe with the default setup flow
- Distribute the exe; first launch pulls from PyPI

**B. Bundled local wheel (for testing before a PyPI release):**
- Run setup with `-UseLocalWheel`: it builds a wheel and prints the `PYAPP_PROJECT_PATH` env var to set
- Run `build-pyapp.ps1` with that env var set
- The resulting exe contains the wheel — works without PyPI

The release CI (`.github/workflows/release.yml`) uses flow B automatically.

## Troubleshooting

**`cargo: command not found`** — re-open the terminal after `rustup-init`.

**`pip install parley` fails at first launch** — the version in `pyproject.toml` isn't on PyPI yet. Either publish, or use the local-wheel flow.

**Slow first launch (~30 s)** — normal: pyapp is downloading dependencies. Subsequent launches reuse the venv.
