#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Flasha MQTT<->UART-bron via lab-VM:n "flasher".
# Stödjer ESP32-C3 och ESP32 (KinCony KC868-A6).
#
# Användning: ./flash-remote.sh [user@host] [seriell-port] [chip]
#   chip:  esp32c3 (default) eller esp32
# Exempel:
#   ./flash-remote.sh                                  # C3, /dev/ttyACM1
#   ./flash-remote.sh opencode@flasher.local /dev/ttyUSB0 esp32
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BUILD="${HERE}/build"

TARGET="${1:-opencode@flasher.local}"
PORT="${2:-/dev/ttyACM1}"
CHIP="${3:-esp32c3}"
KEY="${NIMLY_SSH_KEY:-/data/.ssh/id_ed25519_lab}"
REMOTE_PY="${NIMLY_REMOTE_PY:-~/.venvs/esptool/bin/python}"
REMOTE_DIR="nimly-uart-fw"
APP_BIN="esp32-uart-mqtt-bridge.bin"

SSH_OPTS=(-i "$KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new
          -o UserKnownHostsFile=/data/.ssh/known_hosts_lab)

if [ ! -f "${BUILD}/flash_args" ]; then
    echo "Hittar inte ${BUILD}/flash_args – bygg först: idf.py set-target ${CHIP} && idf.py build" >&2
    exit 1
fi

echo "Kopierar binärer till ${TARGET}:~/${REMOTE_DIR} (chip=${CHIP}) ..."
ssh "${SSH_OPTS[@]}" "$TARGET" "mkdir -p ~/${REMOTE_DIR}"
scp "${SSH_OPTS[@]}" -r \
    "${BUILD}/bootloader" "${BUILD}/partition_table" \
    "${BUILD}/${APP_BIN}" "${BUILD}/ota_data_initial.bin" "${BUILD}/flash_args" \
    "${TARGET}:~/${REMOTE_DIR}/"

echo "Flashar ${PORT} (${CHIP}) på ${TARGET} ..."
ssh "${SSH_OPTS[@]}" "$TARGET" \
    "cd ~/${REMOTE_DIR} && ${REMOTE_PY} -m esptool --chip ${CHIP} -p ${PORT} -b 460800 write_flash @flash_args"

echo "Klart."
