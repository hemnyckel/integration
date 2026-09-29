"""Where a person's photo is read from, and the pure rules around it.

The relay mirrors each person's photo to ``/share/hemnyckel/avatars/<person_id>.jpg``
(the add-on maps ``share:rw``), so Home Assistant can serve it without the bytes
ever travelling over MQTT or sitting on an open URL. This module holds only the
pure rules — the directory, the id shape, the entity tag — so they can be tested
without a Home Assistant install. The HTTP view itself lives in ``avatar_view.py``.
"""

from __future__ import annotations

import os
import re

# The relay maps ``share:rw`` and writes here; Home Assistant Core has the same
# folder mounted at ``/share``. The two halves agree on this one path.
SHARE_AVATAR_DIR = "/share/hemnyckel/avatars"

# A person id is the relay's stable identity: 32 lower-case hex characters
# (``uuid4().hex``). Nothing else may reach the filesystem, so a name or a
# traversal attempt can never name a file.
_PERSON_ID = re.compile(r"^[0-9a-f]{32}$")


def valid_person_id(value: object) -> bool:
    """Whether this is a person id the relay could have minted."""
    return isinstance(value, str) and bool(_PERSON_ID.match(value))


def avatar_path(person_id: object) -> str | None:
    """The mirrored photo's path, or None for anything that is not a person id."""
    if not valid_person_id(person_id):
        return None
    return os.path.join(SHARE_AVATAR_DIR, f"{person_id}.jpg")


def entity_tag(stat: os.stat_result) -> str:
    """A validator for a mirrored file, so an unchanged photo is a 304.

    Built from the file's own size and modification time; a re-mirrored photo
    changes at least the mtime, so the tag can never claim a stale photo is
    current.
    """
    mtime_ns = getattr(stat, "st_mtime_ns", None)
    if mtime_ns is None:  # a synthetic stat_result (tests) has no nanosecond field
        mtime_ns = int(stat.st_mtime * 1_000_000_000)
    return f'"{mtime_ns:x}-{stat.st_size:x}"'
