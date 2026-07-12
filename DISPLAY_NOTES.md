# Pi Zero W + 3.5" SPI TFT (FPGA-bridged) — Working Notes

Investigation log for getting a 3.5" 480×320 SPI touch display working on
`zom@pocket.local`.

Last updated: 2026-07-12 (evening session — empirical SPI/pin probing + protocol identification)

## GOALS
1. Get the 3.5" SPI display drawing pixels. **[Best-candidate recipe applied — awaiting one glance at the glass]**
2. Get touch working. **[DONE — verified.]**
3. End goal: **run i3 (X11 window manager)** on the display.

---

## Hardware — CONFIRMED FACTS

### The Pi
- **Board:** Raspberry Pi Zero W Rev 1.1 (BCM2835, single-core, **armv6l**)
- **OS:** Raspbian 13 (trixie), kernel **6.18.34+rpt-rpi-v6** (fbtft + fb_ili9486 modules ARE present)
- **Host:** `zom@pocket.local` (mDNS drops sometimes; was .170, DHCP may move it — scan `nmap -p22 192.168.1.0/24` and verify hostkey, careful: another box lives at .153)
- SSH key auth + passwordless sudo working.

### The display board (physically inspected; user cautions silkscreen may be a copy-paste)
- Silkscreen: **"3.5 inch Display-G"**, "480 x 320 Pixel", "SPI 180MHz Support", "XPT2046".
- 2×13 (26-pin) header; has a **cooling fan**.
- ICs: **Gowin GW1NZ-LV1** FPGA (SPI→panel bridge), **HR2046** (XPT2046 clone), 3.3V + 1.2V regulators.

---

## EMPIRICAL RESULTS — 2026-07-12 session (tools/probe2.py, probe3.py)

### 1. Display channel is WRITE-ONLY (measured, decisive)
MISO pull test: with internal pull-up all CE0 reads = 0xFF, with pull-down all = 0x00,
while touch reads (CE1) return real data under both pulls.
→ **Nothing ever drives MISO for the display. All register-read-based ID probing is
impossible; historical "all-zero register reads" were measuring a pull resistor.**

### 2. Pin connectivity scan (input + 50k internal pull, both directions)
| GPIO | result |
|------|--------|
| 17 (PENIRQ) | externally pulled HIGH (real, touch works) |
| **24** | **externally pulled HIGH — the ONLY loaded control pin besides 17** |
| 4, 14, 15, 18, 22, 23, 25, 27 | floating (no external pull; could still be hi-Z FPGA inputs) |
| 2, 3 | high (Pi's own hard I²C pull-ups, uninformative) |

→ GPIO24 pull-up matches **lcdwiki/MHS-family DC line** (their overlays: dc=24, rst=25).
→ Waveshare-G's claimed pins (dc=22, rst=27, bl=18) show NO electrical presence
  (weak evidence against, not proof — FPGA inputs don't load pins).
→ GPIO18 is NOT an active-high backlight (it idled LOW while screen stayed lit).

### 3. Oracle attempts (all NEGATIVE = uninformative, not failure)
- **Touch-ADC coupling** (black vs white fill → touch noise stats): no signal; plates rail-pinned.
- **TE scan** (init each candidate with TE-on 0x35, sample all header GPIOs ~110k/s
  via /dev/gpiomem): no pulses on any pin for any candidate. (Reference boards don't
  route TE to the header, so silence was expected even on success.)
- **GPIO24-backlight test** (drive 24 low, watch XPT2046 TEMP channel for rail sag): no shift
  → 24 does not gate the backlight (or BL is on the stiff 5V rail).

### 4. Protocol candidates fired blind (probe3.py — all silent, screen state unobserved)
byte-DBI ST7796 (G recipe) mode0/mode3/CS-active-high; 16-bit-word DBI ILI9486+ST7796
on both pin theories; KeDei v5 framing on CE0 and CE1; KeDei v6.3 framing (mode 3).

---

## BOARD IDENTITY — best-supported theory

**lcdwiki "MHS-3.5inch RPi Display-IPS" class**: an IPS 480×320 with **ST7796S behind a
16-bit SPI→parallel bridge** (here implemented in the GW1NZ FPGA), lcdwiki pinout.

Fingerprint evidence (`tools/mhs35ips-overlay.dtb`, from goodtft/LCD-show):
- `regwidth = 16` → **every command/param goes as a 16-bit big-endian word** ({0x00, byte});
  byte-framed writes (everything tried before today) get mangled by the bridge.
- `dc-gpios = 24` (the one pulled-up pin!), `reset-gpios = 25` (active low), no BL gpio.
- **Its init sequence is byte-identical to Waveshare's official G blob** (same F0 C3/96
  unlocks, same gamma tables) → explains a "display-G" silkscreen on MHS-wired hardware.
- IPS + high SPI clock marketing (115MHz there, "180MHz" here) both match.

KeDei framing remains the fallback theory (v5: 2-byte units ctrl-bits 0x11/0x1B/0x15/0x1F,
reset via 1-byte SPI writes 0x00/0x01, ILI9481 init; v6.3: 32-bit units {00 11 00 cmd} /
{00 15 00 dat}, mode 3, HX8357-C init, display-on-CE1) — both were fired blind, sources
cached in scratchpad (`kedei_v50_spidev.c`, `mpi3501.cpp/h`).

---

## CURRENT STATE (as of end of session, NO reboot performed)

- **The glass should now show**: 8 vertical color bars (white yellow cyan green magenta
  red blue black) on the top ~2/3, black/white checkerboard below — painted twice via the
  verbatim MHS35-IPS recipe (`tools/drive2.py`, 16-bit words, dc=24 rst=25, 24MHz mode 0).
- **ONE GLANCE DECIDES**: test card visible → identity confirmed, activate the real driver
  (below). Still white → MHS theory dead, go to fallbacks.
- spi0.0 is left bound to **spidev** (panel-mipi-dbi deliberately NOT rebound, keeps bus quiet).
- ads7846 rebound — **touch still works**.
- GPIO24 (DC) and GPIO25 (RST) parked as outputs, high.
- Boot config UNCHANGED (still the old mipi-dbi block). Reboot restores old (non-working)
  panel-mipi-dbi state harmlessly.

## ACTIVATE THE REAL DRIVER (once test card confirmed)
Everything staged on the Pi:
- `/boot/firmware/overlays/mhs35ips.dtbo` (fbtft: fb_ili9486, 16-bit regwidth, dc24/rst25,
  built-in ads7846 node penirq=17)
- `/boot/firmware/config.txt.mhs35ips` (current config with mipi-dbi + standalone ads7846
  lines replaced by `dtoverlay=mhs35ips:rotate=90,speed=32000000,fps=30`)

```sh
sudo cp /boot/firmware/config.txt /boot/firmware/config.txt.bak.$(date +%s)
sudo cp /boot/firmware/config.txt.mhs35ips /boot/firmware/config.txt
sudo reboot   # slow — be patient
# after boot: /dev/fb1 = 480x320; console: sudo con2fbmap 1 1
# or add fbcon=map:1 to /boot/firmware/cmdline.txt for console-on-TFT at boot
```
Touch calibration params differ slightly from the old ads7846 line (overlay hardcodes
x-plate-ohms=60); redo libinput calibration when X is up.

## FALLBACKS if the glass is still white
1. Variants within the MHS family: `mhs35` (real ILI9486 init incl. F1/F2/F8/F9 vendor
   unlocks — tools/drive1.py A1), `mhs35b`, `mis35` (ILI9488-style init), `mhs395`
   (ST7796 with 0x05 COLMOD) — all 16-bit dc24/rst25; overlays fetchable from
   goodtft/LCD-show `usr/`.
2. KeDei deep-dive: try LSB-first bit order, CS-active-high with kedei framings,
   v6.3 on CE1-with-touch-displaced. Sources cached.
3. **Hardware truth**: logic analyzer / scope on the flex or FPGA pins; or obtain the
   seller's SD image (its /boot/config.txt + overlays contain the exact protocol).
4. Consider board simply being defective (FPGA bitstream never configures): white +
   working touch + total silence is also consistent with a dead display path.

## Key resources
- goodtft/LCD-show overlays (fetched, in tools/): mhs35ips, mhs35; also mhs35b/mis35/mhs395 in repo `usr/`.
- fbcp-ili9341 (juj) — KeDei v6.3 = `mpi3501.cpp` (`-DMPI3501=ON`).
- FREEWING-JP/RaspberryPi_KeDei_35_lcd_v50 — KeDei v5 userspace C.
- Waveshare G wiki via Wayback (WebFetch blocked for archive.org; curl works).
- Official Waveshare files (files.waveshare.com): St7796s.zip blob, G Python demo,
  Waveshare-st7796s.zip (.ko for 6.1.21/6.6.51 — wrong kernel, and byte-framing anyway),
  Waveshare35g.dtbo (fbtft st7796s dc22/rst27 96MHz — decoded, byte-framing family).

## Pin mapping (updated best knowledge)
| Signal | BCM GPIO | Evidence |
|--------|----------|----------|
| SPI0 SCLK/MOSI/MISO | 11/10/9 | bus proven via touch |
| Display CS | 8 (CE0) | assumed; write-only, never ACKs |
| Touch CS | 7 (CE1) | proven working |
| **DC / RS** | **24** | external pull-up + all MHS-family overlays |
| **RESET** | **25** (active low) | MHS-family overlays; electrically unconfirmed |
| Touch PENIRQ | 17 | proven working |
| Backlight | none/hardwired | GPIO18 ruled out; GPIO24-gate ruled out |

## Touch — WORKING ✅ (unchanged)
- `dtoverlay=ads7846,speed=2000000,penirq=17,...` → /dev/input/event0, verified via touchmon.py.
- (If mhs35ips config activated, its built-in ads7846 node replaces this line.)

## Tools (this repo, `tools/`)
- `probe2.py` — spidev rebinding + MISO drive test + pin scan + protocol-correct register reads
- `probe3.py` — TE-scan oracle (gpiomem high-rate sampler), GPIO24 BL test, 9-candidate blind sweep
- `drive1.py` — 5-candidate sweep with touch-ADC oracle + distinct patterns per candidate
- `drive2.py` — final verbatim MHS35-IPS recipe + color-bar/checkerboard test card
- `mhs35ips-overlay.dtb`, `mhs35-overlay.dtb` — fetched lcdwiki overlays (also staged on Pi)

## STRATEGIC CAVEAT (for the i3 goal) — unchanged
fbtft fbdev only (no DRM/KMS); X on fb1 via fbdev driver; single-core armv6 will be
sluggish but i3's static tiling is a decent fit. The panel-mipi-dbi DRM path is dead for
this board (byte framing).
