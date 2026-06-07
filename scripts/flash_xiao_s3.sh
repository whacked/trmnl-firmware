#!/bin/bash
#
# flash_xiao_s3.sh — foolproof deploy for Seeed XIAO ESP32-S3 TRMNL DIY kits.
#
# Captures the exact, working procedure (see docs/runlogs/) so the next device of the
# same make can be flashed with no edits: build the matching PlatformIO env and upload
# it in one step. PlatformIO picks the chip, offsets, and image pieces — we never hand
# a raw .bin to esptool.
#
# Usage:
#   scripts/flash_xiao_s3.sh                 # auto-detect port, default 7.5" OG env
#   scripts/flash_xiao_s3.sh <env>           # override the PlatformIO environment
#   scripts/flash_xiao_s3.sh <env> <port>    # also pin the serial port
#
# Env vars (alternative to positional args):
#   ENV=...    PlatformIO environment   (default: TRMNL_7inch5_OG_DIY_Kit)
#   PORT=...   serial port              (default: auto-detect /dev/cu.usbmodem*)
#   SKIP_CHIP_CHECK=1   skip the ESP32-S3 verification step
#
# Why this script exists (gotchas it sidesteps):
#   * `pio` must be on PATH — post_build_seeed.py shells out to bare `pio`, so the whole
#     run aborts after a clean compile if it's missing. We add the penv bin dir here.
#   * The default `trmnl` env is an ESP32-C3 board; flashing it to an S3 fails. This
#     script defaults to the correct S3 env.

set -euo pipefail

ENV_NAME="${1:-${ENV:-TRMNL_7inch5_OG_DIY_Kit}}"
PORT_ARG="${2:-${PORT:-}}"
EXPECTED_CHIP="ESP32-S3"

# --- locate pio and put it on PATH (post-build scripts call bare `pio`) -------------
PIO_BIN="$HOME/.platformio/penv/bin"
if [ -x "$PIO_BIN/pio" ]; then
    export PATH="$PIO_BIN:$PATH"
elif ! command -v pio >/dev/null 2>&1; then
    echo "❌ Could not find 'pio'. Install PlatformIO Core or fix PATH." >&2
    echo "   Expected at: $PIO_BIN/pio" >&2
    exit 1
fi

# --- resolve the serial port --------------------------------------------------------
if [ -n "$PORT_ARG" ]; then
    PORT="$PORT_ARG"
else
    # shellcheck disable=SC2207
    PORTS=($(ls /dev/cu.usbmodem* 2>/dev/null || true))
    if [ "${#PORTS[@]}" -eq 0 ]; then
        echo "❌ No /dev/cu.usbmodem* device found. Is the board plugged in?" >&2
        exit 1
    elif [ "${#PORTS[@]}" -gt 1 ]; then
        echo "❌ Multiple usbmodem ports found; pin one explicitly:" >&2
        printf '   %s\n' "${PORTS[@]}" >&2
        echo "   e.g. scripts/flash_xiao_s3.sh $ENV_NAME ${PORTS[0]}" >&2
        exit 1
    fi
    PORT="${PORTS[0]}"
fi

echo "🔌 Port: $PORT"
echo "🧩 Env:  $ENV_NAME"

# --- verify the connected chip is what we expect ------------------------------------
if [ "${SKIP_CHIP_CHECK:-0}" = "0" ]; then
    echo "🔎 Detecting chip..."
    DETECTED="$(pio pkg exec -p tool-esptoolpy esptool.py -- --port "$PORT" chip_id 2>&1 || true)"
    if echo "$DETECTED" | grep -q "$EXPECTED_CHIP"; then
        echo "✅ Confirmed $EXPECTED_CHIP on $PORT"
    else
        echo "⚠️  Did not see '$EXPECTED_CHIP' in chip detection output." >&2
        echo "    This script targets $EXPECTED_CHIP (env: $ENV_NAME)." >&2
        echo "    Detection said:" >&2
        echo "$DETECTED" | grep -iE "chip is|detecting chip|error|failed" >&2 || true
        echo "    Re-run with SKIP_CHIP_CHECK=1 to override, or pass the right env." >&2
        exit 1
    fi
fi

# --- build + upload in one step (PlatformIO handles chip/offsets/merge) --------------
echo ""
echo "⚡ Building + flashing $ENV_NAME → $PORT ..."
pio run -e "$ENV_NAME" -t upload --upload-port "$PORT"

echo ""
echo "✅ Deploy complete. Glance at the panel to confirm it boots and renders."
echo "   (Flash verifies bytes-on-flash only, not display output.)"
