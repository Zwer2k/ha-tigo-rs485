# Home Assistant integration for Tigo optimizers (RS485)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/Zwer2k/ha-tigo-rs485.svg)](https://github.com/Zwer2k/ha-tigo-rs485/releases)
[![License](https://img.shields.io/github/license/Zwer2k/ha-tigo-rs485.svg)](LICENSE)

This integration reads the data of your Tigo TS4 optimizers locally, straight from the RS485 bus between the Tigo Cloud Connect Advanced (CCA) and the Tigo Access Point (TAP, gateway). It does not need the Tigo cloud or the CCA web interface.

The integration only listens. It never sends anything on the bus, so the CCA keeps working as before.

## Installation via HACS

1. In Home Assistant, go to HACS → Integrations.
2. Select the three dots in the top right → "Custom repositories".
3. Enter `https://github.com/Zwer2k/ha-tigo-rs485` as the repository URL and select "Integration".
4. Select "Add" and install the integration.
5. Restart Home Assistant.

## Hardware

You need one of these, connected in parallel to the RS485 wires (A/B) between the CCA and the TAP:

- **USB-RS485 adapter** (for example one based on the CH340 or FTDI chip)
- **RS485-to-TCP converter** in transparent mode. Do not use the Modbus gateway mode: the bus carries Tigo's own gateway protocol, not Modbus.

Serial settings: 38400 baud, 8 data bits, no parity, 1 stop bit.

## Setup

1. Go to **Settings** → **Devices & services** → **Add integration** and search for "Tigo RS485".
2. Select the connection type:
   - **Serial**: select the port. Paths under `/dev/serial/by-id/` are listed first; prefer them, because they stay the same when the adapter is plugged into another USB port.
   - **Network**: enter host and port of the converter.
3. Wait until the optimizers send data. This usually takes a few seconds while the panels produce power.
4. Open the integration options (**Configure**) and enter a label for each optimizer, for example `A1`. Only optimizers with a label get entities.

The options list each optimizer by the serial number printed on its label (for example `4-9A57A2L`), together with its current voltage and current. Until the serial number is known, the node ID assigned by the gateway is shown instead. The serial number is learned from topology reports and node tables on the bus; restarting the CCA makes it send a full node table.

## Entities

Each labeled optimizer becomes a device named `Tigo Optimizer <label>` with these sensors:

| Entity ID | Description | Unit |
|---|---|---|
| `sensor.panel_<label>_voltage_in` | Input voltage (panel) | V |
| `sensor.panel_<label>_voltage_out` | Output voltage | V |
| `sensor.panel_<label>_current` | Input current | A |
| `sensor.panel_<label>_power` | Input power (voltage in × current) | W |
| `sensor.panel_<label>_temperature` | Optimizer temperature | °C |
| `sensor.panel_<label>_pwm` | Duty cycle, raw value 0–255 | |
| `sensor.panel_<label>_rssi` | Signal strength, raw value (diagnostic, disabled by default) | |

## Migrating from home-assistant-tigo

The entity IDs, units and state classes match [home-assistant-tigo](https://github.com/mletenay/home-assistant-tigo), so you can switch without losing long-term statistics:

1. Remove the `tigo` integration. Otherwise the new entities get a `_2` suffix.
2. Set up this integration as described above.
3. Use the same labels as in the CCA (for example `A1`, `A2`, …).

The new entities continue the existing statistics, because Home Assistant stores them by entity ID. The device names differ (`Tigo Optimizer A1` instead of `Panel A1`), so they do not conflict.

The CCA temperature and the panel `status` and `online` values of home-assistant-tigo are not available on the RS485 bus.

## Troubleshooting

To see the received power reports and node table updates, enable debug logging:

```yaml
logger:
  logs:
    custom_components.tigo_rs485: debug
```

## Credits

- Frames are decoded with [tappipe](https://github.com/kicomoco/tap-pipe) (GPLv3). This integration works around some of its bugs.
- The protocol details and the serial number calculation come from [taptap](https://github.com/willglynn/taptap) by Will Glynn (MIT License).

## License

GNU General Public License v3.0, see [LICENSE](LICENSE).
