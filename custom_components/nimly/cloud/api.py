"""Async client for the Nimly (Iotiliti) cloud.

Auth is OAuth2 password grant with a rotating refresh token. Refresh is single-flight: the
provider invalidates the old refresh token, so two concurrent refreshes would both fail.

Endpoint shapes were determined for interoperability by observing the responses of the
account owner's own devices. See docs/privacy.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any
from collections.abc import Callable

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..const import (
    API_CLIENT_ID,
    API_CLIENT_SECRET,
    API_URL,
    HEADER_COMPANY_ID,
    PATH_DEVICE,
    PATH_DEVICE_ACCESS,
    PATH_DEVICE_ACTION,
    PATH_DEVICE_HISTORY,
    PATH_DEVICE_LOCK,
    PATH_DEVICE_SETTINGS,
    PATH_GATEWAY_ACTION,
    PATH_HOME,
    PATH_LOCATION_USERS,
    PATH_LOCATIONS,
    PATH_ME,
    PATH_REFRESH,
    PATH_TOKEN,
    TOKEN_REFRESH_MARGIN,
)

_LOGGER = logging.getLogger(__name__)

# Anything that looks like a credential is masked before a probe body is returned.
_SCRUB = re.compile(
    r'("(?:[a-z_]*token|[a-z_]*password|[a-z_]*secret|[a-z_]*pin[a-z_]*|[a-z_]*code)"'
    r"\s*:\s*)\"[^\"]*\"",
    re.IGNORECASE,
)


def _scrub(text: str) -> str:
    return _SCRUB.sub(r'\1"**REDACTED**"', text)


class NimlyCloudAuthError(Exception):
    """Raised when the account cannot be authenticated."""


class NimlyCloudError(Exception):
    """Raised for a failed (non-auth) API call."""


class NimlyCloudApi:
    """Thin async wrapper around the vendor API."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        access_token: str | None = None,
        refresh_token: str | None = None,
        on_tokens: Callable[[str, str], None] | None = None,
    ) -> None:
        self._hass = hass
        self._session = async_get_clientsession(hass)
        self._access = access_token
        self._refresh = refresh_token
        self._on_tokens = on_tokens
        self._expires_at = 0.0
        self._lock = asyncio.Lock()
        self.company_id: str | None = None

    # -- auth ---------------------------------------------------------------

    @property
    def access_token(self) -> str | None:
        return self._access

    @property
    def refresh_token(self) -> str | None:
        return self._refresh

    def _store(self, token: dict[str, Any]) -> None:
        self._access = token["access_token"]
        if token.get("refresh_token"):
            self._refresh = token["refresh_token"]
        expires = int(token.get("expires_in") or 1800)
        self._expires_at = time.monotonic() + max(expires - TOKEN_REFRESH_MARGIN, 30)
        if self._on_tokens and self._access and self._refresh:
            self._on_tokens(self._access, self._refresh)

    async def _token_request(self, path: str, data: dict[str, str]) -> dict[str, Any]:
        """Run an auth request; only a rejected credential is an auth error.

        A network problem or a 5xx from the vendor must not look like bad
        credentials, or Home Assistant would ask the user to sign in again for
        an outage.
        """
        try:
            async with self._session.post(
                API_URL + path,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=aiohttp.ClientTimeout(total=25),
            ) as resp:
                body = await resp.text()
                if resp.status in (400, 401, 403):
                    raise NimlyCloudAuthError(f"auth failed ({resp.status})")
                if resp.status >= 400:
                    raise NimlyCloudError(f"auth endpoint {resp.status}: {body[:120]}")
                try:
                    return json.loads(body)
                except ValueError as err:
                    raise NimlyCloudError(f"auth response was not JSON: {err}") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise NimlyCloudError(f"auth request failed: {err}") from err

    async def async_login(self, email: str, password: str) -> None:
        token = await self._token_request(
            PATH_TOKEN,
            {
                "grant_type": "password",
                "client_id": API_CLIENT_ID,
                "client_secret": API_CLIENT_SECRET,
                "username": email,
                "password": password,
            },
        )
        self._store(token)

    async def async_ensure_token(self) -> None:
        if self._access and time.monotonic() < self._expires_at:
            return
        async with self._lock:
            if self._access and time.monotonic() < self._expires_at:
                return
            if not self._refresh:
                raise NimlyCloudAuthError("no refresh token")
            token = await self._token_request(
                PATH_REFRESH,
                {
                    "grant_type": "refresh_token",
                    "client_id": API_CLIENT_ID,
                    "client_secret": API_CLIENT_SECRET,
                    "refresh_token": self._refresh,
                },
            )
            self._store(token)

    # -- requests -----------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        extra_headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> Any:
        await self.async_ensure_token()
        headers = {"Authorization": f"Bearer {self._access}"}
        if extra_headers:
            headers.update(extra_headers)
        try:
            async with self._session.request(
                method,
                API_URL + path,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=25),
                **kwargs,
            ) as resp:
                if resp.status == 401:
                    raise NimlyCloudAuthError("token rejected")
                if resp.status == 404:
                    return None
                if resp.status >= 400:
                    body = await resp.text()
                    raise NimlyCloudError(
                        f"{method} {path} -> {resp.status}: {_scrub(body)[:200]}"
                    )
                if resp.status == 204:
                    return None
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise NimlyCloudError(f"{method} {path} failed: {err}") from err

    async def async_get(self, path: str, params: dict[str, str] | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def async_post(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        return await self._request("POST", path, json=payload)

    async def async_patch(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        return await self._request("PATCH", path, json=payload)

    # -- account ------------------------------------------------------------

    async def async_me(self) -> dict[str, Any]:
        return await self.async_get(PATH_ME) or {}

    async def async_locations(self) -> list[dict[str, Any]]:
        locations = await self.async_get(PATH_LOCATIONS) or []
        for location in locations:
            if location.get("companyId"):
                # The history endpoint wants it on every request; remember it once.
                self.company_id = location["companyId"]
                break
        return locations

    async def async_location_users(self, location_id: str) -> list[dict[str, Any]]:
        return await self.async_get(
            PATH_LOCATION_USERS.format(location_id=location_id)
        ) or []

    async def async_home(self, location_id: str) -> dict[str, Any]:
        return await self.async_get(PATH_HOME.format(location_id=location_id)) or {}

    # -- devices ------------------------------------------------------------

    async def async_device(self, device_id: str) -> dict[str, Any]:
        return await self.async_get(PATH_DEVICE.format(device_id=device_id)) or {}

    async def async_device_access(self, device_id: str) -> list[dict[str, Any]]:
        return await self.async_get(
            PATH_DEVICE_ACCESS.format(device_id=device_id)
        ) or []

    async def async_device_history(self, device_id: str) -> list[dict[str, Any]]:
        """The device's activity feed, newest first.

        The endpoint requires the location's company identifier in a request header, the
        same way the vendor app sends it.
        """
        headers = {HEADER_COMPANY_ID: self.company_id} if self.company_id else None
        result = await self._request(
            "GET",
            PATH_DEVICE_HISTORY.format(device_id=device_id),
            extra_headers=headers,
        )
        return result or []

    # -- control (optional: the local mirror layer is the primary one) ----------

    async def async_set_lock(self, device_id: str, locked: bool) -> Any:
        """Ask the cloud to lock or unlock.

        The request shape follows the vendor app; the response is returned so a caller can
        see exactly what the cloud did with it.
        """
        return await self.async_post(
            PATH_DEVICE_LOCK.format(device_id=device_id), {"lock": locked}
        )

    async def async_update_device_settings(
        self, device_id: str, settings: dict[str, Any]
    ) -> Any:
        """Write the app's record of a device's settings (autolock, volume, ...).

        Verified on hardware: `autolock` (bool) and `volume` (silent/low/high) are
        accepted; the response is the updated settings object.
        """
        return await self.async_patch(
            PATH_DEVICE_SETTINGS.format(device_id=device_id), settings
        )

    async def async_device_action(
        self, device_id: str, feature: str, action: str
    ) -> Any:
        return await self.async_post(
            PATH_DEVICE_ACTION.format(device_id=device_id),
            {"feature": feature, "action": action},
        )

    async def async_gateway_scan(self, gateway_id: str, start: bool = True) -> Any:
        """Open or close the bridge's join window, so a device can be paired from HA.

        The vendor's own actions are ``scan.turnOn`` and ``scan.turnOff`` (probed on a
        real bridge: ``start``/``stop`` answer 2014 "Wrong action parameters").
        """
        return await self.async_post(
            PATH_GATEWAY_ACTION.format(gateway_id=gateway_id),
            {"feature": "scan", "action": "turnOn" if start else "turnOff"},
        )

    # -- diagnostics --------------------------------------------------------

    async def async_probe(
        self,
        path: str,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Call any path and report status plus a redacted body. For API discovery only.

        Unlike the normal helpers this never raises and never hides a 404, so an unknown
        endpoint can be told apart from an empty one.
        """
        await self.async_ensure_token()
        send = {"Authorization": f"Bearer {self._access}"}
        if headers:
            send.update({str(key): str(value) for key, value in headers.items()})
        try:
            async with self._session.request(
                method.upper(),
                API_URL + path,
                headers=send,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                body = await resp.text()
                return {
                    "path": path,
                    "method": method.upper(),
                    "status": resp.status,
                    "bytes": len(body),
                    "body": _scrub(body)[:20000],
                }
        except (aiohttp.ClientError, TimeoutError) as err:
            return {"path": path, "method": method.upper(), "status": None, "error": str(err)}
