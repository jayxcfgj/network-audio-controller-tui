### Description

`netaudio` is a Python CLI for controlling Audinate Dante network audio devices.
This project now also ships a Textual-based TUI.

Use in test environments first. Device settings and routing changes can affect live audio.

### Installation

Install from package index:

```bash
pip install netaudio
```

Or from a clone/workspace:

```bash
uv sync
```

### Usage

CLI:

```bash
netaudio --help
```

TUI:

```bash
netaudio-tui
```

From a clone with uv:

```bash
uv run netaudio
uv run netaudio-tui
```

### Quick Start (venv)

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install netaudio
netaudio-tui
```

### Notes

- TUI version label is managed separately from CLI command output.
- Some UI areas are intentionally marked as mockup if upstream CLI support is missing.

### Documentation

- [Examples](https://github.com/chris-ritsen/network-audio-controller/wiki/Examples)
- [Technical details](https://github.com/chris-ritsen/network-audio-controller/wiki/Technical-details)
- [Testing](https://github.com/chris-ritsen/network-audio-controller/wiki/Testing)
