# Pi Zero W + 3.5" SPI TFT — Working Notes

Investigation log for getting a 3.5" 480×320 SPI touch display working on
a Raspberry Pi Zero W (host `pocket`, normally `zom@pocket.local`).

Last updated: 2026-07-13 — **MILESTONE 3: driver PORTED — trixie card drives
the panel natively (login terminal on glass, confirmed by eye)**

---

## ★★★ PORTED TO TRIXIE (session 3, final) — the pocket card works

Done entirely over ssh (`zom@pocket.local`) with the user confirming on glass.
Runtime `dtoverlay /tmp/waveshare35a.dtbo` probed instantly on 6.18.34 —
identical dmesg signature to the vendor image (fb_ili9486, 480x320, 16 MHz),
console appeared on the LCD. Then made persistent:

- `/boot/firmware/overlays/waveshare35a.dtbo` ← the vendor dtbo
  (md5 d46683bf262ffa1b532851590a96907c, also in git at
  `8f29aa4:tools/waveshare35a-vendor.dtbo`)
- `/boot/firmware/config.txt`: `dtoverlay=waveshare35a` under `[all]`
  (backup: config.txt.bak.20260713-0950)
- `/boot/firmware/cmdline.txt`: appended `fbcon=map:1`
  (backup: cmdline.txt.bak.*)

**Gotchas found while porting (trixie/KMS specifics):**
- With `vc4-kms-v3d` present, vc4 claims fb0 early, so the LCD registers as
  **fb1**; headless, vc4 later unregisters fb0 and fbcon falls back to the
  dummy console → black (initialized but consoleless) screen. `fbcon=map:1`
  fixes it at boot (fbcon grabs fb1 when it registers).
- Live remap without reboot: `con2fbmap N 1` hardcodes /dev/fb0 and fails
  when fb0 is gone — temporary `ln -sf /dev/fb1 /dev/fb0` lets the ioctl
  through; then `echo 1 > /sys/class/vtconsole/vtcon1/bind` makes fbcon
  actually take over ("Console: switching to colour frame buffer device").
- Pixel-path smoke test: `cat /dev/urandom > /dev/fb1` → static on glass.
- Touch (ads7846) probes on spi0.1 as an input device out of the box.
- Note `bgr = <0x00>` triggers a 6.18 warning ("boolean property with a
  value") — harmless, colors TBC; if red/blue swap shows up, that's where
  to look.

**Remaining for the end-goal:** X11 + i3 on fb1 (xserver-xorg-video-fbdev,
`Option "fbdev" "/dev/fb1"`), touch calibration (vendor X calib was
`3932 300 294 3801` + SwapAxes=1 — see extraction section), optional
console font tuning (vendor used `fbcon=font:ProFont6x11`).

---

## ★★ DRIVER EXTRACTED (session 3) — full ground truth

The vendor image now boots to a **fully working display**. User enabled
ssh/wifi + set hostname → `root@pocket.local` / `pi@pocket.local` reachable
(keys installed via tools/installKeys.sh). Everything below is read from the
**live running system**, not guessed.

### Kernel driver (the actual "display driver")
- **fbtft `fb_ili9486`** (stock staging module, kernel 5.15.32+) bound to
  **spi0.0 at 16 MHz** → `graphics fb1: 480x320, fps=33` (see
  `tools/vendor-image-dmesg-probe.txt` for the probe log).
- Overlay-provided params: `regwidth=16, buswidth=8, rotate=90, bgr=0,
  fps=30, txbuflen=32768, debug=0`.
- Pins: **dc = GPIO24, reset = GPIO25 active-low** (pinctrl claims 17/25/24)
  — *exactly what we fired blind*. The delta was never the wiring.
- Touch: `ads7846` on **spi0.1 @ 50 kHz** (yes, 0xC350 = 50000 — far slower
  than the 2 MHz in goodtft overlays), PENIRQ GPIO17 active-low,
  x-plate-ohms=20, pressure-max=255, `swapxy` exposed as an override.
- Running overlay md5 `d46683bf262ffa1b532851590a96907c` == our archived
  `tools/waveshare35a-vendor.dtbo` — same file, now **decompiled** to
  `tools/waveshare35a-vendor.dts` (via dtc on the Pi itself).

### THE MAGIC: vendor init sequence (decoded from the dtbo `init` property)
```
B0 00                    Interface Mode Control = 0
11                       Sleep Out
   delay 255 ms
3A 55                    COLMOD: 16 bpp
36 28                    MADCTL: MV|BGR (landscape)
C2 44                    Power Control 3
C5 00 00 00 00           VCOM Control
E0 0F 1F 1C 0C 0F 08 48 98 37 0A 13 04 11 0D 00   pos. gamma
E1 0F 32 2E 0B 0D 05 47 75 37 06 10 03 24 20 00   neg. gamma
E2 0F 32 2E 0B 0D 05 47 75 37 06 10 03 24 20 00   digital gamma (= E1)
36 28                    MADCTL again
11                       Sleep Out again
29                       Display On
```
With regwidth=16 fbtft emits every command/parameter as a 16-bit word
(0x00XX) — the FPGA expects that framing. Differences vs the failed MHS35-family
blind inits: presence of `B0`, the `E2` digital-gamma write, doubled `36`/`11`,
different power/VCOM values (`C2 44`, zeroed `C5`), and no `C0`/`C1`/`F`-series
extended commands. Any (or all) of these are what the FPGA's command parser
requires before it unlatches.

### Display pipeline on the vendor image (IMPORTANT — it's fbcp!)
- `config.txt`: `dtoverlay=waveshare35a` + `hdmi_force_hotplug` +
  `hdmi_cvt 480 320 60` → **fb0 (HDMI/dispmanx) is also 480×320**.
- cmdline: `fbcon=map:10` initially maps consoles to fb1, **but**
  `/etc/rc.local` (archived: `tools/vendor-image-rc.local`) then runs
  **`fbcp &`** (dispmanx fb0 → fb1 mirror, ~25 fps) and **`con2fbmap 1 0`**
  (tty1 back onto fb0). Net effect: everything renders to fb0 and fbcp
  mirrors it to the panel. The armv6 binary is archived as
  `tools/vendor-fbcp-armv6.bin` (13 kB, /usr/local/bin/fbcp).
- X11: fbturbo driver on **/dev/fb0** (`tools/vendor-image-99-fbturbo.conf`);
  touch calibrated in `tools/vendor-image-99-calibration.conf`
  (`Calibration "3932 300 294 3801"`, `SwapAxes 1`, third-button emulation).

### Consequences for the trixie port (plan 4b)
- The kernel side ports cleanly: drop `waveshare35a-vendor.dtbo` into
  `/boot/firmware/overlays/waveshare35a.dtbo`, add `dtoverlay=waveshare35a`
  + `fbcon=map:10` — fb_ili9486 exists on 6.18 and reads the same properties.
- **fbcp does NOT port**: it needs legacy dispmanx, which is gone under
  KMS/6.18. On trixie either run X/console directly on fb1 (fbdev), or skip
  fbcp entirely — for the i3 goal, X on fb1 via xserver-xorg-video-fbdev is
  the straight path.

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
1. ~~Confirm the vendor image boots to a console ON the LCD~~ **DONE — full
   working display (session 3).**
2. ~~Enable ssh/wifi, get into the live system~~ **DONE — ground truth
   extracted, see "DRIVER EXTRACTED" above.**
3. ~~Decompile the dtbo~~ **DONE — `tools/waveshare35a-vendor.dts`; magic
   delta = init sequence content (see decode above).**
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
kedei_v62.c, overlays: waveshare35a-vendor.dtbo ★ + waveshare35a-vendor.dts ★
(decompiled), mhs35/mhs35ips dtbo, vendor-image-config.txt ★,
vendor-image-cmdline.txt ★, vendor-image-dmesg-probe.txt ★ (live fbtft probe),
vendor-image-rc.local ★ (fbcp + con2fbmap), vendor-fbcp-armv6.bin,
vendor-image-99-fbturbo.conf / 99-calibration.conf (X11 fb0 + touch calib),
card-prep helpers: saveWifi.sh (repo root), addWifi.sh, installWifi.sh,
interfaces2wpa.sh, installKeys.sh, setHostname.sh, lockSecrets.sh
(secrets `interfaces`/`wpa_supplicant.conf` are root-only + gitignored).

## Strategic notes for the i3 end-goal
- fbtft/fbdev path (no DRM). X via fbdev on fb1; i3 static tiling is a good fit
  for armv6. The vendor image already has the plumbing; bullseye 32-bit is
  serviceable. If staying on trixie, port the overlay (plan 4b) — fb_ili9486
  is present on 6.18.
- Touch coexists fine with the display in the working overlay (both defined
  in one dtbo, standard SPI sharing — the "FPGA snoops the bus" fear from the
  KeDei theory no longer applies).
