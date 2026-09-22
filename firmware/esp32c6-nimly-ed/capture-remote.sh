#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Fånga seriell logg från ESP32-C6 via lab-VM:n "flasher" och spara lokalt.
#
# Användning:  ./capture-remote.sh [user@host] [port] [sekunder] [utfil]
# Exempel:     ./capture-remote.sh                       # 60 s, ttyACM0
#              ./capture-remote.sh opencode@flasher.local /dev/ttyACM0 120 docs/logs/ha-pairing.log
#
# Loggar via pyserial på flasher (samma venv som esptool). Skriver till stdout
# och till utfilen. OBS: porten får inte vara upptagen av ett annat program.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET="${1:-opencode@flasher.local}"
PORT="${2:-/dev/ttyACM0}"
SECS="${3:-60}"
OUT="${4:-}"

if [ -z "$OUT" ]; then
    mkdir -p "${HERE}/../../docs/logs"
    OUT="${HERE}/../../docs/logs/serial-$(date +%Y%m%d-%H%M%S).log"
fi
mkdir -p "$(dirname "$OUT")"

KEY="${NIMLY_SSH_KEY:-/data/.ssh/id_ed25519_lab}"
PY="${NIMLY_REMOTE_PY:-~/.venvs/esptool/bin/python}"
SSH_OPTS=(-i "$KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new
          -o UserKnownHostsFile=/data/.ssh/known_hosts_lab)

echo "Fångar ${SECS}s från ${PORT} på ${TARGET} -> ${OUT}"
echo "Avbryt med Ctrl-C."

ssh "${SSH_OPTS[@]}" "$TARGET" "${PY} - ${PORT} ${SECS}" <<'PY' | tee "$OUT"
import sys, time, serial

port = sys.argv[1]
secs = float(sys.argv[2])

s = serial.Serial(port, 115200, timeout=1)
end = time.time() + secs
sys.stderr.write("capture: %s @115200 i %.0fs\n" % (port, secs))
while time.time() < end:
    data = s.read(4096)
    if data:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
s.close()
PY
echo
echo "Sparat: ${OUT}"
