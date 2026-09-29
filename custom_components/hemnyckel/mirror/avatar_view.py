"""The authenticated HTTP view that serves a person's mirrored photo.

The relay publishes a person's ``entity_picture`` as
``/api/hemnyckel/avatar/<id>``; this view answers it. It is authentication like
any other Home Assistant API path (``requires_auth``), so a photo is never on an
open URL: only a signed-in Home Assistant user can fetch it, and the relay's
guest rule is untouched — a guest has no Home Assistant account and, in the app,
still sees no family. Only the JPEG the relay mirrored is served; a monogram or a
symbol has no bytes here (the card draws those from the entity's attributes), so
anything else is a 404.
"""

from __future__ import annotations

import logging
import os

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback

from ..const import DOMAIN
from .avatar import avatar_path, entity_tag

_LOGGER = logging.getLogger(__name__)

# Set once when the view is registered, so setting up a second config entry (a
# second lock) never registers the same URL twice.
DATA_AVATAR_VIEW = f"{DOMAIN}_avatar_view"


def _read(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


class HemnyckelAvatarView(HomeAssistantView):
    """``GET /api/hemnyckel/avatar/<person_id>`` — a person's photo."""

    url = "/api/hemnyckel/avatar/{person_id}"
    name = "api:hemnyckel:avatar"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def get(self, request: web.Request, person_id: str) -> web.StreamResponse:
        path = avatar_path(person_id)
        if path is None:
            return web.Response(status=404)
        try:
            stat = await self._hass.async_add_executor_job(os.stat, path)
        except OSError:
            # No mirror, or the photo is gone: not an error, just nothing here.
            return web.Response(status=404)
        tag = entity_tag(stat)
        if request.headers.get("If-None-Match") == tag:
            return web.Response(status=304, headers={"ETag": tag})
        try:
            data = await self._hass.async_add_executor_job(_read, path)
        except OSError:
            return web.Response(status=404)
        return web.Response(
            body=data,
            content_type="image/jpeg",
            headers={"ETag": tag, "Cache-Control": "private, max-age=3600"},
        )


@callback
def async_register_avatar_view(hass: HomeAssistant) -> None:
    """Register the view once, at component setup."""
    if hass.data.get(DATA_AVATAR_VIEW):
        return
    hass.http.register_view(HemnyckelAvatarView(hass))
    hass.data[DATA_AVATAR_VIEW] = True
    _LOGGER.debug("registered the hemnyckel avatar view")
