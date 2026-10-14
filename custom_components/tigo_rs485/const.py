"""Constants for the Tigo RS485 integration."""

DOMAIN = "tigo_rs485"

# Options: {"labels": {"<node id>": "<panel label>"}}
CONF_LABELS = "labels"

# Options: seconds to average the reports over before writing a state,
# 0 writes every report as it arrives
CONF_UPDATE_INTERVAL = "update_interval"
DEFAULT_UPDATE_INTERVAL = 0
UPDATE_INTERVALS = (0, 10, 30, 60, 120, 300, 600)
