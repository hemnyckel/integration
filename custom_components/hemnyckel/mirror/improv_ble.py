"""Improv Wi-Fi over BLE - provisions the bridge from Home Assistant.

Uses the Home Assistant Bluetooth integration (Bleak) to connect to the bridge's
Improv GATT service and send Wi-Fi credentials. The same protocol as the serial
channel and as ESP Web Tools use.
"""

from __future__ import annotations

import asyncio
import logging

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
CONFIG_UUID = "00467768-6228-2272-4663-277478268006"

CMD_WIFI_SETTINGS = 0x01
CMD_GET_CURRENT_STATE = 0x02
CMD_GET_DEVICE_INFO = 0x03
CMD_GET_WIFI_NETWORKS = 0x04
CMD_CONFIG = 0x80

STATE_STOPPED = 0x00
STATE_AUTHORIZED = 0x01
STATE_PROVISIONING = 0x02
STATE_PROVISIONED = 0x03

ERROR_UNABLE_TO_CONNECT = 0x03


def build_rpc(command: int, data: bytes = b"") -> bytes:
    """Builds an Improv RPC payload: [cmd][length][data...][checksum]."""
    payload = bytes([command, len(data)]) + data
    return payload + bytes([sum(payload) & 0xFF])


async def async_provision(
    hass: HomeAssistant,
    address: str,
    ssid: str,
    password: str,
    timeout: int = 40,
    mqtt: tuple[str, str, str] | None = None,
) -> None:
    """Sends Wi-Fi (and optionally MQTT) credentials in one BLE session.

    MQTT settings go first over the encrypted config characteristic; the same
    session then hands over Wi-Fi. Sending both together matters because the
    bridge stops advertising once it is connected.
    """
    device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
    if device is None:
        raise RuntimeError("The device is not reachable over Bluetooth")

    ssid_bytes = ssid.encode()
    password_bytes = password.encode()
    if len(ssid_bytes) > 255 or len(password_bytes) > 255:
        raise ValueError("the Wi-Fi name or password is too long")
    data = (
        bytes([len(ssid_bytes)])
        + ssid_bytes
        + bytes([len(password_bytes)])
        + password_bytes
    )

    try:
        async with BleakClient(device, timeout=20) as client:
            if mqtt is not None:
                await _pair_if_possible(client)
                await client.write_gatt_char(
                    CONFIG_UUID, _config_payload(*mqtt), response=True
                )
                _LOGGER.info("MQTT settings sent to %s", address)
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


def _length_prefixed(value: str) -> bytes:
    raw = value.encode()
    if len(raw) > 255:
        raise ValueError("the field is too long")
    return bytes([len(raw)]) + raw


def _config_payload(uri: str, username: str, password: str) -> bytes:
    """Builds the MQTT-config RPC: [0x80][dlen][uri][user][pass][checksum]."""
    data = _length_prefixed(uri) + _length_prefixed(username) + _length_prefixed(password)
    payload = bytes([CMD_CONFIG, len(data)]) + data
    return payload + bytes([sum(payload) & 0xFF])


async def _pair_if_possible(client: BleakClient) -> None:
    """Pairs before writing secrets; CONFIG_UUID requires an encrypted link."""
    pair = getattr(client, "pair", None)
    if pair is None:
        return
    try:
        await pair()
    except Exception:  # already bonded, or the backend pairs implicitly
        _LOGGER.debug("BLE pairing not required or already bonded")


async def async_provision_mqtt(
    hass: HomeAssistant,
    address: str,
    uri: str,
    username: str,
    password: str,
    timeout: int = 20,
) -> None:
    """Sends MQTT connection settings over the encrypted config characteristic.

    The bridge stores them in NVS and restarts to apply them. The characteristic
    requires an encrypted link (BLE bonding / LE Secure Connections), so we pair
    before writing - credentials never travel over an open BLE link.
    """
    device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
    if device is None:
        raise RuntimeError("The device is not reachable over Bluetooth")

    try:
        async with BleakClient(device, timeout=timeout) as client:
            await _pair_if_possible(client)
            await client.write_gatt_char(
                CONFIG_UUID, _config_payload(uri, username, password), response=True
            )
            _LOGGER.info("MQTT settings sent to %s", address)
    except BleakError as err:
        raise RuntimeError(f"BLE error: {err}") from err
