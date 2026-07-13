# Pi Zero W + 3.5" SPI TFT — Working Notes

Investigation log for getting a 3.5" 480×320 SPI touch display working on
a Raspberry Pi Zero W (host `pocket`, normally `zom@pocket.local`).

Last updated: 2026-07-13 — **MILESTONE: screen responds to vendor LCD (A) image**

---

## ★ MILESTONE (end of session 2)

Booting the **Waveshare "3.5inch RPi LCD (A)" pre-installed vendor image**
(`3.5inch RPi LCD (A)_20220404_32bit_bullusye.img`, Bullseye 32-bit, from the
Google Drive link on https://www.waveshare.com/wiki/3.5inch_RPi_LCD_(A) )
on the Pi Zero W made the screen **visibly clear: a swipe-down wiping the white**
(= fbtft driver loading and blanking the framebuffer). First deterministic,
reproducible screen response of the entire investigation.

**What this proves:**
1. **The display hardware is ALIVE** — the "dead/latched hardware" theory is out.
2. **The board is a Waveshare 3.5" LCD (A)-family device** (or clone). It answers
   to this image's `waveshare35a` overlay. The "Display-G" silkscreen is a lie
   (user insight: the real G mounts UNDER the Pi; this board is a top-mount HAT
   like the A).
3. The user's memory checks out: this is the same kind of "special Bullseye image"
   that worked on their RPi 3. The KeDei hypothesis is dead (KeDei never shipped
   Bullseye; their protocols never matched; the two earlier "blinks" remain
   unexplained — possibly coincidence/marginal effect — and are now moot).
4. The earlier failures of MY waveshare35a-*like* attempts (byte-DBI & 16-bit
   ILI9486 on dc=24/rst=25) mean the working overlay differs in some detail
   (init sequence / regwidth / speed / timing). The exact diff is now extractable.

**Key artifacts saved in this repo (`tools/`):**
- `waveshare35a-vendor.dtbo` — THE working overlay from the image's boot
  partition (2,379 bytes, dated 2022-05-27 — vendor-modified, differs from the
  stock 2022 overlays and from goodtft's 2,616-byte tft35a). md5
  `d46683bf262ffa1b532851590a96907c`. **NOT yet decompiled — first task next
  session** (`dtc -I dtb -O dts` on any Pi, or apt install device-tree-compiler).
- `vendor-image-config.txt` / `vendor-image-cmdline.txt` — the working boot
  config: `dtoverlay=waveshare35a` (ads7846 line commented out — touch is inside
  the overlay), `hdmi_cvt 480 320 60` (for optional fbcp dual-display), and
  cmdline `fbcon=map:10` (console → fb1, i.e. the LCD).
- The full 7.9 GB image itself: `~/ai/pizero/3.5inch RPi LCD (A)_20220404_32bit_bullusye.img`
  (gitignored). Boot partition also extractable via `7z e -so <img> 0.fat`;
  rootfs via `1.img` (ext4, not yet inspected — contains the running driver env).

## CURRENT PHYSICAL STATE (handoff)
- The Pi Zero W is booting the **vendor image SD card** (first boot = very slow;
  screen cleared white but no text seen yet at time of writing).
- The ORIGINAL trixie system ("pocket") is intact on its own SD card, currently
  removed. Its boot config was CLEANED earlier (no display/touch overlays;
  backups in /boot/firmware/config.txt.bak.*; touch disabled until restored).
- The vendor image has NO ssh/wifi configured → currently only observable via
  the screen itself. To make it headless-accessible: mount its boot partition
  and add `ssh` (empty file), `wpa_supplicant.conf` (copy from pocket card),
  and `userconf.txt` (April-2022 images may lack default pi user:
  `pi:$(openssl passwd -6 raspberry)`).

## NEXT SESSION PLAN
1. Confirm the vendor image boots to a console ON the LCD (fbcon=map:10 → login
   prompt should appear on the glass). If yes: hardware + driver fully proven.
2. Enable ssh/wifi on the vendor card (boot partition edits above) → get into
   the LIVE working system: `dmesg` (fbtft probe messages show driver, speed,
   pins!), `lsmod`, `/proc/device-tree` — full ground truth.
3. Decompile `tools/waveshare35a-vendor.dtbo` → diff against everything we
   fired blind (see "eliminated" below) → identify the magic delta.
4. Decide end-state architecture, either:
   a. **Adopt the vendor image** as the OS (Bullseye 32-bit runs i3 fine on a
      Zero W) — least work, or
   b. **Port the overlay** to the trixie "pocket" card (fbtft fb_ili9486 exists
      on its 6.18 kernel; drop the vendor dtbo into /boot/firmware/overlays/,
      add `dtoverlay=waveshare35a` + `fbcon=map:10`) — keeps the current system.
5. Then the original goal: X11 + i3 on fb1 (fbdev), touch calibration (the
   overlay's built-in ads7846 params + libinput calibration).

## Hardware — CONFIRMED FACTS
- Pi Zero W Rev 1.1 (armv6l). Trixie card: kernel 6.18.34+rpt-rpi-v6, fbtft
  modules present. Vendor card: Bullseye 32-bit, kernel ~5.15 (2022-04-04 pi-gen).
- Display board: 26-pin top-mount HAT, fan, silkscreen "3.5 inch Display-G /
  480x320 / SPI 180MHz / XPT2046" (marketing copy-paste; actual family = LCD (A)).
  ICs: Gowin GW1NZ-LV1 FPGA (presumably implementing the ILI9486-compatible
  controller/bridge), HR2046 touch (XPT2046 clone), 3.3V + 1.2V regulators.
- Touch: XPT2046-protocol on CE1, PENIRQ=GPIO17 — verified working (trixie card,
  ads7846 driver). Note the vendor config drives touch with cs=1, swapxy=1,
  xohms=60 (see vendor-image-config.txt commented line for calibration numbers).

## Electrical findings (still valid, tools/probe2.py, probe3.py)
- Display channel WRITE-ONLY: MISO never driven by the display (reads follow
  internal pulls; touch chip on CE1 as positive control returns real data).
- Pin scan: GPIO24 externally pulled HIGH (= DC line per LCD (A) pinout ✓);
  GPIO17 pulled high (PENIRQ ✓); GPIO 4/14/15/18/22/23/25/27 unloaded
  (GPIO25 = reset per (A) pinout — plain FPGA input, no pull, hence "floating").
- Backlight hardwired on; GPIO18 not a backlight (idles low, screen lit).
- No usable electrical success-oracle exists on this board (no TE routed, touch
  ADC insensitive to panel state, rails too stiff) → eyes on glass only.

## What was ELIMINATED by direct observation (sessions 1–2)
- panel-mipi-dbi byte-DBI ST7796S/ILI9486, official G blob/config (dc22/rst27),
  vendor G python demo replication, mode/CS-polarity/LSB variants.
- 16-bit-regwidth fbtft-style (MHS35 / mhs35ips / mhs35b / mis35 / mhs395 inits)
  on dc24/rst25 AND dc22/rst27, incl. LE/LSB/DC-inverted/mode3 variants.
- 9-bit 3-wire DBI; raw "dumb framebuffer" streams (CS-framed + CS-less, both CE).
- KeDei v5.0, v6.2, v6.3 dialects (all unit formats, CS layouts, wake preambles,
  opposite-CS pumping per lzto source) — from warm AND cold boots.
- Blink-era GPIO states / boot-overlay traffic / touch-traffic preambles (primer.py).
- NOTE the paradox for next session: waveshare35a *should* be ~"ILI9486-ish on
  dc24/rst25" which WAS tried — the vendor dtbo's exact content is the answer.

## Session-2 supporting intel (kept for reference)
- KeDei archaeology (mostly moot now, kept in case): kedei.net archived pages;
  recovered `LCD_show_v6_1_2.tar.gz` (90MB, custom 4.4.9 kernels; in scratchpad);
  `rpi_35_buster_v6_3_kernel_4_19.rar` (995MB) archived & downloadable via
  wayback id_ URL; KeDei product models KD035PI0A (32M), KD035PI0B (128M),
  KD035PI0C ("SPI OFFICIAL 16M"); SPI_128M images LOST from archive.
- Protocol sources cached in repo/scratchpad: fbcp-ili9341 mpi3501 (v6.3),
  FREEWING v5.0 C, lzto v6.2 C (tools/kedei_v62.c), goodtft overlay family.
- spidev gotcha: multi-transfer SPI_IOC_MESSAGE capped at 128 transfers
  (4096 bytes of structs) on this kernel; sudo scripts write logs to /root/.

## Tools inventory (`tools/`)
probe2.py (MISO/pin-scan), probe3.py (TE/BL oracles + blind sweep),
drive1/drive2.py (DBI sweeps + test cards), sweep.py (18-candidate live sweep),
blink.py (minimal v6.3 tap test), charact*.py (self-paced state tests),
primer.py (precondition bisect), soak.py (15-min loop + wiggle test),
boottest.py (per-boot adaptive test runner with state in ~/display_tests),
kedei_v62.c, overlays: waveshare35a-vendor.dtbo ★, mhs35/mhs35ips dtbo,
vendor-image-config.txt ★, vendor-image-cmdline.txt ★.

## Strategic notes for the i3 end-goal
- fbtft/fbdev path (no DRM). X via fbdev on fb1; i3 static tiling is a good fit
  for armv6. The vendor image already has the plumbing; bullseye 32-bit is
  serviceable. If staying on trixie, port the overlay (plan 4b) — fb_ili9486
  is present on 6.18.
- Touch coexists fine with the display in the working overlay (both defined
  in one dtbo, standard SPI sharing — the "FPGA snoops the bus" fear from the
  KeDei theory no longer applies).
