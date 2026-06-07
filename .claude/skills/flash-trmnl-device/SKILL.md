---
name: flash-trmnl-device
description: Use when the user wants to flash, deploy, upload, or re-flash firmware onto a physically connected TRMNL device (e.g. "deploy to the device", "flash the board", "upload firmware"). Identifies the device, picks the right PlatformIO env/chip, flashes it, and logs the run — so you don't re-ask which chip/env each time.
---

# Flash firmware onto a connected TRMNL device

Goal: deploy firmware to the connected board with **no questions** when the device is
already known, and minimal questions when it isn't. PlatformIO does the build + flash
in one step (right chip, right offsets) — never hand a raw `.bin` to esptool.

## Procedure

1. **Identify the connected chip and port** (don't assume):
   ```bash
   ls /dev/cu.usbmodem*
   export PATH="$HOME/.platformio/penv/bin:$PATH"
   pio pkg exec -p tool-esptoolpy esptool.py -- --port <PORT> chip_id   # prints "Chip is ESP32-S3/..."
   ```
   If no `usbmodem` port appears, the device is likely **deep-sleeping** (normal TRMNL
   behavior between refreshes) or unplugged. Ask the user to wake it / hold BOOT into
   download mode and re-check. Do not guess a port.

2. **Match the chip to a device + env.** Check `docs/runlogs/` first — if a run log
   already identifies this board (by MAC or make), use its env and skip the questions.
   Otherwise use the known-device table in `docs/runlogs/README.md`. Quick map:

   | Detected chip | Likely device | PlatformIO env |
   |---------------|---------------|----------------|
   | ESP32-S3 + XIAO | Seeed XIAO S3 DIY kit | `TRMNL_7inch5_OG_DIY_Kit` (7.5″ OG mono), `_3CLR` (3-color), `TRMNL_4inch26_DIY_Kit` (4.26″), or `seeed_xiao_esp32s3` (bare) |
   | ESP32-S3 (n16r8) | TRMNL_X / X-class | `TRMNL_X` (+ variants) |
   | ESP32-C3 | TRMNL OG | `trmnl` |
   | ESP32-C5 | TRMNL gen2 | `trmnl_gen2` |

   Only ask the user **which display** if the chip alone doesn't pin the env (e.g. an
   S3 XIAO could be 7.5″/4.26″/3-color). Don't ask anything already answered by a run log.

3. **Flash.** For the common XIAO-S3 case there's a ready script that auto-detects the
   port, verifies the chip is S3, and uploads:
   ```bash
   scripts/flash_xiao_s3.sh                         # default env TRMNL_7inch5_OG_DIY_Kit
   scripts/flash_xiao_s3.sh <env> [port]            # override env/port for sibling variants
   ```
   For any other device, run PlatformIO directly (the foolproof general form):
   ```bash
   export PATH="$HOME/.platformio/penv/bin:$PATH"   # post-build scripts call bare `pio`
   pio run -e <ENV> -t upload --upload-port <PORT>
   ```

4. **Confirm + log.** A successful flash verifies bytes-on-flash only — tell the user to
   glance at the panel to confirm it boots and renders. Then add/update a run log under
   `docs/runlogs/` (`YYYY-MM-DD-<device>-flash.md`) with device identity (incl. MAC),
   port, env, chip, the exact command, any gotcha, and the outcome. See
   `docs/runlogs/README.md` for the convention.

## Known gotchas (so you don't rediscover them)

- **`pio` must be on PATH.** `scripts/extra/post_build_seeed.py` (and siblings) shell
  out to bare `pio`; if it's missing the whole `pio run` aborts *after* a clean compile.
  Always `export PATH="$HOME/.platformio/penv/bin:$PATH"` first. The script does this.
- **Default env is C3.** `default_envs = trmnl` is an ESP32-C3 board. Flashing it to an
  S3 (or letting the IDE Upload button use it) fails with
  "This chip is ESP32-S3, not ESP32-C3. Wrong --chip argument?". Pick the S3 env.
- **`firmware.bin` vs `merged_firmware.bin`.** `firmware.bin` is app-only (offset
  `0x10000`); `scripts/flash_merged.sh` writes at `0x0` and needs `merged_firmware.bin`.
  Prefer `pio run -t upload` and avoid juggling offsets entirely.
- **Port disappears after boot.** TRMNL firmware deep-sleeps between refreshes; the
  USB-Serial/JTAG port vanishes while asleep. Wake / BOOT-into-download to reflash.
