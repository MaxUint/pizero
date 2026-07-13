# Pi Zero W + 3.5" SPI TFT (FPGA-bridged) — Working Notes

Investigation log for getting a 3.5" 480×320 SPI touch display working on
`zom@pocket.local`.

Last updated: 2026-07-13 (KeDei v6.3 blink evidence + suspected latched bad state)

## GOALS
1. Get the 3.5" SPI display drawing pixels. **[Blocked on suspected wedged FPGA/panel
   state — power-cycle experiment pending, see PLAN]**
2. Get touch working. **[Was verified working; ads7846 currently NOT loaded (removed
   from boot config during debugging).]**
3. End goal: **run i3 (X11)** on the display.

---

## Hardware — CONFIRMED FACTS
- **Pi:** Raspberry Pi Zero W Rev 1.1 (armv6l), Raspbian 13 trixie, kernel 6.18.34+rpt-rpi-v6.
  fbtft/fb_ili9486 modules present. SSH `zom@pocket.local`, passwordless sudo.
  (Another machine sometimes squats old DHCP IPs — verify host key; pocket ≠ 192.168.1.153.)
- **Display board:** silkscreen "3.5 inch Display-G", 480×320, "SPI 180MHz", XPT2046;
  26-pin header (pins 1–26 confirmed by user photo); cooling fan; **Gowin GW1NZ-LV1
  FPGA**, HR2046 touch clone, 3.3V+1.2V regs. Silkscreen may be copy-paste (knockoff).
- Board sits on pins 1–26 only → available GPIOs: 2,3,4,7,8,9,10,11,14,15,17,18,22,23,24,25,27.

## Electrical findings (probe2.py / probe3.py — solid, reproducible)
1. **Display channel is write-only.** MISO is never driven during CE0 activity
   (reads follow internal pulls exactly; touch on CE1 returns real data under both
   pulls → bus + method sound). Register-read identification is impossible.
2. **Pin scan:** GPIO24 = the ONLY externally-loaded control pin (pulled high)
   besides PENIRQ 17. GPIO4/14/15/18/22/23/25/27 all float. GPIO18 idles low while
   backlight is on → not an active-high BL. GPIO24 does not gate the backlight
   (temp-channel rail test negative). GPIO24's role still unknown
   (candidates: unused DC per MHS pinout, FPGA RECONFIG_N/DONE, BUSY).
3. Backlight: hardwired on. Screen shows **pure uniform white** (user-inspected
   closely: no banding/flicker/grey) whenever panel is uninitialized = gate drivers
   never ran.
4. Oracles that DON'T work on this board: TE-line scan (no TE routed), touch-ADC
   coupling (plates rail-pinned), rail-sag via XPT2046 temp channel (too stiff).
   **Only reliable oracle = human eyes on the glass.**

## Protocol findings — the KeDei v6.3 blink evidence
User-observed, keyboard-timestamped (tools/blink.py, correlation table):
```
key @ 63.61s -> during: v63 CE0 m3: magic+init
key @ 67.10s -> during: v63 CE0 m3: fill BLACK
key @ 74.98s -> during: v63 CE1 m3: magic+init
key @ 78.35s -> during: v63 CE1 m3: fill BLACK
(no taps during ANY KeDei v5 step, CE0 or CE1)
```
- Screen "blinked black a few times" — visible changes during **KeDei v6.3 framing**
  (32-bit units {00 11 00 cmd}/{00 15 00 dat}, SPI mode 3, HX8357-C-ish init from
  fbcp-ili9341 `mpi3501.cpp`), on **both CE0 and CE1** → FPGA appears CS-agnostic.
- Same blinks seen in the preceding sweep re-run (KeDei-only steps).
- **NOT reproducible since** (see timeline). Content never persisted to a stable image.

## What has been ELIMINATED on-glass (user watched / confirmed white after)
- Official Waveshare G recipe (byte DBI ST7796S dc22/rst27) — kernel panel-mipi-dbi,
  vendor blob, vendor python equivalent, at multiple speeds/modes; also CS-active-high,
  mode 3, LSB-first byte variants.
- 16-bit-word DBI (MHS35/mhs35ips/mhs35b/mis35/mhs395 style) on dc24/rst25 AND
  dc22/rst27, incl. little-endian, LSB-first, DC-inverted, mode 3 variants.
  The staged mhs35ips theory is DEAD on this board (drive2.py test card → still white).
- 9-bit 3-wire DBI; raw dumb-framebuffer streams (CS-framed and CS-less, CE0/CE1);
  KeDei v5 full sequences (never produced taps).

## Session timeline (2026-07-12 → 13, one Pi boot, no power cycle!)
1. probe2 (electrical) → write-only + pin scan findings.
2. drive1 (5 DBI candidates + touch-ADC oracle) — unobserved, oracle null.
3. probe3 (TE/BL oracles + 9 blind inits incl. KeDei inits WITHOUT fills) — silent.
4. drive2 mhs35ips test card ×2 — later confirmed WHITE (theory dead).
5. sweep run1 (18 steps; KeDei steps crashed EMSGSIZE — spidev caps multi-transfer
   ioctl at 128 transfers/4096B of structs).
6. sweep run2 (KeDei 11–14 fixed): **USER SAW BLINKS.**
7. blink.py: keyboard correlation above — **v6.3 active on both CS.**
8. charact.py (8 v6.3 states: fills, bands, BULK writes, mode-0 reinit, **32MHz bulk**,
   touch-driver rebinds) — USER NOT WATCHING. Bulk timing: full-frame fill 1.0s vs 4.0s
   pumped; 0.8s at 32MHz.
9. Since then: EVERYTHING dead white — charact2 (v6.3 self-paced), charact3
   (bisect incl. exact blink.py order: v5 CE0 full → v63 CE0 → v5 CE1 → v63 CE1,
   wake-bytes variant) — **zero pixels, zero flicker**.

## LEADING HYPOTHESIS — latched bad state (user's suggestion, evidence agrees)
The FPGA (or panel behind it) entered a stuck state partway through the session and
no longer reacts to anything, including previously-working sequences. Prime suspects,
in order of when they first appeared between "blinks" (7) and "dead" (9):
- **32MHz mode-3 bulk stream** (charact.py state 7) — most aggressive signal of the
  whole session; could glitch FPGA input sampling/state machine.
- Bulk writes generally (1024 units per CS frame instead of pumped CS) — if the FPGA
  frames on CS edges, a 4096-byte frame is 1023 units of misalignment...
- Mode-0 re-init among mode-3 traffic (SCLK idle level flips mid-session).
- ads7846 driver rebinds + touch traffic — CS-agnostic FPGA swallows touch bytes as
  garbage units (also a long-term coexistence concern!).
- Panel-side: accidental SLPIN/DISPOFF/deep-standby from garbage — DSTB on many
  controllers exits ONLY via hardware reset/power-cycle, and this board has no
  reachable reset pin (KeDei resets in-band via SPI).
Mechanisms like these are consistent with "correct commands stop working": state
machines held mid-frame, panel bias/charge-pump shut down, deep standby.

## PLAN — clean-state reproduction (NEXT ACTION, needs human at the hardware)
1. Boot config has been CLEANED (2026-07-13): display + ads7846 overlays removed from
   /boot/firmware/config.txt (backups: config.txt.bak.*). After reboot, NOTHING
   touches SPI0 at boot → first bus traffic is whatever we choose.
2. **FULL POWER CYCLE** (not just reboot — unplug Pi power, wait ~15s, replug).
   Display is header-powered, so this hard-resets FPGA + panel.
3. First and only traffic after boot: `sudo python3 ~/blink.py` (verbatim script that
   produced the taps) with eyes on the glass.
   - Blinks return → latched-state confirmed + v6.3 protocol re-confirmed.
     Discipline from then on: ONE experiment per power cycle; bisect what wedges it
     (32MHz? bulk? mode0? touch traffic?); characterize colors/windowing gently.
   - Still dead → the original blinks need re-examination (H: mode-3 SCLK-idle
     transitions, v5+v63 interaction, or observational artifact) — next step would be
     re-running the EXACT sweep run-2 (steps 11–14) after another power cycle.
4. Remember: reboots/power cycles are slow on this Pi and mDNS can drop — find it via
   `nmap -p22 192.168.1.0/24` if pocket.local doesn't resolve.

## Driver end-game (once protocol is stable)
- fbcp-ili9341 `-DMPI3501=ON` implements KeDei v6.3 (needs legacy/dispmanx: remove
  `dtoverlay=vc4-kms-v3d`; unknown if dispmanx userland still works on trixie/armhf).
  NOTE: it owns SPI registers directly → CANNOT coexist with kernel ads7846; touch
  would need polling or custom handling. Also expects display on CE1 (ours: either).
- Alternative: custom userspace fbcp via spidev (bulk writes measured ~1 fps pumped,
  ~4 fps bulk at 8MHz, faster at 32MHz — IF bulk mode is actually safe; it's also a
  wedge suspect).
- Touch coexistence on a CS-agnostic FPGA is an open design problem — touch SPI
  traffic may corrupt display state; needs experiment (charact2 state 5/6 never ran
  due to dead panel).

## Current machine state (end of 2026-07-13 session)
- /boot/firmware/config.txt: CLEAN (no display, no ads7846). Backups + staged
  alternates: config.txt.bak.*, config.txt.mhs35ips (dead theory, kept for reference).
- spi0.0/spi0.1: spidev bound at runtime (until reboot); ads7846 currently unbound →
  **touch inactive**. Restore later by re-adding the ads7846 dtoverlay line.
- Screen: white (uninitialized), backlight on.
- Repo tools/: probe2, probe3, drive1, drive2, sweep, blink, charact, charact2,
  charact3 + fetched overlays. On the Pi: same scripts in ~zom.

## Key resources
- fbcp-ili9341 mpi3501.{cpp,h} = KeDei v6.3 reference (cached in scratchpad + tools).
- FREEWING-JP/RaspberryPi_KeDei_35_lcd_v50 = v5 reference (cached).
- goodtft/LCD-show overlays (mhs35, mhs35ips, mhs35b, mis35, mhs395) — decoded; all
  16-bit dc24/rst25; eliminated on-glass.
- Waveshare G official artifacts (blob, python demo, dtbo, .ko) — eliminated on-glass.
- Product identity still unknown; seller listing/driver link would still be GOLD —
  ask user to dig it up if possible.

## Pin mapping (best current knowledge)
| Signal | BCM GPIO | Evidence |
|--------|----------|----------|
| SPI0 SCLK/MOSI/MISO | 11/10/9 | proven via touch |
| Display data path | CE0 or CE1, CS-agnostic | v6.3 blinks on both |
| Touch CS | 7 (CE1) | proven |
| Touch PENIRQ | 17 | proven |
| GPIO24 | pulled high, role unknown | not DC (MHS recipes dead), not BL-gate |
| DC / RESET GPIOs | none used | KeDei-style in-band control |
| Backlight | hardwired on | GPIO18/24 ruled out |
