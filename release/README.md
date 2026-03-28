## Release Bundle Notes

This folder documents the release-facing TUI integration for `netaudio`.

### Public commands

- CLI: `netaudio`
- TUI: `netaudio-tui`

### Key release files

- Packaged TUI module:
  - `packages/netaudio/src/netaudio/network_audio_tui.py`
- TUI version file (packaged):
  - `packages/netaudio/src/netaudio/TUI_VERSION`
- Package metadata:
  - `packages/netaudio/pyproject.toml`
- User documentation:
  - `packages/netaudio/README.md`

### Local dev launcher (fallback)

- `../run_v16.sh`
- Launches `../network_audio_tui.py`

### Minimal user setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install netaudio
netaudio-tui
```
