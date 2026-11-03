"""Diagnostics for the Tigo RS485 integration."""

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .sensor import TigoCoordinator

TO_REDACT = {CONF_HOST}


def _report(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a power report with the raw bytes as hex, for JSON."""
    if data is None:
        return None
    return {
        key: value.hex(":") if isinstance(value, bytes) else value
        for key, value in data.items()
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return the diagnostics of a config entry."""
    diagnostics: dict[str, Any] = {
        "data": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
    }
    coordinator: TigoCoordinator | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )
    if coordinator is None:
        return diagnostics

    now = hass.loop.time()
    diagnostics["connected"] = coordinator.connected
    diagnostics["stats"] = dict(coordinator.reader.stats) if coordinator.reader else {}
    diagnostics["optimizers"] = [
        {
            "node_id": node_id,
            "address": coordinator.addresses.get(node_id),
            "serial_number": coordinator.serial_number(node_id),
            "label": (label := coordinator.label_for(node_id)),
            "string": coordinator.string_for(label),
            "idle": coordinator.is_idle(node_id),
            "seconds_since_report": (
                round(now - last)
                if (last := coordinator.last_report.get(node_id)) is not None
                else None
            ),
            "data": _report(coordinator.optimizer_data.get(node_id)),
        }
        for node_id in sorted(
            set(coordinator.optimizer_data) | set(coordinator.addresses)
        )
    ]
    diagnostics["energy_wh"] = {
        "today": coordinator.energy_today,
        "total": coordinator.energy_total,
    }
    return diagnostics
