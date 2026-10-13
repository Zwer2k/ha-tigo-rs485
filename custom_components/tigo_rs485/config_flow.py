"""Config flow for Tigo RS485 integration."""
from __future__ import annotations
import os
import voluptuous as vol
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
from .const import CONF_LABELS, DOMAIN

BY_ID_DIR = "/dev/serial/by-id"
DEFAULT_SERIAL_PORT = "/dev/ttyUSB0"


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
            # Test connection to tappipe hier (pseudo code)
            try:
                if connection_type == "tcp":
                    # from tappipe import TappipeClient
                    # client = TappipeClient(user_input[CONF_HOST], user_input[CONF_PORT])
                    # await client.async_connect()
                    # await client.async_disconnect()
                    pass
                else:
                    # from tappipe import TappipeSerial
                    # client = TappipeSerial(user_input[CONF_SERIAL_PORT])
                    # await client.async_connect()
                    # await client.async_disconnect()
                    pass
            except Exception:
                errors["base"] = "cannot_connect"
            else:
                # Unique ID je nach Typ
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
                await self.async_set_unique_id(unique)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=title, data=data)

        if connection_type == "tcp":
            data_schema = vol.Schema({
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=502): int,
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
    """Assign panel labels to the discovered optimizers."""

    async def async_step_init(self, user_input=None):
        """Show one label field per known optimizer."""
        old_labels: dict[str, str] = self.config_entry.options.get(CONF_LABELS, {})
        coordinator = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        if coordinator is None:
            return self.async_abort(reason="no_optimizers")

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
        if not keys:
            return self.async_abort(reason="no_optimizers")

        errors: dict[str, str] = {}
        if user_input is not None:
            new_labels = {
                key: label
                for key in keys
                if (label := user_input.get(key, "").strip())
            }
            if len(set(new_labels.values())) != len(new_labels):
                errors["base"] = "duplicate_label"
            else:
                return self.async_create_entry(data={CONF_LABELS: new_labels})
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

        schema = vol.Schema(
            {
                vol.Optional(
                    key, description={"suggested_value": labels.get(key, "")}
                ): str
                for key in keys
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders={"optimizers": "\n".join(lines)},
        )
