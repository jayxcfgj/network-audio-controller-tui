# TUI application

from textual.app import App, ComposeResult
from textual.widgets import DataTable, Button, Input, Select, Label, Header, TabbedContent, TabPane, Static
from textual.containers import Vertical, Horizontal, Grid, ItemGrid
from textual.screen import ModalScreen
from textual import on, events
from textual.coordinate import Coordinate
from rich.text import Text
import re
from pathlib import Path
import asyncio
import subprocess
import json
import time
from concurrent.futures import ThreadPoolExecutor

APP_NAME = "TUI for Network Audio Controller CLI"
APP_VERSION_FALLBACK = "0.1.0"
TUI_VERSION_FILE = Path(__file__).resolve().with_name("TUI_VERSION")

def get_tui_version() -> str:
    try:
        content = TUI_VERSION_FILE.read_text(encoding="utf-8").strip()
        return content or APP_VERSION_FALLBACK
    except Exception:
        return APP_VERSION_FALLBACK

APP_VERSION = get_tui_version()
APP_TITLE = f"TUI v{APP_VERSION} for Network Audio Controller CLI"

def _normalize_version_text(text: str) -> str:
    match = re.search(r"\b\d+\.\d+\.\d+\b", text)
    if match:
        return match.group(0)
    return text.strip()

def get_netaudio_cli_version() -> str:
    commands = [
        ["netaudio", "--version"],
        ["netaudio", "version"],
        ["netaudio", "-V"],
    ]
    for cmd in commands:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except Exception:
            continue
        output = (result.stdout or result.stderr or "").strip()
        if output:
            first_line = output.splitlines()[0].strip()
            return _normalize_version_text(first_line)
    return "not found"


class SingleClickDataTable(DataTable):
    """DataTable variant with single-click selection and basic header resize."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._resize_column_index = None
        self._resize_start_x = 0.0
        self._resize_start_render_width = 0
        self._resize_hit_tolerance = 1
        self._min_content_width_by_column = {0: 12, 1: 8}

    def _get_resize_handle_positions(self):
        positions = []
        x = float(self._row_label_column_width)
        for column_index, column in enumerate(self.ordered_columns[:2]):
            x += column.get_render_width(self)
            positions.append((column_index, x))
        return positions

    def _find_resize_column(self, x_pos: float):
        for column_index, boundary_x in self._get_resize_handle_positions():
            if abs(x_pos - boundary_x) <= self._resize_hit_tolerance:
                return column_index
        return None

    def _set_column_render_width(self, column_index: int, render_width: int) -> None:
        if column_index >= len(self.ordered_columns):
            return
        column = self.ordered_columns[column_index]
        min_content_width = self._min_content_width_by_column.get(column_index, 8)
        content_width = max(min_content_width, int(render_width) - (2 * self.cell_padding))
        if not column.auto_width and int(column.width) == content_width:
            return

        column.auto_width = False
        column.width = content_width
        if column.content_width < content_width:
            column.content_width = content_width

        self._clear_caches()
        self._require_update_dimensions = True
        self.check_idle()
        self._update_count += 1
        self.refresh(layout=True)

        callback = getattr(self.app, "on_matrix_column_width_changed", None)
        if callable(callback):
            callback(column_index, content_width)

    def _on_mouse_down(self, event: events.MouseDown) -> None:
        meta = event.style.meta if event.style else {}
        if (
            event.button == 1
            and meta.get("row") == -1
            and self.show_header
        ):
            column_index = self._find_resize_column(event.x)
            if column_index in (0, 1):
                self._resize_column_index = column_index
                self._resize_start_x = event.x
                self._resize_start_render_width = self.ordered_columns[
                    column_index
                ].get_render_width(self)
                self.capture_mouse()
                event.stop()
                return
        super()._on_mouse_down(event)

    def _on_mouse_move(self, event: events.MouseMove):
        if self._resize_column_index is not None:
            delta = int(round(event.x - self._resize_start_x))
            min_render_width = self._min_content_width_by_column.get(
                self._resize_column_index, 8
            ) + (2 * self.cell_padding)
            target_render_width = max(
                min_render_width,
                self._resize_start_render_width + delta,
            )
            self._set_column_render_width(self._resize_column_index, target_render_width)
            event.stop()
            return
        super()._on_mouse_move(event)

    def _on_mouse_up(self, event: events.MouseUp) -> None:
        if self._resize_column_index is not None:
            self._resize_column_index = None
            self.release_mouse()
            event.stop()
            return
        super()._on_mouse_up(event)

    async def _on_click(self, event: events.Click) -> None:
        self._set_hover_cursor(True)
        meta = event.style.meta if event.style else {}
        if "row" not in meta or "column" not in meta:
            return
        if self.cursor_type != "row" and meta.get("out_of_bounds", False):
            return

        row_index = meta["row"]
        column_index = meta["column"]
        if row_index == -1 and self._find_resize_column(event.x) in (0, 1):
            event.stop()
            return
        is_header_click = self.show_header and row_index == -1
        is_row_label_click = self.show_row_labels and column_index == -1
        if is_header_click:
            column = self.ordered_columns[column_index]
            message = DataTable.HeaderSelected(
                self, column.key, column_index, label=column.label
            )
            self.post_message(message)
        elif is_row_label_click:
            row = self.ordered_rows[row_index]
            message = DataTable.RowLabelSelected(
                self, row.key, row_index, label=row.label
            )
            self.post_message(message)
        elif self.show_cursor and self.cursor_type != "none":
            self.cursor_coordinate = Coordinate(row_index, column_index)
            self._post_selected_message()
            self._scroll_cursor_into_view(animate=True)
            event.stop()

    def watch_scroll_x(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_x(old_value, new_value)
        callback = getattr(self.app, "on_matrix_scroll_changed", None)
        if callable(callback):
            callback(new_value)


class SettingsScreen(ModalScreen):
    _config_flag_support_cache = {}

    DEFAULT_CSS = """
    SettingsScreen {
        align: center middle;
    }
    #settings-modal {
        width: 92%;
        max-width: 140;
        height: 90%;
        min-height: 12;
        border: thick #333;
        background: $panel;
        padding: 1 2;
        overflow: hidden;
    }
    #select-container {
        width: 100%;
        align: left middle;
        height: auto;
        margin-bottom: 1;
    }
    #select-container .device-label {
        width: auto;
        margin-right: 1;
    }
    #device-select {
        width: 1fr;
        max-width: 100%;
    }
    #tab-container {
        width: 100%;
        height: 1fr;
        min-height: 0;
    }
    #tab-container ContentSwitcher {
        height: 1fr;
        min-height: 0;
    }
    #tab-container TabPane {
        height: 1fr;
        min-height: 0;
        overflow-y: auto;
        overflow-x: hidden;
        padding-right: 1;
        padding-bottom: 2;
    }
    #tab-info {
    layout: vertical;
    height: 1fr;
    min-height: 0;
    overflow-y: auto;
    overflow-x: hidden;
    padding-right: 1;
    padding-bottom: 2;
    }

    #info-content {
    width: 100%;
    }

    #tab-info .config-buttons {
    dock: bottom;
    height: auto;
    min-height: 3;
    margin-top: 1;
    background: $panel;
    }
    #config-tabs {
        height: 1fr;
        min-height: 0;
    }
    #config-tabs ContentSwitcher {
        height: 1fr;
        min-height: 0;
    }
    #config-tabs TabPane {
        layout: vertical;
        height: 1fr;
        min-height: 0;
        overflow-y: auto;
        overflow-x: hidden;
        padding-right: 1;
        padding-bottom: 2;
    }
    #config-tabs .config-buttons {
        dock: bottom;
        height: auto;
        min-height: 3;
        margin-top: 1;
        background: $panel;
    }
    """
    def __init__(self, selected_device=None):
        super().__init__()
        self.selected_device = selected_device
        self.no_device_value = "__no_device__"
        self.redundancy_supported = False
        self.mockup_hints = {
            "config-pullup": "Sample rate pullup is not implemented in the CLI yet.",
            "config-multicast-prefix": "Multicast prefix is not supported by the CLI yet.",
            "config-ip": "Network configuration is not implemented in the CLI yet.",
            "config-netmask": "Network configuration is not implemented in the CLI yet.",
            "config-gateway": "Network configuration is not implemented in the CLI yet.",
            "config-dns": "Network configuration is not implemented in the CLI yet.",
            "config-redundancy": "Dante redundancy is not implemented in the CLI yet.",
            "latency-plot": "Latency histogram is not implemented in the CLI yet.",
        }
        self.original_values = {
            "name": "",
            "sample_rate": None,
            "encoding": None,
            "latency": None,
            "redundancy": None,
            "aes67": False,
            "multicast_prefix": "",
        }

    @classmethod
    def supports_config_flag(cls, flag: str) -> bool:
        cached = cls._config_flag_support_cache.get(flag)
        if cached is not None:
            return cached
        try:
            result = subprocess.run(
                ["netaudio", "config", "--help"],
                capture_output=True,
                text=True,
                check=False,
            )
            help_text = f"{result.stdout}\n{result.stderr}"
            supported = flag in help_text
        except Exception:
            supported = False
        cls._config_flag_support_cache[flag] = supported
        return supported

    def _build_device_options(self, devices: list[str], empty_label: str) -> list[tuple[str, str]]:
        seen = set()
        normalized = []
        for name in devices:
            if not isinstance(name, str):
                continue
            value = name.strip()
            if not value or value in seen:
                continue
            seen.add(value)
            normalized.append(value)

        selected = self.selected_device
        if (
            isinstance(selected, str)
            and selected
            and selected != self.no_device_value
            and selected not in seen
        ):
            normalized.insert(0, selected)

        if not normalized:
            return [(empty_label, self.no_device_value)]
        return [(name, name) for name in normalized]

    def _apply_device_options(self, devices: list[str], empty_label: str = "No Device") -> None:
        options = self._build_device_options(devices, empty_label=empty_label)
        values = [value for _, value in options]

        selected = None
        if self.selected_device in values:
            selected = self.selected_device
        else:
            current_value = self.device_select.value
            if current_value in values:
                selected = current_value

        if selected is None and options:
            selected = options[0][1]

        self.device_select.set_options(options)
        if selected is not None:
            try:
                self.device_select.value = selected
            except Exception:
                pass

    async def _refresh_device_options_async(self) -> None:
        devices = await asyncio.to_thread(
            self.app.query_devices,
            force_refresh=False,
            cached_only=False,
        )
        if not self.is_mounted:
            return
        self._apply_device_options(devices, empty_label="No Device")

    @staticmethod
    def _parse_json_output(raw_text: str):
        text = raw_text.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            for index, ch in enumerate(text):
                if ch in "[{":
                    try:
                        return json.loads(text[index:])
                    except json.JSONDecodeError:
                        break
            return None

    def request_device_info_load(self) -> None:
        if not self.selected_device or self.selected_device == self.no_device_value:
            self.update_info_display({})
            return

        self.query_one("#info-content", Static).update("[bold]Loading device info...[/bold]")
        self.run_worker(
            self._load_device_info_async(self.selected_device),
            group="settings-device-info",
            exclusive=True,
        )

    async def _load_device_info_async(self, device_name: str) -> None:
        info, error_text = await asyncio.to_thread(self._fetch_device_info_sync, device_name)
        if not self.is_mounted or self.selected_device != device_name:
            return
        if error_text is not None:
            self.query_one("#info-content", Static).update(f"Failure: {error_text}")
            return
        self.update_info_display(info)

    def _fetch_device_info_sync(self, device_name: str) -> tuple[dict, str | None]:
        try:
            result = subprocess.run(
                ["netaudio", "--json", "--name", device_name, "device", "list"],
                capture_output=True,
                text=True,
            )
            output = (result.stdout or "").strip()
            if not output:
                return {}, None
            data = self._parse_json_output(output)
            if isinstance(data, dict) and data:
                first_device = next(iter(data.values()))
                if isinstance(first_device, dict):
                    return first_device, None
            return {}, None
        except Exception as error:
            return {}, str(error)

    async def _load_redundancy_support_async(self) -> None:
        supported = await asyncio.to_thread(self.supports_config_flag, "--set-redundancy")
        if not self.is_mounted:
            return
        self.redundancy_supported = supported
        self.redundancy_select.disabled = not supported

    def compose(self) -> ComposeResult:
        devices = self.app.query_devices(cached_only=True)
        device_options = self._build_device_options(devices, empty_label="Loading devices...")
        device_values = [opt[1] for opt in device_options]
        initial_value = (
            self.selected_device
            if self.selected_device in device_values
            else device_options[0][1]
        )

        self.device_select = Select(
            device_options,
            id="device-select",
            prompt=" Select Device ",
            value=initial_value,
            allow_blank=False,
        )

        with Vertical(id="settings-modal"):
            with Horizontal(id="select-container"):
                yield Label("Device:", classes="device-label")
                yield self.device_select

            with TabbedContent(id="tab-container"):
                with TabPane("Info", id="tab-info"):
                    yield Static(
                        "[bold]Device:[/bold]\n"
                        "  - Name: -\n"
                        "  - Lock Status: -\n\n"
                        "[bold]Manufacturer:[/bold]\n"
                        "  - Manufacturer: -\n"
                        "  - Model: -\n"
                        "  - Version: -\n\n"
                        "[bold]Dante:[/bold]\n"
                        "  - Model: -\n"
                        "  - Firmware: -\n"
                        "  - Hardware: -\n"
                        "  - ROM/Boot: -\n\n"
                        "[bold]Clock Sync:[/bold]\n"
                        "  - Mute: -\n"
                        "  - Sync: -\n"
                        "  - External World Clock: -\n"
                        "  - Preferred: -\n"
                        "  - Offset: - ppm\n\n"
                        "[bold]Network (Primary):[/bold]\n"
                        "  - IP: -\n"
                        "  - MAC: -\n"
                        "  - TX Utilization: -\n"
                        "  - RX Utilization: -\n\n"
                        "[bold]Latency:[/bold]\n"
                        "  - Count: -\n"
                        "  - Duration: -\n"
                        "  - Peak/Average: -\n"
                        "  - Setting: -",
                        id="info-content"
                    )
                    # Buttons
                    yield Horizontal(
                        Button("Cancel", id="config-cancel"),
                        classes="config-buttons"
                        )  

                with TabPane("Config", id="tab-config"):
                    with TabbedContent(id="config-tabs"):
                        with TabPane("General", id="config-device"):
                            #yield Label("[bold]Rename Device[/bold]", classes="section-title")
                            # Rename device
                            self.device_name_input = Input(placeholder="New device name", id="config-device-name")
                            yield self.device_name_input

                            # Sample Rate
                            yield Label("[bold]Sample Rate[/bold]", classes="section-title")
                            self.sample_rate_select = Select(
                                [(str(rate), rate) for rate in [44100, 48000, 88200, 96000, 176400, 192000]],
                                prompt="Sample Rate",
                                id="config-sample-rate"
                            )
                            self.pullup_select = Select(
                            [("44.1k", 44100), ("48k", 48000)],
                            prompt="Sample Rate Pullup",
                            id="config-pullup",
                            classes="mockup-field"
                            )
                            yield self.sample_rate_select
                            yield self.pullup_select

                            # Dante Redundancy
                            yield Label("[bold]Dante Redundancy[/bold]", classes="section-title")
                            self.redundancy_select = Select(
                            [("Primary", "primary"), ("Redundant", "redundant"), ("Switch", "switch")],
                            prompt="Dante Redundancy",
                            id="config-redundancy",
                            classes="mockup-field"
                            )
                            yield self.redundancy_select

                            # Encoding
                            yield Label("[bold]Encoding[/bold]", classes="section-title")
                            self.encoding_select = Select(
                            [("L24", "L24"), ("L16", "L16")],
                            prompt="Encoding",
                            id="config-encoding"
                            )
                            yield self.encoding_select

                            # Latency
                            yield Label("[bold]Latency[/bold]", classes="section-title")
                            self.latency_select = Select(
                            [("0.5", 0.5), ("1", 1), ("2", 2), ("3", 3), ("4", 4), ("5", 5), ("10", 10)],
                            prompt="Latency (ms)",
                            id="config-latency"
                            )
                            yield self.latency_select

                            # Buttons
                            yield Horizontal(
                            Button("Cancel", id="config-cancel"),
                            Button("Save Config", id="config-save"),
                            Button("Clear Config", id="config-clear"),
                            Button("Reboot Device", id="config-reboot"),
                            classes="config-buttons"
                            )  


                        # AES67 Mode
                        with TabPane("AES67", id="aes67"):
                            yield Label("[bold]AES67 Mode[/bold]", classes="section-title")
                            self.aes67_mode_select = Select(
                                [("Enabled", "on"), ("Disabled", "off")],
                                prompt="AES67 Mode",
                                id="config-aes67-mode",
                                allow_blank=False,
                            )
                            yield self.aes67_mode_select

                            # RTP Multicast Prefix
                            yield Label("[bold]RTP Multicast Prefix[/bold]", classes="section-title")
                            self.multicast_prefix = Input(
                                placeholder="239.69",
                                id="config-multicast-prefix",
                                classes="mockup-field",
                            )
                      
                            yield self.multicast_prefix

                            # Buttons
                            yield Horizontal(
                            Button("Cancel", id="config-cancel"),
                            Button("Save Config", id="config-save"),
                            Button("Clear Config", id="config-clear"),
                            Button("Reboot Device", id="config-reboot"),
                            classes="config-buttons"
                            )



                            # IP settings
                        with TabPane("Network", id="network"):
                            yield Label("[bold]Setting network data is not implemented in netaudio yet[/bold]", classes="section-title")
                            self.ip_input = Input(placeholder="IP address", id="config-ip", classes="mockup-field")
                            self.netmask_input = Input(placeholder="netmask", id="config-netmask", classes="mockup-field")
                            self.gateway_input = Input(placeholder="gateway", id="config-gateway", classes="mockup-field")
                            self.dns_input = Input(placeholder="DNS", id="config-dns", classes="mockup-field")
                            yield self.ip_input
                            yield self.netmask_input
                            yield self.gateway_input
                            yield self.dns_input

                            # Buttons
                            yield Horizontal(
                                Button("Cancel", id="config-cancel"),
                                classes="config-buttons"
                            )



                        # Latency histogram (placeholder)
                        with TabPane("Latency Histogram", id="latency"):
                            yield Label("[bold]Packet Latency Histogram[/bold]", classes="section-title")
                            self.latency_plot = Static(
                                "Latency histogram data might be shown here in the future.",
                                id="latency-plot",
                                classes="plot-area mockup-field",
                            )
                            yield self.latency_plot

                            # Buttons
                            yield Horizontal(
                            Button("Cancel", id="config-cancel"),
                            Button("Clear Config", id="config-clear"),
                            Button("Reboot Device", id="config-reboot"),
                            classes="config-buttons"
                            )   

    def on_mount(self) -> None:
        if not self.selected_device:
            selected = self.device_select.value
            if selected != self.no_device_value:
                self.selected_device = selected

        self.redundancy_select.disabled = True
        self.apply_safe_mode_state()

        if self.selected_device and self.selected_device != self.no_device_value:
            self.request_device_info_load()
        else:
            self.update_info_display({})

        self.run_worker(
            self._refresh_device_options_async(),
            group="settings-device-options",
            exclusive=True,
        )
        self.run_worker(
            self._load_redundancy_support_async(),
            group="settings-capabilities",
            exclusive=True,
        )

    def apply_safe_mode_state(self) -> None:
        safe_mode = getattr(self.app, "safe_mode", True)
        for node in self.query("#config-save"):
            if isinstance(node, Button):
                node.disabled = safe_mode
        for node in self.query("#config-reboot"):
            if isinstance(node, Button):
                node.disabled = safe_mode

    def reset_form_inputs(self, use_original: bool = True) -> None:
        if not hasattr(self, "device_name_input"):
            return
        if use_original and isinstance(self.original_values, dict):
            original = self.original_values
        else:
            original = {
                "name": "",
                "sample_rate": None,
                "encoding": None,
                "latency": None,
                "redundancy": None,
                "aes67": False,
                "multicast_prefix": "",
            }

        def _set_select_value(select: Select, value) -> None:
            if value is None:
                select.clear()
                return
            try:
                select.value = value
            except Exception:
                select.clear()

        self.device_name_input.value = original.get("name") or ""
        _set_select_value(self.sample_rate_select, original.get("sample_rate"))
        _set_select_value(self.encoding_select, original.get("encoding"))
        _set_select_value(self.latency_select, original.get("latency"))
        _set_select_value(self.redundancy_select, original.get("redundancy"))
        self.pullup_select.clear()
        _set_select_value(self.aes67_mode_select, "on" if bool(original.get("aes67")) else "off")
        self.multicast_prefix.value = original.get("multicast_prefix") or ""

        self.ip_input.value = ""
        self.netmask_input.value = ""
        self.gateway_input.value = ""
        self.dns_input.value = ""

    def on_button_pressed(self, event):
        if event.button.id == "config-clear":
            has_device = bool(self.selected_device and self.selected_device != self.no_device_value)
            self.reset_form_inputs(use_original=has_device)

        elif event.button.id == "config-reboot":
            if getattr(self.app, "safe_mode", True):
                self.app.notify("Blocked by Safe Mode: reboot device", severity="warning")
                return
            self.app.notify(
                "Reboot command is not available in this netaudio version",
                severity="warning",
            )
        elif event.button.id == "config-cancel":
            self.app.pop_screen()

    def _resolve_mockup_target(self, control):
        node = control
        while node is not None:
            target_id = getattr(node, "id", None)
            if target_id in self.mockup_hints:
                return target_id
            if hasattr(node, "has_class") and node.has_class("mockup-field"):
                return target_id
            node = getattr(node, "parent", None)
        return None

    def on_focus(self, event: events.Focus) -> None:
        control = getattr(event, "control", None)
        target_id = self._resolve_mockup_target(control)
        if target_id:
            message = self.mockup_hints.get(target_id)
            if message:
                self.app.notify(message, severity="warning")

    def on_click(self, event: events.Click) -> None:
        control = getattr(event, "control", None)
        target_id = self._resolve_mockup_target(control)
        if target_id:
            message = self.mockup_hints.get(target_id)
            if message:
                self.app.notify(message, severity="warning")

    def _run_config_command(self, command: list[str], action_label: str) -> bool:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception as error:
            self.app.notify(f"Failure ({action_label}): {error}", severity="error")
            return False

        if result.returncode != 0:
            error_text = (result.stderr or result.stdout or "").strip()
            if not error_text:
                error_text = f"exit status {result.returncode}"
            self.app.notify(f"Failure ({action_label}): {error_text}", severity="error")
            return False

        return True

    @on(Button.Pressed, "#config-save")
    def save_config(self):
        if getattr(self.app, "safe_mode", True):
            self.app.notify("Blocked by Safe Mode: save config", severity="warning")
            return
        device_name = self.selected_device  # <- explicit saving
        if not device_name or device_name == self.no_device_value:
            self.app.notify("No device selected", severity="error")
            return
        
        current = {
            "name": self.device_name_input.value.strip(),
            "sample_rate": self.sample_rate_select.value,
            "encoding": self.encoding_select.value,
            "latency": self.latency_select.value,
            "redundancy": self.redundancy_select.value,
            "aes67": self.aes67_mode_select.value == "on",
            "multicast_prefix": self.multicast_prefix.value,
        }

        original = self.original_values

        # Send only values that changed

        if current["name"] and current["name"] != original["name"]:
            if not self._run_config_command(
                [
                    "netaudio", "--name", device_name,
                    "device", "name", current["name"],
                ],
                "rename device",
            ):
                return

        if current["sample_rate"] != original["sample_rate"]:
            if not self._run_config_command(
                [
                    "netaudio", "--name", device_name,
                    "device", "config", "sample-rate", str(current["sample_rate"]),
                ],
                "set sample rate",
            ):
                return

        if current["encoding"] != original["encoding"]:
            encoding_map = {"L16": "16", "L24": "24", "L32": "32"}
            encoding_value = encoding_map.get(current["encoding"])
            if not encoding_value:
                self.app.notify("Unsupported encoding value", severity="error")
                return
            if not self._run_config_command(
                [
                    "netaudio", "--name", device_name,
                    "device", "config", "encoding", encoding_value,
                ],
                "set encoding",
            ):
                return

        if current["latency"] != original["latency"]:
            if not self._run_config_command(
                [
                    "netaudio", "--name", device_name,
                    "device", "config", "latency", str(current["latency"]),
                ],
                "set latency",
            ):
                return

        if current["redundancy"] != original["redundancy"]:
            if self.redundancy_supported:
                if not self._run_config_command(
                    [
                        "netaudio", "--name", device_name,
                        "device", "config", "redundancy", current["redundancy"],
                    ],
                    "set redundancy",
                ):
                    return
            else:
                self.app.notify(
                    "Dante redundancy setting is not supported by this netaudio version",
                    severity="warning",
                )

        if current["aes67"] != original["aes67"]:
            aes67_target = "on" if current["aes67"] else "off"
            if not self._run_config_command(
                [
                    "netaudio", "--name", device_name,
                    "device", "config", "aes67", aes67_target,
                ],
                f"set AES67 {aes67_target}",
            ):
                return

        if current["multicast_prefix"] != original["multicast_prefix"]:
            self.app.notify(
                "Multicast prefix is not supported by this netaudio version",
                severity="warning",
            )

        self.app.notify("Changes sent", severity="information")   

    def on_select_changed(self, event) -> None:
        if event.select.id != "device-select":
            return
        if event.value == self.no_device_value:
            self.selected_device = None
            self.update_info_display({})
            return
        if not event.value:
            return
        self.selected_device = event.value
        self.request_device_info_load()

    def update_info_display(self, info):
        latency = info.get("latency", {})
        if not isinstance(latency, dict):
            latency = {}
        self.original_values = {
            "name": info.get("name", ""),
            "sample_rate": info.get("sample_rate"),
            "encoding": info.get("encoding", "L24"),
            "latency": latency.get("setting"),
            "redundancy": info.get("redundancy_mode", "primary"),
            "aes67": info.get("aes67_enabled", False),
            "multicast_prefix": info.get("multicast_prefix", "239.69"),
        }
        # Extract data safely
        name = info.get("name", "-")
        is_locked = info.get("is_locked")
        if is_locked is True:
            lock_status = "[red]locked[/red]"
        elif is_locked is False:
            lock_status = "[green]unlocked[/green]"
        else:
            lock_status = "[yellow]unknown[/yellow]"
        manufacturer = info.get("manufacturer_information", {})
        dante = info.get("dante_information", {})
        clock = info.get("clock_synchronization", {})
        services = info.get("services", {})
        arc_properties = {}
        if isinstance(services, dict):
            for service in services.values():
                if not isinstance(service, dict):
                    continue
                service_type = str(service.get("type", ""))
                if service_type == "_netaudio-arc._udp.local.":
                    arc_properties = service.get("properties", {})
                    break
        if not isinstance(arc_properties, dict):
            arc_properties = {}

        network = info.get("network_interfaces", {}).get("Primary", {})
        if not isinstance(network, dict):
            network = {}

        manufacturer_name = manufacturer.get("manufacturer", arc_properties.get("mf", "-"))
        model_name = manufacturer.get("model_name", info.get("model_id", arc_properties.get("model", "-")))
        product_version = manufacturer.get(
            "product_version",
            arc_properties.get("arcp_vers", arc_properties.get("router_vers", "-")),
        )
        dante_model = dante.get("dante_model", info.get("model_id", "-"))
        dante_firmware = dante.get(
            "dante_firmware_version",
            arc_properties.get("arcp_vers", "-"),
        )
        hardware_version = dante.get("hardware_version", arc_properties.get("router_info", "-"))
        rom_boot_version = dante.get("rom_boot_version", arc_properties.get("router_debug", "-"))
        ip_addr = network.get("ip_addr", info.get("ipv4", "-"))
        mac_addr = network.get("mac", info.get("mac_address", "-"))
        tx_utilization = network.get("tx_utilization", "-")
        rx_utilization = network.get("rx_utilization", "-")

        content = (
            f"[bold]Device:[/bold]\n"
            f"  - Name: {name}\n"
            f"  - Lock Status: {lock_status}\n\n"
            f"[bold]Manufacturer:[/bold]\n"
            f"  - Manufacturer: {manufacturer_name}\n"
            f"  - Model: {model_name}\n"
            f"  - Version: {product_version}\n\n"
            f"[bold]Dante:[/bold]\n"
            f"  - Model: {dante_model}\n"
            f"  - Firmware: {dante_firmware}\n"
            f"  - Hardware: {hardware_version}\n"
            f"  - ROM/Boot: {rom_boot_version}\n\n"
            f"[bold]Clock Sync:[/bold]\n"
            f"  - Mute: {clock.get('mute_status', '-')}\n"
            f"  - Sync: {clock.get('sync_status', '-')}\n"
            f"  - External World Clock: {clock.get('external_world_clock', '-')}\n"
            f"  - Preferred: {clock.get('preferred', '-')}\n"
            f"  - Offset: {clock.get('frequency_offset', '-')}\n\n"
            f"[bold]Network (Primary):[/bold]\n"
            f"  - IP: {ip_addr}\n"
            f"  - MAC: {mac_addr}\n"
            f"  - TX Utilization: {tx_utilization}\n"
            f"  - RX Utilization: {rx_utilization}\n\n"
            f"[bold]Latency:[/bold]\n"
            f"  - Count: {latency.get('count', '-')}\n"
            f"  - Duration: {latency.get('duration', '-')}\n"
            f"  - Peak/Average: {latency.get('peak_average', '-')}\n"
            f"  - Setting: {latency.get('setting', '-')}"
        )
        self.query_one("#info-content", Static).update(content)   
        self.reset_form_inputs(use_original=bool(info))
            #self.query_one("#info-content", Label).update(f"failure: {str(e)}")

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.app.pop_screen()   

class ConfirmExit(ModalScreen):
    DEFAULT_CSS = """
    ConfirmExit {
        align: center middle;
    }
    #dialog {
        width: 60;
        height: 11;
        border: thick $background 80%;
        background: $surface;
        padding: 0 1;
        grid-size: 2;
        grid-gutter: 1 2;
        grid-rows: 1fr 3;
    }
    #question {
        column-span: 2;
        content-align: center middle;
    }
    Button {
        width: 100%;
    }
    """

    def compose(self):
        with Grid(id="dialog"):
            yield Label("Really quit?", id="question")
            yield Button("Yes", variant="error", id="yes")
            yield Button("No", variant="primary", id="no")

    def on_button_pressed(self, event):
        if event.button.id == "yes":
            self.app.exit()
        else:
            self.app.pop_screen()


class UnlockSafeModeScreen(ModalScreen[bool]):
    DEFAULT_CSS = """
    UnlockSafeModeScreen {
        align: center middle;
    }
    #unlock-dialog {
        width: 74;
        height: 15;
        border: thick $warning;
        background: $surface;
        padding: 1 2;
    }
    #unlock-buttons Button {
        margin-right: 1;
    }
    """

    def compose(self):
        with Vertical(id="unlock-dialog"):
            yield Label("[b]Safety Lock[/b]")
            yield Label("Safe Mode blocks write actions to Dante devices.")
            yield Label("Type UNLOCK to allow write actions in this session.")
            yield Input(placeholder="Type UNLOCK", id="unlock-input")
            yield Horizontal(
                Button("Cancel", id="unlock-cancel"),
                Button("Unlock", variant="warning", id="unlock-confirm"),
                id="unlock-buttons",
            )

    def on_button_pressed(self, event):
        if event.button.id == "unlock-cancel":
            self.dismiss(False)
            return
        if event.button.id == "unlock-confirm":
            value = self.query_one("#unlock-input", Input).value.strip()
            if value == "UNLOCK":
                self.dismiss(True)
                return
            self.app.notify("Unlock phrase mismatch", severity="error")

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(False)


class StartupWarningScreen(ModalScreen[bool]):
    DEFAULT_CSS = """
    StartupWarningScreen {
        align: center middle;
    }
    #startup-warning-dialog {
        width: 90%;
        max-width: 100;
        height: 85%;
        max-height: 26;
        border: thick $warning;
        background: $surface;
        padding: 1 2;
        overflow: hidden;
    }
    #startup-warning-text {
        width: 100%;
        height: 1fr;
        overflow: auto;
    }
    #startup-warning-buttons {
        align: center middle;
        height: auto;
        margin-top: 1;
    }
    #startup-warning-ack {
        width: auto;
        min-width: 18;
    }
    """

    def compose(self):
        warning_text = (
            "[b]Disclaimer[/b]\n\n"
            "This application can change settings on Dante/network-audio devices. "
            "Improper operation or misconfiguration may cause audio dropouts, "
            "service interruptions, or device/network issues.\n\n"
            "Use at your own risk. This software is provided \"as is\" without warranty. "
            "No liability is accepted for direct or indirect damage to hardware, "
            "software, data, or production environments.\n\n"
            "Use only in an appropriate test environment and validate all changes beforehand."
        )
        with Vertical(id="startup-warning-dialog"):
            yield Label(warning_text, id="startup-warning-text")
            with Horizontal(id="startup-warning-buttons"):
                yield Button("OK, understood", variant="warning", id="startup-warning-ack")

    def on_mount(self) -> None:
        self.query_one("#startup-warning-ack", Button).focus()

    def on_button_pressed(self, event) -> None:
        if event.button.id == "startup-warning-ack":
            self.dismiss(True)

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()


class ManageAES67FlowsScreen(ModalScreen):
    _flow_support_cache = None
    def __init__(self, device_name: str | None):
        super().__init__()
        self.device_name = device_name
        self.no_device_value = "__no_device__"

    def _build_device_options(self, devices: list[str], empty_label: str) -> list[tuple[str, str]]:
        seen = set()
        normalized = []
        for name in devices:
            if not isinstance(name, str):
                continue
            value = name.strip()
            if not value or value in seen:
                continue
            seen.add(value)
            normalized.append(value)

        if self.device_name and self.device_name not in seen:
            normalized.insert(0, self.device_name)

        if not normalized:
            return [(empty_label, self.no_device_value)]
        return [(name, name) for name in normalized]

    def _apply_device_options(self, devices: list[str], empty_label: str = "No Device") -> None:
        options = self._build_device_options(devices, empty_label=empty_label)
        values = [value for _, value in options]

        selected = None
        if self.device_name in values:
            selected = self.device_name
        else:
            current_value = self.device_select.value
            if current_value in values:
                selected = current_value

        if selected is None and options:
            selected = options[0][1]

        self.device_select.set_options(options)
        if selected is not None:
            try:
                self.device_select.value = selected
            except Exception:
                pass

        self.device_name = None if selected == self.no_device_value else selected
        self.update_device_title()

    async def _refresh_device_options_async(self) -> None:
        devices = await asyncio.to_thread(
            self.app.query_devices,
            force_refresh=False,
            cached_only=False,
        )
        if not self.is_mounted:
            return
        self._apply_device_options(devices, empty_label="No Device")

    def compose(self):
        devices = self.app.query_devices(cached_only=True)
        device_options = self._build_device_options(devices, empty_label="Loading devices...")
        device_values = [opt[1] for opt in device_options]
        initial_value = (
            self.device_name
            if self.device_name in device_values
            else (device_options[0][1] if device_options else self.no_device_value)
        )
        self.device_name = (
            None
            if initial_value in (self.no_device_value, Select.BLANK, None)
            else initial_value
        )

        with Vertical(id="manage-flows-dialog"):
            device_label = self._display_device_name(self.device_name)
            yield Label(
                f"[b]Manage AES67 Multicast Flows[/b] on {device_label}",
                id="manage-flows-title",
            )
            with Horizontal(id="flow-device-select-row"):
                yield Label("Device:", classes="device-label")
                self.device_select = Select(
                    device_options,
                    id="flow-device-select",
                    prompt=" Select Device ",
                    value=initial_value,
                    allow_blank=True,
                )
                yield self.device_select

            # channels for a new Flow
            yield Label(
                "Create New Flow (mockup only - not implemented in this CLI version yet)",
                classes="mockup-note",
            )
            yield Label("Channels: space-separated, e.g. 1 2")
            yield Input(placeholder="1 2", id="new-flow-channels")

            # Buttons
            yield Horizontal(
                Button("Create Flow", id="create-flow"),
                Button("Close", id="close"),
                classes="button-row"
            )

            # Table for existing Flows
            yield Label("Existing Flows (mockup only - not implemented in this CLI version yet)")
            with Vertical(classes="mockup-block"):
                yield Label("Mockup only - not implemented in this CLI version yet", classes="mockup-note")
                flows_table = DataTable(id="flows-table")
                flows_table.add_columns("Flow-ID", "Channels", "Status")
                flows_table.add_row("1", "1 2", "active")
                flows_table.add_row("2", "3 4 5", "active")
                flows_table.disabled = True
                yield flows_table
                yield Horizontal(
                    Button("Refresh", id="refresh-flows"),
                    Button("Delete Selected", id="delete-flow"),
                    classes="button-row"
                )

    def on_mount(self) -> None:
        safe_mode = getattr(self.app, "safe_mode", True)
        self.query_one("#create-flow", Button).disabled = safe_mode
        self.query_one("#delete-flow", Button).disabled = True
        self.query_one("#refresh-flows", Button).disabled = True
        self.update_device_title()
        self.run_worker(
            self._refresh_device_options_async(),
            group="aes67-device-options",
            exclusive=True,
        )

    def _display_device_name(self, value: str | None) -> str:
        if not value or value == self.no_device_value:
            return "No Device"
        return value

    def update_device_title(self) -> None:
        label = self._display_device_name(self.device_name)
        try:
            self.query_one("#manage-flows-title", Label).update(
                f"[b]Manage AES67 Multicast Flows[/b] on {label}"
            )
        except Exception:
            pass

    def on_select_changed(self, event) -> None:
        if event.select.id != "flow-device-select":
            return
        if event.value in (self.no_device_value, Select.BLANK, None):
            self.device_name = None
            self.update_device_title()
            return
        self.device_name = event.value
        self.update_device_title()

    def on_button_pressed(self, event):
        if event.button.id == "close":
            self.app.pop_screen()
        elif event.button.id == "refresh-flows":
            self.app.notify("Command 'list flows' is not implemented", severity="warning")
        elif event.button.id == "create-flow":
            if getattr(self.app, "safe_mode", True):
                self.app.notify("Blocked by Safe Mode: create AES67 flow", severity="warning")
                return
            if not self.supports_flow_creation():
                self.app.notify(
                    "AES67 flow creation is not supported by this CLI version",
                    severity="warning",
                )
                return
            if not self.device_name:
                self.app.notify("No device selected", severity="error")
                return
            channels = self.query_one("#new-flow-channels").value.strip()
            if not channels:
                self.app.notify("Please enter channels", severity="error")
                return
            try:
                subprocess.run([
                    "netaudio", "config",
                    "--device-name", self.device_name,
                    "--aes67-activate-multicast", channels
                ], check=True, capture_output=True, text=True)
                self.app.notify(f"Multicast flow for channel(s) {channels} was created", severity="success")
            except subprocess.CalledProcessError as e:
                self.app.notify(f"Failure: {e.stderr}", severity="error")
        elif event.button.id == "delete-flow":
            if getattr(self.app, "safe_mode", True):
                self.app.notify("Blocked by Safe Mode: delete AES67 flow", severity="warning")
                return
            self.app.notify("Command 'delete flow' is not implemented", severity="warning")   

    @classmethod
    def supports_flow_creation(cls) -> bool:
        cached = cls._flow_support_cache
        if cached is not None:
            return cached
        try:
            result = subprocess.run(
                ["netaudio", "config", "--help"],
                capture_output=True,
                text=True,
                check=False,
            )
            help_text = f"{result.stdout}\n{result.stderr}"
            supported = "--aes67-activate-multicast" in help_text
        except Exception:
            supported = False
        cls._flow_support_cache = supported
        return supported


class AboutScreen(ModalScreen):
    DEFAULT_CSS = """
    AboutScreen {
        align: center middle;
    }
    #about-box {
        width: 88;
        height: 19;
        border: thick $accent;
        background: $surface;
        padding: 2;
        text-align: center;
    }
    """

    def compose(self):
        with Vertical(id="about-box"):
            yield Label(f"[b]{APP_NAME}[/b]", classes="title")
            yield Label(f"Version: {APP_VERSION}")
            yield Label("by jaycfgj")
            yield Label("License: MIT")
            yield Label("")
            yield Label("Network Audio Controller by Chris Ritsen")
            yield Label("CLI Version: loading...", id="cli-version")
            yield Label("AES67 extensions by Mo-way")
            yield Label("Project: https://github.com/chris-ritsen/network-audio-controller")
            yield Horizontal(
                Button("Close", variant="primary", id="close"),
                classes="button-row"
            )

    def on_button_pressed(self, event):
        if event.button.id == "close":
            self.app.pop_screen()   

    def on_mount(self) -> None:
        self._sync_cli_version()
        asyncio.create_task(self.app.ensure_cli_version_loaded())
        if getattr(self.app, "cli_version_loading", False):
            self.set_timer(0.2, self._sync_cli_version)
            self.set_timer(0.5, self._sync_cli_version)

    def _sync_cli_version(self) -> None:
        version = getattr(self.app, "cli_version_text", "unknown")
        try:
            self.query_one("#cli-version", Label).update(f"CLI Version: {version}")
        except Exception:
            pass

class NetworkAudioTUI(App):
    TITLE = APP_TITLE
    SUB_TITLE = ""
    STATUS_COLUMN_WIDTH = 14
    DEVICE_LIST_CACHE_TTL_SECONDS = 20.0
    RESIZE_HANDLE = "<>"
    RX_HEADER_TITLE = "RX Channel"
    STATUS_HEADER_TITLE = "Status"

    CSS = """
    Screen { layout: vertical; padding: 0 1; }

    #tx-device-header {
        height: 1;
        width: 1fr;
        margin-top: 0;
        margin-bottom: 0;
        text-align: left;
        content-align: left middle;
        color: $text-muted;
    }

    #cli-version-bar {
        height: 1;
        width: 1fr;
        margin-top: 0;
        margin-bottom: 0;
        text-align: right;
        content-align: right middle;
        color: $text-muted;
    }

    #matrix { height: 1fr; min-height: 8; }

    #controls { margin-top: 0; height: auto; padding: 0; }

    Input, Select { width: 100%; }

    #button-row {
        height: auto;
        margin-top: 1;
        margin-bottom: 1;
        padding: 0;
    }
    #button-row Button {
        min-width: 16;
        width: 1fr;
    }

    #settings-modal {
        align: center middle;
        background: $panel;
        border: thick #333;
        padding: 1 2;
        width: 92%;
        max-width: 140;
        height: 90%;
        min-height: 12;
        overflow: hidden;
    }

    #select-container {
        width: 100%;
        align: left middle;
        height: auto;
        margin-bottom: 1;
    }

    .device-label {
        margin-right: 1;
        width: auto;
    }

    #settings-modal Select {
        max-width: 100%;
    }

    #tab-container {
         margin-top: 0;
         width: 100%;
         height: 1fr;
         min-height: 0;
    }

    #device-select {
    width: 1fr;
    max-width: 100%;
    height: auto;
    }

    #tab-container ContentSwitcher {
    height: 1fr;
    min-height: 0;
    }

    #tab-container TabPane {
    height: 1fr;
    min-height: 0;
    overflow-y: auto;
    overflow-x: hidden;
    padding-right: 1;
    padding-bottom: 2;
    }

    #tab-info {
        layout: vertical;
        height: 1fr;
        min-height: 0;
        overflow-y: auto;
        overflow-x: hidden;
        padding-right: 1;
        padding-bottom: 2;
    }
    #info-content {
        width: 100%;
    }
    #tab-info .config-buttons {
        dock: bottom;
        height: auto;
        min-height: 3;
        margin-top: 1;
        background: $panel;
    }

    #config-tabs {
    height: 1fr;
    min-height: 0;
    }

    #config-tabs ContentSwitcher {
    height: 1fr;
    min-height: 0;
    }

    #config-tabs TabPane {
    layout: vertical;
    height: 1fr;
    min-height: 0;
    overflow-y: auto;
    overflow-x: hidden;
    padding-right: 1;
    padding-bottom: 2;
    }

    #config-tabs .config-buttons {
    dock: bottom;
    height: auto;
    min-height: 3;
    margin-top: 1;
    background: $panel;
    }
    
    .section-title {
    margin-top: 1;
    text-style: bold;
    }

    .plot-area {
    height: 10;
    border: round $accent;
    background: $boost;
    padding: 1;
    margin-bottom: 1;
    }

    .config-buttons Button {
    margin-right: 2;
    }   

    #safe-mode-status {
        height: 1;
        width: 1fr;
        margin-top: 0;
        margin-bottom: 0;
    }

    #status-row {
        height: 1;
        width: 100%;
        margin-top: 0;
        margin-bottom: 0;
    }

    .mockup-block {
        border: round $surface;
        background: $panel;
        opacity: 0.6;
        padding: 1;
        margin-top: 1;
    }

    .mockup-note {
        text-style: bold;
        color: $text-muted;
        margin-bottom: 1;
    }

    .mockup-field {
        opacity: 0.6;
        color: $text-muted;
    }
    """

    def __init__(self):
        super().__init__()
        self.safe_mode = True
        self.selected_device = None
        self.cli_version_text = "loading..."
        self.cli_version_loaded = False
        self.cli_version_loading = False
        self.matrix_row_endpoints = {}
        self.matrix_column_endpoints = {}
        self.matrix_subscription_map = {}
        self.tx_device_blocks = []
        self.tx_device_groups = []
        self.tx_device_prefix_width = 0
        self.tx_device_separator_width = 0
        self.rx_column_width = None
        self.status_column_width = self.STATUS_COLUMN_WIDTH
        self.subscription_change_in_progress = False
        self.table_refresh_in_progress = False
        self.table_refresh_pending = False
        self.table_refresh_pending_view_state = None
        self.table_refresh_pending_force = False
        self.matrix_refresh_indicator_active = False
        self.pending_subscription_expectation = None
        self.device_inventory_cache = None
        self.device_inventory_cache_expires_at = 0.0

    def compose(self):
        yield Header()
        self.tx_device_header = Static("", id="tx-device-header")
        yield self.tx_device_header

        # Routing matrix
        table = SingleClickDataTable(id="matrix")
        table.cursor_type = "cell"
        table.zebra_stripes = True
        table.fixed_columns = 2
        table.fixed_rows = 0
        table.header_height = 1
        if self.rx_column_width is None:
            table.add_column(self.build_resizable_header_label(self.RX_HEADER_TITLE))
        else:
            table.add_column(
                self.build_resizable_header_label(self.RX_HEADER_TITLE, self.rx_column_width),
                width=self.rx_column_width,
            )
        table.add_column(
            self.build_resizable_header_label(
                self.STATUS_HEADER_TITLE,
                self.status_column_width,
            ),
            width=self.status_column_width,
        )
        self.table = table
        yield table

        # Controls
        with Vertical(id="controls"):
            with Horizontal(id="status-row"):
                self.safe_mode_status = Label("", id="safe-mode-status")
                yield self.safe_mode_status
                self.cli_version_bar = Static("CLI: loading...", id="cli-version-bar")
                yield self.cli_version_bar
            self.safe_mode_toggle = Button("", id="toggle-safe-mode")
            with ItemGrid(
                id="button-row",
                min_column_width=20,
                stretch_height=False,
                regular=False,
            ):
                yield Button("Refresh", id="refresh")
                yield Button("Device Identify", id="identify")
                yield Button("Settings", id="settings")
                yield Button("Manage AES67 Flows", id="manage-aes67-flows")
                yield self.safe_mode_toggle
                yield Button("About", id="about")
                yield Button("Exit", variant="error", id="exit")

    @on(DataTable.CellSelected)
    async def on_cell_selected(self, event) -> None:
        if event.data_table.id != "matrix":
            return

        row_key = event.cell_key.row_key
        column_key = event.cell_key.column_key
        rx_endpoint = self.matrix_row_endpoints.get(row_key)
        if not rx_endpoint:
            return

        rx_device, rx_channel = rx_endpoint
        self.selected_device = rx_device

        # First column: open settings for RX device.
        if event.coordinate.column == 0:
            if self.is_valid_device_selection(rx_device):
                self.push_screen(SettingsScreen(selected_device=rx_device))
            return

        # Only crosspoint columns can toggle subscriptions.
        tx_endpoint = self.matrix_column_endpoints.get(column_key)
        if not tx_endpoint:
            return
        if self.block_if_safe_mode("toggle subscription via matrix crosspoint"):
            return

        if self.subscription_change_in_progress:
            self.app.notify("Subscription change already in progress", severity="warning")
            return

        tx_device, tx_channel = tx_endpoint
        current_tx = self.matrix_subscription_map.get(rx_endpoint)
        expected_tx = None if current_tx == tx_endpoint else tx_endpoint
        self.subscription_change_in_progress = True
        self.set_matrix_refresh_indicator(True, message="Applying subscription change...")
        try:
            if current_tx == tx_endpoint:
                await asyncio.to_thread(
                    subprocess.run,
                    [
                        "netaudio",
                        "subscription",
                        "remove",
                        "--rx",
                        f"{rx_channel}@{rx_device}",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.app.notify(
                    f"Disconnected {rx_device}.{rx_channel}",
                    severity="information",
                )
            else:
                await asyncio.to_thread(
                    subprocess.run,
                    [
                        "netaudio",
                        "subscription",
                        "add",
                        "--tx",
                        f"{tx_channel}@{tx_device}",
                        "--rx",
                        f"{rx_channel}@{rx_device}",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.app.notify(
                    f"Connected {rx_device}.{rx_channel} -> {tx_device}.{tx_channel}",
                    severity="success",
                )
            self.refresh_table_with_retry(
                expected_rx_endpoint=rx_endpoint,
                expected_tx_endpoint=expected_tx,
            )
        except subprocess.CalledProcessError as error:
            self.pending_subscription_expectation = None
            self.set_matrix_refresh_indicator(False)
            error_text = (error.stderr or error.stdout or str(error)).strip()
            self.app.notify(f"Failure: {error_text}", severity="error")
        except Exception as error:
            self.pending_subscription_expectation = None
            self.set_matrix_refresh_indicator(False)
            self.app.notify(f"Failure: {error}", severity="error")
        finally:
            self.subscription_change_in_progress = False

    def on_mount(self):
        self.update_table(force_refresh=True)
        self.set_safe_mode(True, announce=False)
        self.push_screen(StartupWarningScreen(), self.on_startup_warning_result)
        asyncio.create_task(self.ensure_cli_version_loaded())

    def on_startup_warning_result(self, acknowledged: bool | None) -> None:
        if acknowledged:
            return
        self.exit()

    def set_safe_mode(self, enabled: bool, announce: bool = True) -> None:
        self.safe_mode = enabled
        self.update_safe_mode_ui()
        if announce:
            message = (
                "Safe Mode enabled: write actions are blocked"
                if enabled
                else "Safe Mode disabled: write actions allowed"
            )
            self.app.notify(message, severity="information")

    def update_safe_mode_ui(self) -> None:
        status_text = (
            "[b]Safe Mode:[/b] ON (read-only)"
            if self.safe_mode
            else "[b]Safe Mode:[/b] OFF (writes enabled)"
        )
        self.safe_mode_status.update(status_text)
        self.safe_mode_toggle.label = "Unlock Write Actions" if self.safe_mode else "Lock Safe Mode"
        self.safe_mode_toggle.variant = "warning" if self.safe_mode else "success"
        self.update_cli_version_labels()

    async def ensure_cli_version_loaded(self) -> None:
        if self.cli_version_loaded or self.cli_version_loading:
            self.update_cli_version_labels()
            return
        self.cli_version_loading = True
        version = await asyncio.to_thread(get_netaudio_cli_version)
        self.cli_version_text = version
        self.cli_version_loaded = True
        self.cli_version_loading = False
        self.update_cli_version_labels()

    def update_cli_version_labels(self) -> None:
        try:
            self.cli_version_bar.update(f"CLI: {self.cli_version_text}")
        except Exception:
            pass
        try:
            self.query_one("#cli-version", Label).update(f"CLI Version: {self.cli_version_text}")
        except Exception:
            pass

    def on_matrix_column_width_changed(self, column_index: int, width: int) -> None:
        if column_index == 0:
            self.rx_column_width = int(width)
        elif column_index == 1:
            self.status_column_width = int(width)
        self.update_resizable_header_labels()
        try:
            self.tx_device_prefix_width = sum(
                column.get_render_width(self.table)
                for column in self.table.ordered_columns[:2]
            )
        except Exception:
            pass
        self.update_tx_device_header_line()

    def block_if_safe_mode(self, action: str) -> bool:
        if not self.safe_mode:
            return False
        self.app.notify(f"Blocked by Safe Mode: {action}", severity="warning")
        return True

    def on_unlock_safe_mode_result(self, unlocked: bool | None) -> None:
        if unlocked:
            self.set_safe_mode(False)
        else:
            self.app.notify("Safe Mode remains enabled", severity="information")

    def refresh_table_with_retry(
        self,
        expected_rx_endpoint: tuple[str, str] | None = None,
        expected_tx_endpoint: tuple[str, str] | None = None,
    ) -> None:
        # Some Dante devices apply subscription changes asynchronously.
        if expected_rx_endpoint is None:
            self.pending_subscription_expectation = None
        else:
            self.pending_subscription_expectation = {
                "rx_endpoint": expected_rx_endpoint,
                "tx_endpoint": expected_tx_endpoint,
                "attempt": 0,
            }
        view_state = self._capture_matrix_view_state()
        self.set_timer(0.2, lambda state=view_state: self.update_table(state))

    def _schedule_subscription_followup_refresh_if_needed(self) -> None:
        expectation = self.pending_subscription_expectation
        if not expectation:
            return

        rx_endpoint = expectation.get("rx_endpoint")
        if not isinstance(rx_endpoint, tuple) or len(rx_endpoint) != 2:
            self.pending_subscription_expectation = None
            return

        expected_tx = expectation.get("tx_endpoint")
        observed_tx = self.matrix_subscription_map.get(rx_endpoint)
        if observed_tx == expected_tx:
            self.pending_subscription_expectation = None
            return

        attempt = int(expectation.get("attempt", 0)) + 1
        if attempt > 1:
            self.pending_subscription_expectation = None
            self.app.notify(
                "Subscription change still pending on device; use Refresh if needed",
                severity="warning",
            )
            return

        expectation["attempt"] = attempt
        self.pending_subscription_expectation = expectation
        view_state = self._capture_matrix_view_state()
        self.set_timer(0.45, lambda state=view_state: self.update_table(state))

    @staticmethod
    def is_valid_device_selection(device_value) -> bool:
        return isinstance(device_value, str) and bool(device_value.strip())

    @staticmethod
    def compact_status_text(status_text: str) -> str:
        return " ".join(str(status_text).split())

    def build_resizable_header_label(self, title: str, content_width: int | None = None) -> Text:
        handle = self.RESIZE_HANDLE
        if content_width is None:
            label = f"{title} {handle}"
            text = Text(label, style="bold cyan")
            text.stylize("bold yellow", len(label) - len(handle), len(label))
            return text

        width = max(1, int(content_width))
        if width <= len(handle):
            return Text(handle[:width], style="bold yellow")

        title_area_width = max(1, width - len(handle))
        title_part = self.fit_text_to_width(str(title), title_area_width)
        label = f"{title_part}{handle}"
        text = Text(label, style="bold cyan")
        text.stylize("bold yellow", len(label) - len(handle), len(label))
        return text

    def update_resizable_header_labels(self) -> None:
        if not hasattr(self, "table"):
            return
        try:
            ordered_columns = self.table.ordered_columns
            if len(ordered_columns) < 2:
                return
            header_specs = (
                (0, self.RX_HEADER_TITLE),
                (1, self.STATUS_HEADER_TITLE),
            )
            for column_index, title in header_specs:
                column = ordered_columns[column_index]
                render_width = column.get_render_width(self.table)
                content_width = max(1, render_width - (2 * self.table.cell_padding))
                column.label = self.build_resizable_header_label(title, content_width)
            self.table.refresh()
        except Exception:
            pass

    @staticmethod
    def fit_text_to_width(text: str, width: int) -> str:
        normalized = " ".join(str(text).split())
        if width <= 0:
            return ""
        if len(normalized) > width:
            if width <= 1:
                return "…"
            return normalized[: width - 1] + "…"
        return normalized.ljust(width)

    def set_matrix_refresh_indicator(
        self,
        active: bool,
        queued: bool = False,
        message: str | None = None,
    ) -> None:
        self.matrix_refresh_indicator_active = active
        if not hasattr(self, "tx_device_header"):
            return
        if active:
            if message:
                text = str(message).strip()
            else:
                suffix = " (queued)" if queued else ""
                text = f"Refreshing routing matrix{suffix}..."
            self.tx_device_header.update(Text(text, style="bold yellow"))
            return
        self.update_tx_device_header_line()

    def update_tx_device_header_line(self) -> None:
        if self.matrix_refresh_indicator_active:
            return
        if not hasattr(self, "tx_device_header"):
            return
        if not self.tx_device_blocks:
            self.tx_device_header.update("")
            return

        try:
            prefix_width = sum(
                column.get_render_width(self.table)
                for column in self.table.ordered_columns[:2]
            )
        except Exception:
            prefix_width = int(self.tx_device_prefix_width)

        first_tx_text_offset = int(getattr(self.table, "cell_padding", 0))
        prefix_len = max(0, int(prefix_width) + first_tx_text_offset)
        header_width = int(getattr(getattr(self.tx_device_header, "size", None), "width", 0))
        if header_width <= 0:
            header_width = int(getattr(getattr(self.table, "size", None), "width", 0))
        visible_tx_width = max(0, header_width - prefix_len)

        scroll_x = int(round(getattr(self.table, "scroll_x", 0.0)))
        tx_buffer = [" "] * visible_tx_width
        visible_start = scroll_x
        visible_end = scroll_x + visible_tx_width

        for device_name, block_start, block_width in self.tx_device_blocks:
            block_end = block_start + block_width
            if block_end <= visible_start or block_start >= visible_end:
                continue

            zone_start = max(block_start, visible_start)
            zone_end = min(block_end, visible_end)
            zone_width = zone_end - zone_start
            if zone_width <= 0:
                continue

            zone_offset = zone_start - visible_start
            sticky_label = self.fit_text_to_width(device_name, zone_width)
            for index, char in enumerate(sticky_label[:zone_width]):
                tx_buffer[zone_offset + index] = char

        tx_line = "".join(tx_buffer)
        prefix = " " * prefix_len
        self.tx_device_header.update(Text(prefix + tx_line, style="bold cyan"))

    def on_matrix_scroll_changed(self, _new_scroll_x: float) -> None:
        self.update_tx_device_header_line()

    @staticmethod
    def _parse_json_output(raw_text: str):
        text = raw_text.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Some CLI commands return human-readable output even with --json.
            # Try to salvage JSON if it appears later in the output.
            for index, ch in enumerate(text):
                if ch in "[{":
                    try:
                        return json.loads(text[index:])
                    except json.JSONDecodeError:
                        break
            return None

    @staticmethod
    def _extract_device_names(devices_data: dict) -> list[str]:
        names = []
        seen = set()
        if not isinstance(devices_data, dict):
            return names
        for key, value in devices_data.items():
            if isinstance(value, dict) and value.get("name"):
                name = str(value["name"])
            elif isinstance(key, str):
                name = key
            else:
                continue
            if name not in seen:
                seen.add(name)
                names.append(name)
        return names

    def _load_device_inventory(self, force_refresh: bool = False):
        now = time.monotonic()
        if (
            not force_refresh
            and isinstance(self.device_inventory_cache, dict)
            and now < self.device_inventory_cache_expires_at
        ):
            return self.device_inventory_cache
        result = subprocess.run(
            ["netaudio", "--json", "device", "list"],
            capture_output=True,
            text=True,
            check=True,
        )
        devices_data = self._parse_json_output(result.stdout) or {}
        if not isinstance(devices_data, dict):
            devices_data = {}
        self.device_inventory_cache = devices_data
        self.device_inventory_cache_expires_at = now + self.DEVICE_LIST_CACHE_TTL_SECONDS
        return devices_data

    @staticmethod
    def _extract_subscription_status(sub: dict) -> tuple[str, str]:
        status_state = ""
        status_payload = sub.get("status")
        candidates = [sub.get("status_text"), sub.get("status_label")]
        if isinstance(status_payload, dict):
            raw_state = status_payload.get("state")
            if raw_state is not None:
                status_state = str(raw_state).strip().lower()
            candidates.extend(
                [
                    status_payload.get("label"),
                    status_payload.get("detail"),
                    status_payload.get("state"),
                ]
            )
        rx_channel_status = sub.get("rx_channel_status")
        if isinstance(rx_channel_status, dict):
            candidates.extend(
                [
                    rx_channel_status.get("label"),
                    rx_channel_status.get("detail"),
                    rx_channel_status.get("state"),
                ]
            )

        for value in candidates:
            if isinstance(value, list):
                value = value[-1] if value else None
            if value is None:
                continue
            text_value = str(value).strip()
            if text_value:
                return text_value, status_state

        return "No subscription for this channel", status_state

    def query_devices(self, force_refresh: bool = False, cached_only: bool = False):
        try:
            if cached_only:
                if isinstance(self.device_inventory_cache, dict):
                    return self._extract_device_names(self.device_inventory_cache)
                return []
            devices_data = self._load_device_inventory(force_refresh=force_refresh)
            return self._extract_device_names(devices_data)
        except Exception:
            return []

    def update_table(self, view_state: dict | None = None, force_refresh: bool = False):
        if view_state is None:
            view_state = self._capture_matrix_view_state()
        if self.table_refresh_in_progress:
            self.table_refresh_pending = True
            self.table_refresh_pending_view_state = view_state
            self.table_refresh_pending_force = self.table_refresh_pending_force or force_refresh
            self.set_matrix_refresh_indicator(True, queued=True)
            return
        self.set_matrix_refresh_indicator(True)
        self.table_refresh_in_progress = True
        self.run_worker(
            self._update_table_async(view_state, force_refresh),
            group="matrix-refresh",
            exclusive=True,
        )

    async def _update_table_async(self, view_state: dict, force_refresh: bool) -> None:
        try:
            snapshot = await asyncio.to_thread(self._collect_matrix_snapshot, force_refresh)
            self._render_matrix_snapshot(snapshot, view_state)
        except Exception as error:
            self._render_matrix_error(str(error), view_state)
        finally:
            self.table_refresh_in_progress = False
            if self.table_refresh_pending:
                pending_view_state = self.table_refresh_pending_view_state
                pending_force_refresh = self.table_refresh_pending_force
                self.table_refresh_pending = False
                self.table_refresh_pending_view_state = None
                self.table_refresh_pending_force = False
                self.update_table(pending_view_state, force_refresh=pending_force_refresh)
            else:
                self.set_matrix_refresh_indicator(False)

    def _collect_matrix_snapshot(self, force_refresh: bool = False):
        now = time.monotonic()
        should_refresh_devices = (
            force_refresh
            or not isinstance(self.device_inventory_cache, dict)
            or now >= self.device_inventory_cache_expires_at
        )

        if should_refresh_devices:
            with ThreadPoolExecutor(max_workers=2) as executor:
                device_future = executor.submit(
                    subprocess.run,
                    ["netaudio", "--json", "device", "list"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                subscription_future = executor.submit(
                    subprocess.run,
                    ["netaudio", "--json", "subscription", "list"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                device_result = device_future.result()
                subscription_result = subscription_future.result()
            devices_data = self._parse_json_output(device_result.stdout) or {}
            if not isinstance(devices_data, dict):
                devices_data = {}
            self.device_inventory_cache = devices_data
            self.device_inventory_cache_expires_at = (
                time.monotonic() + self.DEVICE_LIST_CACHE_TTL_SECONDS
            )
        else:
            devices_data = self.device_inventory_cache
            subscription_result = subprocess.run(
                ["netaudio", "--json", "subscription", "list"],
                capture_output=True,
                text=True,
                check=True,
            )

        subscriptions_data = self._parse_json_output(subscription_result.stdout)
        if subscriptions_data is None and "no active subscriptions" in subscription_result.stdout.lower():
            subscriptions_data = []

        subscriptions = []
        if isinstance(subscriptions_data, list):
            subscriptions = subscriptions_data
        elif isinstance(subscriptions_data, dict):
            for info in subscriptions_data.values():
                if isinstance(info, dict):
                    subscriptions.extend(info.get("subscriptions", []))

        rx_endpoints = []
        tx_endpoints = []
        if isinstance(devices_data, dict):
            for host_key, info in devices_data.items():
                if not isinstance(info, dict):
                    continue
                device_name = str(info.get("name", host_key))

                receivers = info.get("channels", {}).get("receivers", {})
                transmitters = info.get("channels", {}).get("transmitters", {})

                try:
                    rx_items = sorted(receivers.items(), key=lambda item: int(item[0]))
                except Exception:
                    rx_items = receivers.items()
                try:
                    tx_items = sorted(transmitters.items(), key=lambda item: int(item[0]))
                except Exception:
                    tx_items = transmitters.items()

                for _, channel_info in rx_items:
                    rx_name = channel_info.get("name") if isinstance(channel_info, dict) else None
                    if rx_name is None:
                        continue
                    rx_endpoints.append((device_name, str(rx_name)))
                for _, channel_info in tx_items:
                    tx_name = channel_info.get("name") if isinstance(channel_info, dict) else None
                    if tx_name is None:
                        continue
                    tx_endpoints.append((device_name, str(tx_name)))

        if not rx_endpoints and subscriptions:
            for sub in subscriptions:
                rx_device = sub.get("rx_device")
                rx_channel = sub.get("rx_channel")
                if rx_device and rx_channel:
                    endpoint = (str(rx_device), str(rx_channel))
                    if endpoint not in rx_endpoints:
                        rx_endpoints.append(endpoint)

        if not tx_endpoints and subscriptions:
            for sub in subscriptions:
                tx_device = sub.get("tx_device")
                tx_channel = sub.get("tx_channel")
                if tx_device and tx_channel:
                    endpoint = (str(tx_device), str(tx_channel))
                    if endpoint not in tx_endpoints:
                        tx_endpoints.append(endpoint)

        def channel_sort_key(endpoint: tuple[str, str]):
            device, channel = endpoint
            try:
                ch_key = (0, int(channel))
            except Exception:
                ch_key = (1, channel)
            return (device.lower(), ch_key)

        rx_endpoints.sort(key=channel_sort_key)
        tx_endpoints.sort(key=channel_sort_key)

        subscription_map = {}
        for sub in subscriptions:
            rx_device = sub.get("rx_device")
            rx_channel = sub.get("rx_channel")
            if not rx_device or not rx_channel:
                continue
            status_text, status_state = self._extract_subscription_status(sub)
            tx_device = sub.get("tx_device")
            tx_channel = sub.get("tx_channel")
            tx_endpoint = None
            if tx_device and tx_channel:
                tx_endpoint = (str(tx_device), str(tx_channel))
            subscription_map[(str(rx_device), str(rx_channel))] = {
                "status": str(status_text),
                "status_state": str(status_state),
                "tx_endpoint": tx_endpoint,
            }

        return {
            "rx_endpoints": rx_endpoints,
            "tx_endpoints": tx_endpoints,
            "subscription_map": subscription_map,
        }

    def _capture_matrix_view_state(self) -> dict:
        state = {
            "scroll": (
                float(getattr(self.table, "scroll_x", 0.0)),
                float(getattr(self.table, "scroll_y", 0.0)),
            ),
            "cursor_coordinate": None,
            "cursor_rx_endpoint": None,
            "cursor_tx_endpoint": None,
            "had_focus": bool(getattr(self.table, "has_focus", False)),
        }
        try:
            coordinate = self.table.cursor_coordinate
            if coordinate is not None:
                state["cursor_coordinate"] = (int(coordinate.row), int(coordinate.column))
                cell_key = self.table.coordinate_to_cell_key(coordinate)
                state["cursor_rx_endpoint"] = self.matrix_row_endpoints.get(cell_key.row_key)
                state["cursor_tx_endpoint"] = self.matrix_column_endpoints.get(cell_key.column_key)
        except Exception:
            pass
        return state

    def _restore_matrix_view_state(self, view_state: dict) -> None:
        target_row = None
        target_column = None
        rx_endpoint = view_state.get("cursor_rx_endpoint")
        tx_endpoint = view_state.get("cursor_tx_endpoint")
        if rx_endpoint is not None and tx_endpoint is not None:
            row_key = next(
                (key for key, endpoint in self.matrix_row_endpoints.items() if endpoint == rx_endpoint),
                None,
            )
            column_key = next(
                (key for key, endpoint in self.matrix_column_endpoints.items() if endpoint == tx_endpoint),
                None,
            )
            if row_key is not None and column_key is not None:
                try:
                    target_row = self.table.get_row_index(row_key)
                    target_column = self.table.get_column_index(column_key)
                except Exception:
                    target_row = None
                    target_column = None

        if target_row is None or target_column is None:
            cursor_coordinate = view_state.get("cursor_coordinate")
            if cursor_coordinate is not None:
                target_row, target_column = cursor_coordinate

        if target_row is not None and target_column is not None:
            try:
                self.table.move_cursor(
                    row=target_row,
                    column=target_column,
                    animate=False,
                    scroll=False,
                )
            except Exception:
                pass

        if view_state.get("had_focus"):
            try:
                self.table.focus()
            except Exception:
                pass

        scroll_x, scroll_y = view_state.get("scroll", (0.0, 0.0))
        try:
            self.table.scroll_to(scroll_x, scroll_y, animate=False, force=True)
        except Exception:
            try:
                self.table.scroll_x = scroll_x
                self.table.scroll_y = scroll_y
            except Exception:
                pass

    def _render_matrix_error(self, error_text: str, view_state: dict) -> None:
        self.pending_subscription_expectation = None
        self.table.clear(columns=True)
        self.matrix_row_endpoints = {}
        self.matrix_column_endpoints = {}
        self.matrix_subscription_map = {}
        self.tx_device_blocks = []
        self.tx_device_groups = []
        self.tx_device_prefix_width = 0
        self.tx_device_separator_width = 0
        if self.rx_column_width is None:
            self.table.add_column(self.build_resizable_header_label(self.RX_HEADER_TITLE))
        else:
            self.table.add_column(
                self.build_resizable_header_label(self.RX_HEADER_TITLE, self.rx_column_width),
                width=self.rx_column_width,
            )
        self.table.add_column(
            self.build_resizable_header_label(
                self.STATUS_HEADER_TITLE,
                self.status_column_width,
            ),
            width=self.status_column_width,
        )
        self.table.add_row("Failure", error_text)
        self.update_resizable_header_labels()
        self.update_tx_device_header_line()
        self.set_timer(0.02, self.update_tx_device_header_line)
        self._restore_matrix_view_state(view_state)
        self.set_timer(0.02, lambda state=view_state: self._restore_matrix_view_state(state))

    def _render_matrix_snapshot(self, snapshot, view_state: dict) -> None:
        self.table.clear(columns=True)
        self.matrix_row_endpoints = {}
        self.matrix_column_endpoints = {}
        self.matrix_subscription_map = {}
        self.tx_device_blocks = []
        self.tx_device_groups = []
        self.tx_device_prefix_width = 0
        self.tx_device_separator_width = 0

        rx_endpoints = snapshot["rx_endpoints"]
        tx_endpoints = snapshot["tx_endpoints"]
        subscription_map = snapshot["subscription_map"]

        # Add columns grouped by TX device with separator columns.
        if self.rx_column_width is None:
            self.table.add_column(self.build_resizable_header_label(self.RX_HEADER_TITLE))
        else:
            self.table.add_column(
                self.build_resizable_header_label(self.RX_HEADER_TITLE, self.rx_column_width),
                width=self.rx_column_width,
            )
        self.table.add_column(
            self.build_resizable_header_label(
                self.STATUS_HEADER_TITLE,
                self.status_column_width,
            ),
            width=self.status_column_width,
        )
        self.tx_device_prefix_width = sum(
            column.get_render_width(self.table)
            for column in self.table.ordered_columns[:2]
        )

        tx_grid_columns = []
        grouped_tx_endpoints = []
        tx_content_cursor = 0
        for tx_device, tx_channel in tx_endpoints:
            if not grouped_tx_endpoints or grouped_tx_endpoints[-1][0] != tx_device:
                grouped_tx_endpoints.append((tx_device, []))
            grouped_tx_endpoints[-1][1].append(tx_channel)

        for group_index, (tx_device, channels) in enumerate(grouped_tx_endpoints):
            channel_labels = [str(channel).strip() or "?" for channel in channels]
            channel_widths = [max(3, len(label)) for label in channel_labels]
            group_render_width = 0

            for tx_channel, tx_label, tx_column_width in zip(
                channels,
                channel_labels,
                channel_widths,
            ):
                tx_key = self.table.add_column(
                    Text(tx_label, style="bold yellow"),
                    width=tx_column_width,
                )
                tx_grid_columns.append((tx_device, tx_channel))
                self.matrix_column_endpoints[tx_key] = (tx_device, tx_channel)
                group_render_width += tx_column_width + (2 * self.table.cell_padding)

            self.tx_device_groups.append((str(tx_device), group_render_width))
            self.tx_device_blocks.append((str(tx_device), tx_content_cursor, group_render_width))
            tx_content_cursor += group_render_width

            if group_index < len(grouped_tx_endpoints) - 1:
                separator_content_width = 1
                self.table.add_column(Text("│", style="dim"), width=separator_content_width)
                tx_grid_columns.append(None)
                self.tx_device_separator_width = separator_content_width + (
                    2 * self.table.cell_padding
                )
                tx_content_cursor += self.tx_device_separator_width

        self.matrix_subscription_map = {
            rx_endpoint: data["tx_endpoint"] for rx_endpoint, data in subscription_map.items()
        }

        self.table.fixed_rows = 0

        # Add rows grouped by RX device with section headers.
        last_rx_device = None
        for rx_device, rx_channel in rx_endpoints:
            if rx_device != last_rx_device:
                section_cells = [
                    Text("│", style="dim") if tx_endpoint is None else Text("")
                    for tx_endpoint in tx_grid_columns
                ]
                self.table.add_row(
                    Text(f"RX: {rx_device}", style="bold white on dark_blue"),
                    Text("device block", style="dim italic"),
                    *section_cells,
                )
                last_rx_device = rx_device

            sub = subscription_map.get((rx_device, rx_channel), {})
            active_tx = sub.get("tx_endpoint")
            status_text = self.compact_status_text(
                sub.get("status", "No subscription for this channel")
            )
            markers = []
            for tx_endpoint in tx_grid_columns:
                if tx_endpoint is None:
                    markers.append(Text("│", style="dim"))
                else:
                    if active_tx == tx_endpoint:
                        markers.append(Text("●", style="bold green"))
                    else:
                        markers.append(Text("·", style="grey50"))

            status_state = str(sub.get("status_state", "")).lower()
            if not active_tx:
                status_style = "grey58"
            elif status_state in {"failed", "error"}:
                status_style = "red"
            elif status_state in {"unresolved"}:
                status_style = "yellow"
            else:
                status_style = "green"

            row_key = self.table.add_row(
                Text(f"{rx_device}.{rx_channel}", style="bold"),
                Text(status_text, style=status_style),
                *markers,
            )
            self.matrix_row_endpoints[row_key] = (rx_device, rx_channel)

        if self.table.row_count == 0:
            self.table.add_row("No channels found", "-")
        self._schedule_subscription_followup_refresh_if_needed()
        self.update_resizable_header_labels()
        self.update_tx_device_header_line()
        self.set_timer(0.02, self.update_tx_device_header_line)
        self._restore_matrix_view_state(view_state)
        self.set_timer(0.02, lambda state=view_state: self._restore_matrix_view_state(state))

    def get_active_device(self):
        if self.is_valid_device_selection(self.selected_device):
            return self.selected_device
        devices = self.query_devices()
        if len(devices) == 1:
            return devices[0]
        return None

    def on_button_pressed(self, event):
        btn = event.button.id
        if btn == "refresh":
            self.update_table(force_refresh=True)
        elif btn == "toggle-safe-mode":
            if self.safe_mode:
                self.push_screen(UnlockSafeModeScreen(), self.on_unlock_safe_mode_result)
            else:
                self.set_safe_mode(True)
        elif btn == "identify":
            device = self.get_active_device()
            if self.is_valid_device_selection(device):
                try:
                    subprocess.run([
                        "netaudio", "--name", device, "device", "identify"
                    ], check=True, capture_output=True, text=True)
                    self.app.notify(f"Identify triggered for {device}", severity="success")
                except subprocess.CalledProcessError:
                    self.app.notify("Identify failed", severity="error")
            else:
                self.app.notify("No device selected", severity="warning")
        elif btn == "settings":
            device = self.get_active_device()
            selected_device = device if self.is_valid_device_selection(device) else None
            self.push_screen(SettingsScreen(selected_device=selected_device))
        elif btn == "manage-aes67-flows":
            device = self.get_active_device()
            selected_device = device if self.is_valid_device_selection(device) else None
            self.push_screen(ManageAES67FlowsScreen(selected_device))
        elif btn == "about":
            self.push_screen(AboutScreen())
        elif btn == "exit":
            self.push_screen(ConfirmExit())   

def main() -> None:
    NetworkAudioTUI().run()


if __name__ == "__main__":
    main()
