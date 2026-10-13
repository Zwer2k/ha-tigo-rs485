"""Sensor platform for Tigo RS485 integration."""

import logging
import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
import struct
from typing import Any

import serial
import tappipe


from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    CoordinatorEntity,
)
from .barcode import barcode
from .const import CONF_LABELS, DOMAIN

CONF_CONNECTION_TYPE = "connection_type"
CONF_SERIAL_PORT = "serial_port"
CONF_HOST = "host"
CONF_PORT = "port"

_LOGGER = logging.getLogger(__name__)

FRAME_START = b"\x7e\x07"
FRAME_END = b"\x7e\x08"
MAX_BUFFER = 4096
STORAGE_VERSION = 1
# All Tigo long addresses start with this prefix
ADDRESS_PREFIX = bytes((0x04, 0xC0, 0x5B))
ADDRESS_PREFIX_TEXT = "04:C0:5B"


def _check_crc(self) -> bool:
    """Check the frame CRC.

    tappipe 0.0.5 shadows its own `crc` class with a local int in
    `frame.checkCRC`, which raises TypeError on every frame.
    """
    if len(self.bytes) < 7:
        return False
    expected = (self.bytes[-4] << 8) + self.bytes[-3]
    return tappipe.crc(self.bytes[3:-4]).check() == expected


tappipe.frame.checkCRC = _check_crc



@dataclass(frozen=True, kw_only=True)
class TigoSensorEntityDescription(SensorEntityDescription):
    """Describes a Tigo optimizer sensor."""

    value_fn: Callable[[dict[str, Any]], StateType]


def _power(data: dict[str, Any]) -> float | None:
    """Return the input power of an optimizer."""
    if data.get("vin") is None or data.get("ampsin") is None:
        return None
    return round(data["vin"] * data["ampsin"], 2)


def _rounded(key: str, digits: int) -> Callable[[dict[str, Any]], float | None]:
    """Return a getter that rounds the given value."""
    return lambda data: None if data.get(key) is None else round(data[key], digits)


# Keys, units and state classes match home-assistant-tigo, so the
# resulting entity IDs and long-term statistics can be taken over
OPTIMIZER_SENSORS: tuple[TigoSensorEntityDescription, ...] = (
    TigoSensorEntityDescription(
        key="voltage_in",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
        value_fn=_rounded("vin", 2),
    ),
    TigoSensorEntityDescription(
        key="voltage_out",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
        value_fn=_rounded("vout", 1),
    ),
    TigoSensorEntityDescription(
        key="current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
        value_fn=_rounded("ampsin", 3),
    ),
    TigoSensorEntityDescription(
        key="power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=1,
        value_fn=_power,
    ),
    TigoSensorEntityDescription(
        key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value_fn=_rounded("temp", 1),
    ),
    TigoSensorEntityDescription(
        key="pwm",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:numeric",
        value_fn=lambda data: data.get("duty"),
    ),
    TigoSensorEntityDescription(
        key="rssi",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.get("rssi"),
    ),
)


class TappipeReaderBase:
    """Abstract base for Tappipe readers (serial or TCP)."""

    def __init__(self, data_callback, address_callback):
        self._data_callback = data_callback
        self._address_callback = address_callback
        self._buffer = bytearray()
        self._running = False
        self._task = None

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._read_loop())

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _read_loop(self):
        raise NotImplementedError

    def _process_data(self, data):
        """Buffer received bytes and handle all complete frames."""
        self._buffer += data
        while (start := self._buffer.find(FRAME_START)) != -1:
            end = self._buffer.find(FRAME_END, start + len(FRAME_START))
            if end == -1:
                break
            raw = self._buffer[start : end + len(FRAME_END)]
            del self._buffer[: end + len(FRAME_END)]
            self._handle_frame(raw)
        # Keep a possibly incomplete start marker, drop leftover noise
        if FRAME_START not in self._buffer:
            del self._buffer[:-1]
        elif len(self._buffer) > MAX_BUFFER:
            self._buffer.clear()

    def _handle_frame(self, raw):
        """Decode a single frame and forward the contained reports."""
        # Gateway responses start with 7e 07 without the ff preamble that
        # tappipe expects, so always add it before handing the frame over
        fr = tappipe.frame(b"\xff" + raw)
        if fr.getType() is None:
            return
        try:
            fr.process()
        except struct.error:
            _LOGGER.debug("Ignoring truncated frame: %s", raw.hex(" "))
            return
        if not (fr.processor and hasattr(fr.processor, "decoded")):
            return
        # Node table, sent when the CCA (re)builds its view of the network
        if isinstance(fr.processor, tappipe.cmd_resp) and isinstance(
            fr.processor.processor, tappipe.node_table
        ):
            table = fr.processor.processor.decoded["table"]
            _LOGGER.debug("Node table received with %s entries", len(table))
            for node_id, address in table.items():
                self._address_callback(node_id, address)
            return
        for pkt in fr.processor.decoded.get("packets", []):
            if pkt.getType() == tappipe.pvtype.TOPOLOGY_REPORT.value:
                # tappipe reads the address 6 bytes too early. After the
                # 7 byte packet header follow short address, node ID, next
                # hop and 2 unknown bytes, then the long address (see taptap)
                address = bytes(pkt.bytes[15:23])
                if len(address) == 8:
                    self._address_callback(pkt.decoded["nodeid"], address)
            elif pkt.getType() == tappipe.pvtype.POWER_REPORT.value:
                if pkt.decoded["nodeid"] is not None:
                    _LOGGER.debug("Power report: %s", pkt.decoded)
                    self._data_callback(pkt.decoded["nodeid"], pkt.decoded)


class TappipeSerialReader(TappipeReaderBase):
    def __init__(self, port, data_callback, address_callback):
        super().__init__(data_callback, address_callback)
        self._port = port
        self._serial = None

    async def stop(self):
        await super().stop()
        if self._serial:
            self._serial.close()
            self._serial = None

    async def _read_loop(self):
        loop = asyncio.get_running_loop()
        self._serial = await loop.run_in_executor(
            None,
            partial(
                serial.Serial,
                self._port,
                baudrate=38400,
                bytesize=8,
                timeout=0.2,
                stopbits=serial.STOPBITS_ONE,
            ),
        )
        _LOGGER.info("Serial connection to %s established successfully", self._port)
        while self._running:
            data = await loop.run_in_executor(None, self._serial.read, 256)
            try:
                self._process_data(data)
            except Exception:
                _LOGGER.exception("Error while processing received data")


# Raw byte stream from an RS485-to-TCP converter. The bus carries Tigo's
# own gateway protocol, not Modbus, so the converter must pass bytes through
class TappipeTCPReader(TappipeReaderBase):
    def __init__(self, host, port, data_callback, address_callback):
        super().__init__(data_callback, address_callback)
        self._host = host
        self._port = port

    async def _read_loop(self):
        reader, _ = await asyncio.open_connection(self._host, self._port)
        while self._running:
            data = await reader.read(256)
            if not data:
                _LOGGER.warning("TCP connection closed by remote side")
                break
            try:
                self._process_data(data)
            except Exception:
                _LOGGER.exception("Error while processing received data")


def format_address(address: bytes) -> str:
    """Return the long address of an optimizer as text."""
    return address.hex(":").upper()


class TigoCoordinator(DataUpdateCoordinator):
    """Collect the reports received from the bus."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=None,  # Push-based
        )
        self.entry = entry
        self.labels: dict[str, str] = entry.options.get(CONF_LABELS, {})
        self.addresses: dict[int, str] = {}  # node ID -> long address
        # Wrongly decoded addresses of older versions -> node ID, so labels
        # assigned to them keep working until the options are saved again
        self.legacy_keys: dict[str, int] = {}
        self.optimizer_data: dict[int, dict[str, Any]] = {}  # node ID -> report
        self.reader = None
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )

    async def _async_update_data(self):
        # Not used, push-based
        return self.optimizer_data

    def serial_number(self, node_id: int) -> str | None:
        """Return the barcode printed on the optimizer, if known."""
        if (address := self.addresses.get(node_id)) is None:
            return None
        return barcode(bytes.fromhex(address.replace(":", ""))) or address

    def label_key(self, node_id: int) -> str:
        """Return the key used to store the label of an optimizer.

        The barcode never changes and matches the label on the optimizer, so
        it is preferred over the node ID that the gateway may reassign.
        """
        return self.serial_number(node_id) or str(node_id)

    def label_for(self, node_id: int) -> str | None:
        """Return the label assigned to an optimizer."""
        # Labels may also have been assigned by long address or node ID
        for key in (
            self.label_key(node_id),
            self.addresses.get(node_id),
            str(node_id),
        ):
            if key is not None and (label := self.labels.get(key)) is not None:
                return label
        for key, legacy_node_id in self.legacy_keys.items():
            if legacy_node_id == node_id and (label := self.labels.get(key)):
                return label
        return None

    def node_for(self, label: str) -> int | None:
        """Return the current node ID of the optimizer with the given label."""
        for node_id in self.optimizer_data:
            if self.label_for(node_id) == label:
                return node_id
        return None

    @callback
    def _on_data(self, node_id: int, data: dict[str, Any]) -> None:
        self.optimizer_data[node_id] = data
        self.async_set_updated_data(self.optimizer_data)

    @callback
    def _on_address(self, node_id: int, address: bytes) -> None:
        if not address.startswith(ADDRESS_PREFIX):
            _LOGGER.debug(
                "Ignoring invalid address %s for node ID %s", address.hex(":"), node_id
            )
            return
        text = format_address(address)
        if self.addresses.get(node_id) == text:
            return
        _LOGGER.debug("Optimizer node ID %s has address %s", node_id, text)
        # A node ID is only used by one optimizer at a time
        for other, other_text in list(self.addresses.items()):
            if other_text == text:
                del self.addresses[other]
        self.addresses[node_id] = text
        self._store.async_delay_save(self._data_to_store, 10)
        self.async_update_listeners()

    @callback
    def _data_to_store(self) -> dict[str, Any]:
        return {"addresses": {str(k): v for k, v in self.addresses.items()}}

    async def async_start(self) -> None:
        """Load the known addresses and start reading from the bus."""
        if stored := await self._store.async_load():
            for key, text in stored.get("addresses", {}).items():
                if text.startswith(ADDRESS_PREFIX_TEXT):
                    self.addresses[int(key)] = text
                else:
                    # Stored by an older version that decoded addresses wrong
                    self.legacy_keys[text] = int(key)
        typ = self.entry.data.get(CONF_CONNECTION_TYPE, "serial")
        if typ == "tcp":
            self.reader = TappipeTCPReader(
                self.entry.data[CONF_HOST],
                self.entry.data[CONF_PORT],
                self._on_data,
                self._on_address,
            )
        else:
            self.reader = TappipeSerialReader(
                self.entry.data[CONF_SERIAL_PORT], self._on_data, self._on_address
            )
        await self.reader.start()

    async def async_stop(self) -> None:
        """Stop reading and save the known addresses."""
        if self.reader:
            await self.reader.stop()
        await self._store.async_save(self._data_to_store())


def _unique_id(entry: ConfigEntry, label: str, key: str) -> str:
    """Return the unique ID of an optimizer sensor.

    It only depends on the label, which also defines the entity ID, so
    learning the long address or a new node ID keeps the same entities.
    """
    return f"{entry.entry_id}_{label}_{key}"


def _device_identifier(entry: ConfigEntry, label: str) -> tuple[str, str]:
    """Return the device identifier of a labeled optimizer."""
    return (DOMAIN, f"{entry.entry_id}_{label}")


@callback
def _async_remove_stale(
    hass: HomeAssistant, entry: ConfigEntry, labels: dict[str, str]
) -> None:
    """Remove entities and devices of unlabeled or relabeled optimizers."""
    expected = {
        _unique_id(entry, label, description.key)
        for label in labels.values()
        for description in OPTIMIZER_SENSORS
    }
    ent_reg = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if entity.unique_id not in expected:
            ent_reg.async_remove(entity.entity_id)

    expected_devices = {_device_identifier(entry, label) for label in labels.values()}
    dev_reg = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        if not device.identifiers & expected_devices:
            dev_reg.async_update_device(
                device.id, remove_config_entry_id=entry.entry_id
            )


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Tigo RS485 sensors from a config entry."""
    coordinator = TigoCoordinator(hass, entry)
    _async_remove_stale(hass, entry, coordinator.labels)
    await coordinator.async_start()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Entities are only created for labeled optimizers, so the entity IDs
    # match home-assistant-tigo (sensor.panel_<label>_<key>) right away
    known_labels: set[str] = set()
    reported_nodes: set[int] = set()

    @callback
    def _check_new_optimizers() -> None:
        """Add entities for newly discovered optimizers."""
        new_entities = []
        for node_id in coordinator.optimizer_data:
            if (label := coordinator.label_for(node_id)) is None:
                if node_id not in reported_nodes:
                    reported_nodes.add(node_id)
                    _LOGGER.info(
                        "Optimizer %s found, assign a label in the integration "
                        "options to add it",
                        coordinator.label_key(node_id),
                    )
                continue
            if label in known_labels:
                continue
            known_labels.add(label)
            new_entities.extend(
                TigoOptimizerSensor(coordinator, label, description)
                for description in OPTIMIZER_SENSORS
            )
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_check_new_optimizers))
    entry.async_on_unload(coordinator.async_stop)


class TigoOptimizerSensor(CoordinatorEntity[TigoCoordinator], SensorEntity):
    """Sensor of a single Tigo optimizer."""

    entity_description: TigoSensorEntityDescription

    def __init__(
        self,
        coordinator: TigoCoordinator,
        label: str,
        description: TigoSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._label = label
        entry = coordinator.entry
        # Same naming as home-assistant-tigo, the device name stays distinct
        # to avoid conflicts with its "Panel <label>" devices
        self._attr_name = f"Panel {label} {description.key}"
        self._attr_unique_id = _unique_id(entry, label, description.key)
        self._attr_device_info = self._device_info()

    def _device_info(self) -> DeviceInfo:
        """Return the device info, including the address once it is known."""
        info = DeviceInfo(
            identifiers={_device_identifier(self.coordinator.entry, self._label)},
            name=f"Tigo Optimizer {self._label}",
            manufacturer="Tigo",
            model="Optimizer",
        )
        node_id = self.coordinator.node_for(self._label)
        if node_id is not None and (serial := self.coordinator.serial_number(node_id)):
            info["serial_number"] = serial
        return info

    @callback
    def _handle_coordinator_update(self) -> None:
        """Update the device serial number when the address becomes known."""
        node_id = self.coordinator.node_for(self._label)
        serial = (
            self.coordinator.serial_number(node_id) if node_id is not None else None
        )
        if serial and self.device_entry and self.device_entry.serial_number != serial:
            dr.async_get(self.hass).async_update_device(
                self.device_entry.id, serial_number=serial
            )
        super()._handle_coordinator_update()

    @property
    def _data(self) -> dict[str, Any] | None:
        """Return the last power report of this optimizer."""
        if (node_id := self.coordinator.node_for(self._label)) is None:
            return None
        return self.coordinator.optimizer_data.get(node_id)

    @property
    def available(self) -> bool:
        """Return if a power report was received."""
        return super().available and self._data is not None

    @property
    def native_value(self) -> StateType:
        """Return the value of this sensor."""
        if (data := self._data) is None:
            return None
        return self.entity_description.value_fn(data)
