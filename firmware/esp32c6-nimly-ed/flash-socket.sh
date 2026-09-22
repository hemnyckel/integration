#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Flasha Nimly ED till en ESP32-C6 över en nätverks-serial (ser2net/socat).
#
# Användning:  ./flash-socket.sh <ip>:<port>   t.ex. 192.168.1.50:3000
#
# Kräver att firmware är byggd (build/ finns) och att esptool är tillgängligt
# (ingår i ESP-IDF; körs här som `python -m esptool`).

set -euo pipefail

PORT="${1:-}"
if [ -z "$PORT" ]; then
    echo "Användning: $0 <ip>:<port>" >&2
    exit 1
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
BUILD="${HERE}/build"

if [ ! -f "${BUILD}/flash_args" ]; then
    echo "Hittar inte ${BUILD}/flash_args – bygg först:  idf.py build" >&2
    exit 1
fi

# Gör esptool tillgängligt även utan exporterad IDF-miljö.
if [ -f /data/esp/esp-idf/export.sh ]; then
    # shellcheck disable=SC1091
    . /data/esp/esp-idf/export.sh >/dev/null 2>&1 || true
fi

echo "Flasha ${PORT} ..."
python -m esptool --chip esp32c6 -p "socket://${PORT}" -b 460800 \
    write_flash "@${BUILD}/flash_args"

echo "Klart."
