"""Config flow for Tigo RS485 integration."""
from __future__ import annotations
import asyncio
import contextlib
import logging
import os
from typing import Any
import voluptuous as vol
import serial
import serial.tools.list_ports
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
CONF_CONNECTION_TYPE = "connection_type"
CONF_SERIAL_PORT = "serial_port"
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from .const import (
    CONF_LABELS,
    CONF_STRINGS,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    UPDATE_INTERVALS,
    guess_string,
)

_LOGGER = logging.getLogger(__name__)

BY_ID_DIR = "/dev/serial/by-id"
DEFAULT_SERIAL_PORT = "/dev/ttyUSB0"
CONNECT_TIMEOUT = 10


def _list_serial_ports() -> list[SelectOptionDict]:
    """Return the serial ports, stable /dev/serial/by-id paths first.

    The by-id path stays the same when the adapter is plugged into another
    USB port, while /dev/ttyUSB* numbers may change after a restart.
    """
    by_id: list[SelectOptionDict] = []
    if os.path.isdir(BY_ID_DIR):
        for entry in sorted(os.scandir(BY_ID_DIR), key=lambda e: e.name):
            label = entry.name
            if (target := os.path.realpath(entry.path)) != entry.path:
                label = f"{entry.name} ({target})"
            by_id.append({"value": entry.path, "label": label})

    other: list[SelectOptionDict] = []
    for port in sorted(serial.tools.list_ports.comports(), key=lambda p: p.device):
        label = port.device
        if port.description and port.description != "n/a":
            label = f"{port.device} ({port.description})"
        other.append({"value": port.device, "label": label})

    ports = by_id + other
    if not ports:
        ports = [{"value": DEFAULT_SERIAL_PORT, "label": DEFAULT_SERIAL_PORT}]
    return ports

async def _async_test_connection(hass, user_input: dict[str, Any]) -> None:
    """Open the TCP connection or serial port briefly, raise OSError if it fails.

    No data is awaited, as the optimizers do not report at night.
    """
    if CONF_HOST in user_input:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(user_input[CONF_HOST], user_input[CONF_PORT]),
            CONNECT_TIMEOUT,
        )
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
        return

    def _open_and_close() -> None:
        # SerialException is an OSError
        serial.Serial(user_input[CONF_SERIAL_PORT], baudrate=38400).close()

    await hass.async_add_executor_job(_open_and_close)


class TigoRS485ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tigo RS485."""

    VERSION = 1
    MINOR_VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> TigoRS485OptionsFlow:
        """Return the options flow."""
        return TigoRS485OptionsFlow()


    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            # Merke Auswahl und gehe zum nächsten Schritt
            self.connection_type = user_input[CONF_CONNECTION_TYPE]
            return await self.async_step_connection()

        data_schema = vol.Schema({
            vol.Required(CONF_CONNECTION_TYPE, default="serial"): SelectSelector(
                SelectSelectorConfig(
                    options=["serial", "tcp"],
                    translation_key=CONF_CONNECTION_TYPE,
                    mode=SelectSelectorMode.LIST,
                )
            ),
        })
        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)

    async def async_step_connection(self, user_input=None):
        errors = {}
        connection_type = getattr(self, "connection_type", None)
        if user_input is not None:
            if connection_type == "tcp":
                unique = f"tigo_tcp_{user_input[CONF_HOST]}_{user_input[CONF_PORT]}"
                title = f"Tigo RS485 TCP {user_input[CONF_HOST]}"
                data = {
                    CONF_CONNECTION_TYPE: connection_type,
                    CONF_HOST: user_input[CONF_HOST],
                    CONF_PORT: user_input[CONF_PORT],
                }
            else:
                unique = f"tigo_serial_{user_input[CONF_SERIAL_PORT]}"
                title = f"Tigo RS485 Serial {user_input[CONF_SERIAL_PORT]}"
                data = {
                    CONF_CONNECTION_TYPE: connection_type,
                    CONF_SERIAL_PORT: user_input[CONF_SERIAL_PORT],
                }
            # Checked first, the configured port may be in use by this entry
            await self.async_set_unique_id(unique)
            self._abort_if_unique_id_configured()
            try:
                await _async_test_connection(self.hass, user_input)
            except OSError as err:
                _LOGGER.debug("Cannot connect to %s: %s", title, err)
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title=title, data=data)

        if connection_type == "tcp":
            data_schema = vol.Schema({
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=502): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
            })
        else:
            ports = await self.hass.async_add_executor_job(_list_serial_ports)
            data_schema = vol.Schema({
                vol.Required(CONF_SERIAL_PORT, default=ports[0]["value"]): SelectSelector(
                    SelectSelectorConfig(
                        options=ports,
                        custom_value=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            })
        return self.async_show_form(step_id="connection", data_schema=data_schema, errors=errors)


class TigoRS485OptionsFlow(config_entries.OptionsFlow):
    """Assign panel labels to the discovered optimizers and their strings."""

    def __init__(self) -> None:
        """Initialize the options flow."""
        self._options: dict[str, Any] = {}

    async def async_step_init(self, user_input=None):
        """Show the update interval and one label field per known optimizer."""
        old_labels: dict[str, str] = self.config_entry.options.get(CONF_LABELS, {})
        update_interval: int = self.config_entry.options.get(
            CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
        )
        coordinator = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        if coordinator is None:
            return self.async_abort(reason="not_loaded")

        # One field per optimizer, keyed by its long address once known
        node_ids = sorted(set(coordinator.optimizer_data) | set(coordinator.addresses))
        keys = {coordinator.label_key(node_id): node_id for node_id in node_ids}
        labels = {
            key: label
            for key, node_id in keys.items()
            if (label := coordinator.label_for(node_id)) is not None
        }
        # Keep labels of optimizers that have not reported since the restart
        for key, label in old_labels.items():
            if key in coordinator.legacy_keys:
                continue  # Wrongly decoded address, now listed under its node
            if key not in keys and label not in labels.values():
                keys[key] = None
                labels[key] = label

        errors: dict[str, str] = {}
        if user_input is not None:
            update_interval = int(user_input[CONF_UPDATE_INTERVAL])
            new_labels = {
                key: label
                for key in keys
                if (label := user_input.get(key, "").strip())
            }
            if len(set(new_labels.values())) != len(new_labels):
                errors["base"] = "duplicate_label"
            else:
                self._options = {
                    CONF_LABELS: new_labels,
                    CONF_UPDATE_INTERVAL: update_interval,
                }
                if not new_labels:
                    return self.async_create_entry(
                        data={**self._options, CONF_STRINGS: {}}
                    )
                return await self.async_step_strings()
            labels = new_labels

        # List what is known about each optimizer to help identify it
        lines = []
        for key, node_id in keys.items():
            data = coordinator.optimizer_data.get(node_id, {})
            details = [f"`{key}`"]
            if node_id is not None:
                details.append(f"node {node_id}")
            if data.get("vin") is not None:
                details.append(f"Vin {data['vin']:.1f} V")
            if data.get("ampsin") is not None:
                details.append(f"I {data['ampsin']:.2f} A")
            if node_id is None or not data:
                details.append("no data yet")
            lines.append("- " + ", ".join(details))
        if not lines:
            lines.append("- none yet")

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_UPDATE_INTERVAL, default=str(update_interval)
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[str(seconds) for seconds in UPDATE_INTERVALS],
                        translation_key=CONF_UPDATE_INTERVAL,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                **{
                    vol.Optional(
                        key, description={"suggested_value": labels.get(key, "")}
                    ): str
                    for key in keys
                },
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders={"optimizers": "\n".join(lines)},
        )

    async def async_step_strings(self, user_input=None):
        """Assign each labeled optimizer to a string."""
        labels = sorted(self._options[CONF_LABELS].values())
        if user_input is not None:
            strings = {
                label: string
                for label in labels
                if (string := user_input.get(label, "").strip())
            }
            return self.async_create_entry(data={**self._options, CONF_STRINGS: strings})

        old_labels = set(self.config_entry.options.get(CONF_LABELS, {}).values())
        old_strings: dict[str, str] | None = self.config_entry.options.get(CONF_STRINGS)
        suggested: dict[str, str] = {}
        for label in labels:
            # Kept labels show their saved string, even when it was cleared
            if old_strings is not None and label in old_labels:
                suggested[label] = old_strings.get(label, "")
            else:
                suggested[label] = guess_string(label) or ""

        schema = vol.Schema(
            {
                vol.Optional(
                    label, description={"suggested_value": suggested[label]}
                ): str
                for label in labels
            }
        )
        return self.async_show_form(step_id="strings", data_schema=schema)
