"""Constants for the Tigo RS485 integration."""

import re

DOMAIN = "tigo_rs485"

# Options: {"labels": {"<node id>": "<panel label>"}}
CONF_LABELS = "labels"

# Options: seconds to average the reports over before writing a state,
# 0 writes every report as it arrives
CONF_UPDATE_INTERVAL = "update_interval"
DEFAULT_UPDATE_INTERVAL = 0
UPDATE_INTERVALS = (0, 10, 30, 60, 120, 300, 600)

# Options: {"<panel label>": "<string name>"}, labels without a string are
# left out. Entries saved before it existed derive the string from the label
CONF_STRINGS = "strings"

# A name followed by a number, e.g. A1, Süd-1 or "Dach links 3"
STRING_LABEL = re.compile(r"(\D*?)[\s._-]*\d+")


def guess_string(label: str) -> str | None:
    """Return the string suggested for a panel label."""
    if not (match := STRING_LABEL.fullmatch(label)) or not (name := match.group(1)):
        return None
    return name
