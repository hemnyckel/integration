#!/usr/bin/env bash
# Copy the integration into a live Home Assistant configuration directory.
#
#   tools/sync_to_ha.sh /path/to/homeassistant/config

set -euo pipefail

TARGET="${1:-}"
if [ -z "$TARGET" ] || [ ! -d "$TARGET" ]; then
  echo "usage: $0 /path/to/homeassistant/config" >&2
  exit 2
fi

SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESTINATION="$TARGET/custom_components/hemnyckel"

rm -rf "$DESTINATION"
cp -a "$SOURCE/custom_components/hemnyckel" "$DESTINATION"
find "$DESTINATION" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$DESTINATION" \( -name '*.pyc' -o -name '*.pyo' \) -delete

echo "synced hemnyckel -> $DESTINATION"
echo
echo "Reload the integration (Settings -> Devices & services) or restart Home Assistant."
