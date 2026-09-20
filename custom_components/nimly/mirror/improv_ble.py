"""Improv Wi-Fi over BLE - provisions the bridge from Home Assistant.

Uses the Home Assistant Bluetooth integration (Bleak) to connect to the bridge's
Improv GATT service and send Wi-Fi credentials. The same protocol as the serial
channel and as ESP Web Tools use.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from bleak import BleakClient
from bleak.exc import BleakError

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

SERVICE_UUID = "00467768-6228-2272-4663-277478268000"
STATUS_UUID = "00467768-6228-2272-4663-277478268001"
ERROR_UUID = "00467768-6228-2272-4663-277478268002"
RPC_COMMAND_UUID = "00467768-6228-2272-4663-277478268003"
RPC_RESULT_UUID = "00467768-6228-2272-4663-277478268004"
CAPABILITIES_UUID = "00467768-6228-2272-4663-277478268005"

CMD_WIFI_SETTINGS = 0x01
CMD_GET_CURRENT_STATE = 0x02
CMD_GET_DEVICE_INFO = 0x03
CMD_GET_WIFI_NETWORKS = 0x04

STATE_STOPPED = 0x00
STATE_AUTHORIZED = 0x01
STATE_PROVISIONING = 0x02
STATE_PROVISIONED = 0x03

ERROR_UNABLE_TO_CONNECT = 0x03


@dataclass
class ImprovDeviceInfo:
    """What the device reports via GET_DEVICE_INFO."""

    name: str = ""
    firmware: str = ""
    chip: str = ""
    device: str = ""
    state: int | None = None
    raw: list[str] = field(default_factory=list)


def build_rpc(command: int, data: bytes = b"") -> bytes:
    """Builds an Improv RPC payload: [cmd][length][data...][checksum]."""
    payload = bytes([command, len(data)]) + data
    return payload + bytes([sum(payload) & 0xFF])


def _parse_rpc(payload: bytes) -> list[str]:
    """Parses an RPC payload: [cmd][length][length-prefixed strings]."""
    if len(payload) < 2:
        return []
    total = payload[1]
    out: list[str] = []
    pos = 2
    end = min(len(payload), 2 + total)
    while pos < end:
        length = payload[pos]
        pos += 1
        out.append(payload[pos : pos + length].decode(errors="replace"))
        pos += length
    return out


async def async_fetch_info(hass: HomeAssistant, address: str) -> ImprovDeviceInfo:
    """Connects and fetches the device info and the current state."""
    device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
    if device is None:
        raise RuntimeError("The device is not reachable over Bluetooth")

    info = ImprovDeviceInfo()
    async with BleakClient(device, timeout=20) as client:
        await client.write_gatt_char(
            RPC_COMMAND_UUID, build_rpc(CMD_GET_DEVICE_INFO), response=True
        )
        await asyncio.sleep(1.0)
        raw = await client.read_gatt_char(RPC_RESULT_UUID)
        strings = _parse_rpc(bytes(raw))
        info.raw = strings
        if len(strings) >= 4:
            info.name, info.firmware, info.chip, info.device = strings[:4]
        state = await client.read_gatt_char(STATUS_UUID)
        info.state = state[0] if state else None
    return info


async def async_provision(
    hass: HomeAssistant,
    address: str,
    ssid: str,
    password: str,
    timeout: int = 40,
) -> None:
    """Sends Wi-Fi credentials and waits for the device to be provisioned."""
    device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
    if device is None:
        raise RuntimeError("The device is not reachable over Bluetooth")

    data = (
        bytes([len(ssid)])
        + ssid.encode()
        + bytes([len(password)])
        + password.encode()
    )

    try:
        async with BleakClient(device, timeout=20) as client:
            await client.write_gatt_char(
                RPC_COMMAND_UUID, build_rpc(CMD_WIFI_SETTINGS, data), response=True
            )
            _LOGGER.info("Wi-Fi credentials sent to %s - waiting for connection", address)

            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            while loop.time() < deadline:
                state = await client.read_gatt_char(STATUS_UUID)
                if state and state[0] == STATE_PROVISIONED:
                    _LOGGER.info("The device is provisioned and connected")
                    return
                if state and state[0] == STATE_STOPPED:
                    error = await client.read_gatt_char(ERROR_UUID)
                    if error and error[0] == ERROR_UNABLE_TO_CONNECT:
                        raise RuntimeError("The device could not connect to the network")
                await asyncio.sleep(1.5)
    except BleakError as err:
        raise RuntimeError(f"BLE error: {err}") from err

    raise RuntimeError("The device never reported that it was provisioned")
