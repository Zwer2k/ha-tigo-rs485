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

### Wiring

The adapter is connected in parallel to the existing cable between the CCA and the TAP, for example directly at the gateway terminals of the CCA. The cable to the TAP stays connected.

```text
 ┌───────────────────┐                        ┌───────────────────┐
 │    Tigo CCA       │                        │     Tigo TAP      │
 │  gateway terminal │                        │    (gateway)      │
 │                   │                        │                   │
 │              +  ●─┼────────────────────────┼─● +               │
 │              A  ●─┼──────────┬─────────────┼─● A               │
 │              B  ●─┼──────────┼──┬──────────┼─● B               │
 │            GND  ●─┼──────────┼──┼──┬───────┼─● GND             │
 └───────────────────┘          │  │  │       └───────────────────┘
                                │  │  │
                         ┌──────●──●──●──────┐
                         │      A  B GND     │
                         │ USB-RS485 adapter │       ┌────────────────┐
                         │ or RS485-to-TCP   ├───────┤ Home Assistant │
                         │ converter         │  USB  └────────────────┘
                         └───────────────────┘ or LAN
```

- Connect **A to A** and **B to B**. If no data arrives, swap A and B: some manufacturers label them the other way round.
- Connect **GND** if the adapter has a GND terminal. It gives both sides the same reference and makes the reception more reliable.
- Do **not** connect the supply wire (`+`) to the adapter. It powers the TAP.
- Do **not** enable a termination resistor on the adapter. The adapter is only an extra listener on the bus.
- Keep the wires from the bus to the adapter short.

## Setup

1. Go to **Settings** → **Devices & services** → **Add integration** and search for "Tigo RS485".
2. Select the connection type:
   - **Serial**: select the port. Paths under `/dev/serial/by-id/` are listed first; prefer them, because they stay the same when the adapter is plugged into another USB port.
   - **Network**: enter host and port of the converter.
3. Wait until the optimizers send data. This usually takes a few seconds while the panels produce power.
4. Open the integration options (**Configure**) and enter a label for each optimizer, for example `A1`. Only optimizers with a label get entities.
5. In the next step, check the string of each optimizer. It is suggested from the label, see [Strings and installation](#strings-and-installation).

The options list each optimizer by the serial number printed on its label (for example `4-9A57A2L`), together with its current voltage and current. Until the serial number is known, the node ID assigned by the gateway is shown instead.

### When do the serial numbers appear?

The power reports only contain the node ID, not the serial number. The serial number is calculated from the long address of the optimizer, which only appears in two kinds of messages on the bus:

- **Topology reports:** each optimizer sends them on its own from time to time, while its panel produces power. In a system with 16 optimizers, the serial numbers of all optimizers were known after less than 30 minutes, without restarting the CCA.
- **Node table:** a complete list of all optimizers. The CCA requests it when it starts. It is not verified yet that a restart of the CCA reliably triggers it.

**You do not need to disconnect the CCA from power.** Wait until the serial numbers appear in the options, during the day while the panels produce power. If you do not want to wait, you can try restarting the CCA.

The integration stores the serial numbers, so they are available right away after restarting Home Assistant. You can already assign labels by node ID before the serial number is known. They are kept when the serial number appears.

### Update interval

The optimizers report every few seconds, which leads to many state writes. In the options, you can choose an update interval of up to 10 minutes. The values are then averaged over that time and written once. The average is weighted by time, the same way the energy is calculated, so a lost report does not distort it and the average power matches the energy of the interval. Gaps of more than 2 minutes are left out. The default is to write every report as it arrives.

When an optimizer stops reporting for 2 minutes, for example after sunset, its voltage, current and power sensors change to `0` and the other sensors to unknown.

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
| `sensor.panel_<label>_energy_today` | Energy since midnight | kWh |
| `sensor.panel_<label>_energy_total` | Energy since the label was assigned | kWh |

### Strings and installation

After the labels, the options ask for the string of each labeled optimizer. The string is suggested from the label: the part before the number, so `A1` and `A-12` suggest `A`, and `Dach links 3` suggests `Dach links`. You can change the suggestion, or clear it for optimizers that do not belong to a string. Each string becomes a device named `Tigo String <name>`. Until the options are saved once, the suggestions are used. The device `Tigo Installation` covers all optimizers on the bus, also those without a label.

| Entity ID | Description | Unit |
|---|---|---|
| `sensor.tigo_string_<name>_power` | Input power of the string | W |
| `sensor.tigo_string_<name>_energy_today` | Energy of the string since midnight | kWh |
| `sensor.tigo_string_<name>_energy_total` | Energy of the string | kWh |
| `sensor.tigo_installation_power` | Input power of all optimizers | W |
| `sensor.tigo_installation_energy_today` | Energy of all optimizers since midnight | kWh |
| `sensor.tigo_installation_energy_total` | Energy of all optimizers | kWh |

The energy sensors can be used in the energy dashboard.

### Energy

The energy is calculated from the input power of every report, also when an update interval is set. When an optimizer does not report for more than 2 minutes, for example at night or while the connection is lost, nothing is added for that time. The values are stored and kept across restarts. The daily energy starts at 0 at midnight.

When a label is removed or changed, the energy of the old label is removed with its entities. The energy of the strings and the installation keeps counting.

## Migrating from home-assistant-tigo

The entity IDs, units and state classes match [home-assistant-tigo](https://github.com/mletenay/home-assistant-tigo), so you can switch without losing long-term statistics:

1. Remove the `tigo` integration. Otherwise the new entities get a `_2` suffix.
2. Set up this integration as described above.
3. Use the same labels as in the CCA (for example `A1`, `A2`, …).

The new entities continue the existing statistics, because Home Assistant stores them by entity ID. The device names differ (`Tigo Optimizer A1` instead of `Panel A1`), so they do not conflict.

The CCA temperature and the panel `status` and `online` values of home-assistant-tigo are not available on the RS485 bus.

## Troubleshooting

The diagnostics (**Settings** → **Devices & services** → **Tigo RS485** → ⋮ → **Download diagnostics**) contain the number of received frames and reports, the known optimizers with their serial number, label and last report, and the stored energy. The host of a network converter is removed.

When the connection to the adapter or converter is lost, for example because `ser2net` or the converter restarts, the integration connects again on its own. While it is disconnected, the entities are unavailable. A TCP connection that receives no data for 60 seconds is opened again, because the CCA polls the gateway all the time.

To see the received power reports and node table updates, enable debug logging:

```yaml
logger:
  logs:
    custom_components.tigo_rs485: debug
```

## Changelog

### 1.1.0 (2026-11-03)

- Daily and total energy for each labeled optimizer.
- Power and energy for each string and for the whole installation. The strings are assigned in a second step of the options, suggested from the labels (for example `A` for `A1`).
- Download of diagnostics.
- Translated sensor names (English, German), shown with the device name, for example `Tigo Optimizer A1 Voltage in` instead of `Panel A1 voltage_in`. The entity IDs stay `sensor.panel_<label>_<key>`, also on newer Home Assistant versions that would otherwise add the device name to them.
- The update interval averages the values weighted by time instead of the plain mean of the reports.
- The entities of all labeled optimizers are created right away, not only after the optimizer reported. The energy is therefore shown also after a restart at night.

### 1.0.2 (2026-10-30)

- Removed `pyserial` from the requirements. It is already included in Home Assistant, and listing it made the hassfest validation fail.

### 1.0.1 (2026-10-20)

- When the connection to the adapter or converter is lost, the integration connects again on its own. It waits 5 seconds and doubles the wait up to 60 seconds. Before, only a reload of the integration helped, for example after `ser2net` or the Raspberry Pi restarted ([#1](https://github.com/Zwer2k/ha-tigo-rs485/issues/1)).
- A TCP connection that receives no data for 60 seconds is opened again. This covers converters that restart without closing the connection.
- Entities are unavailable while the bus cannot be read.

### 1.0.0 (2026-10-14)

- First release.
- Optional update interval of 10 seconds to 10 minutes, with the reports averaged over that time. The default is to write every report.
- Optimizers that do not report for 2 minutes show `0` for voltage, current and power, and unknown for the other sensors.
- The options dialog also opens before any optimizer has reported.

## Credits

- Frames are decoded with [tappipe](https://github.com/kicomoco/tap-pipe) (GPLv3). This integration works around some of its bugs.
- The protocol details and the serial number calculation come from [taptap](https://github.com/willglynn/taptap) by Will Glynn (MIT License).

## License

GNU General Public License v3.0, see [LICENSE](LICENSE).
