#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Flasha Nimly-bryggan (ESP32-C3) via lab-VM:n "flasher" (eller annan SSH-värd).
#
# Användning:   ./flash-remote.sh [user@host] [seriell-port]
# Exempel:      ./flash-remote.sh
#               ./flash-remote.sh opencode@flasher.local /dev/ttyACM2
#
# Förutsätter:
#  - firmware byggd (build/flash_args finns)
#  - SSH-nyckel med åtkomst till värden (default /data/.ssh/id_ed25519_lab)
#  - esptool på fjärrvärden (default ~/.venvs/esptool/bin/python)
#  - användaren på fjärrvärden har åtkomst till den seriella porten (Arch: gruppen uucp)

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BUILD="${HERE}/build"

TARGET="${1:-opencode@flasher.local}"
PORT="${2:-/dev/ttyACM2}"
KEY="${NIMLY_SSH_KEY:-/data/.ssh/id_ed25519_lab}"
REMOTE_PY="${NIMLY_REMOTE_PY:-~/.venvs/esptool/bin/python}"
REMOTE_DIR="nimly-fw-c3"

SSH_OPTS=(-i "$KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new
          -o UserKnownHostsFile=/data/.ssh/known_hosts_lab)

if [ ! -f "${BUILD}/flash_args" ]; then
    echo "Hittar inte ${BUILD}/flash_args – bygg först:  idf.py build" >&2
    exit 1
fi

echo "Kopierar binärer till ${TARGET}:~/${REMOTE_DIR} ..."
ssh "${SSH_OPTS[@]}" "$TARGET" "mkdir -p ~/${REMOTE_DIR}"
scp "${SSH_OPTS[@]}" -r \
    "${BUILD}/bootloader" "${BUILD}/partition_table" \
    "${BUILD}/esp32-uart-mqtt-bridge.bin" "${BUILD}/ota_data_initial.bin" \
    "${BUILD}/flash_args" "${TARGET}:~/${REMOTE_DIR}/"

echo "Flashar ${PORT} på ${TARGET} ..."
ssh "${SSH_OPTS[@]}" "$TARGET" \
    "cd ~/${REMOTE_DIR} && ${REMOTE_PY} -m esptool --chip esp32c3 -p ${PORT} -b 460800 write_flash @flash_args"

echo "Klart."
