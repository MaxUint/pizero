# Pi Zero W — 3.5″ "Display-G" SPI LCD: working driver & setup

Working, verified driver setup for a 3.5″ 480×320 SPI touch display HAT on a
Raspberry Pi Zero W. The knowledge here took several sessions of reverse
engineering to obtain — the board's silkscreen is misleading and no stock
overlay drives it. **The two files that matter are `waveshare35a.dtbo`
(the driver payload) and this README.**

Verified working 2026-07-13 on Raspberry Pi OS trixie (kernel 6.18.34+rpt-rpi-v6,
armv6l) and on the vendor Bullseye image (5.15.32+).

> **Found this by searching?** If your board's silkscreen says
> **"3.5 inch Display-G / 480x320 / SPI 180MHz / XPT2046"** — a 26-pin
> top-mount HAT with a fan and a Gowin GW1NZ-LV1 FPGA — and you're getting a
> **white screen** that no LCD-show script, goodtft/tft35a overlay, KeDei
> protocol, MHS35 init, or generic ILI9486/ST7796 recipe will light up:
> this repo is your answer. Use `waveshare35a.dtbo` from here and read on.

## The hardware (identified)

- 26-pin **top-mount** HAT with fan. Silkscreen: *"3.5 inch Display-G /
  480x320 / SPI 180MHz / XPT2046"* — **the silkscreen is marketing
  copy-paste; it is NOT a Display-G** (the real G mounts under the Pi).
- Behaves exactly as a **Waveshare "3.5inch RPi LCD (A)"** (or clone).
- ICs: Gowin **GW1NZ-LV1 FPGA** implementing an ILI9486-compatible
  controller/bridge, **HR2046** resistive touch (XPT2046 clone), 3.3 V +
  1.2 V regulators. Backlight hardwired on.
- Display channel is **write-only** (MISO never driven); touch shares the
  bus on CE1 and does respond.

## The driver

Stock in-kernel **fbtft `fb_ili9486`** (staging) — no out-of-tree code. All
of the board-specific magic lives in the device-tree overlay
(`waveshare35a.dtbo`, decompiled copy: `waveshare35a.dts`, md5 of dtbo:
`d46683bf262ffa1b532851590a96907c`, extracted from Waveshare's vendor image):

| parameter | value |
|---|---|
| bus | spi0.0 @ 16 MHz |
| framing | `regwidth=16` (every command/param sent as a 16-bit word — the FPGA requires this), `buswidth=8` |
| pins | DC = GPIO24, RESET = GPIO25 (active low), pinctrl claims 17/24/25 |
| panel | 480×320, `rotate=90`, `bgr=0`, `fps=30`, `txbuflen=32768` |
| touch | ads7846 on spi0.1 @ 50 kHz, PENIRQ = GPIO17, x-plate-ohms=20, `swapxy` override available |

Init sequence carried in the overlay (the part no generic ILI9486 recipe
reproduces — note `B0`, the `E2` digital-gamma write, and the doubled
`36`/`11`):

```
B0 00                  Interface Mode Control = 0
11                     Sleep Out            (then delay 255 ms)
3A 55                  COLMOD: 16 bpp
36 28                  MADCTL: MV|BGR (landscape)
C2 44                  Power Control 3
C5 00 00 00 00         VCOM Control
E0 0F 1F 1C 0C 0F 08 48 98 37 0A 13 04 11 0D 00   positive gamma
E1 0F 32 2E 0B 0D 05 47 75 37 06 10 03 24 20 00   negative gamma
E2 0F 32 2E 0B 0D 05 47 75 37 06 10 03 24 20 00   digital gamma (= E1)
36 28                  MADCTL again
11                     Sleep Out again
29                     Display On
```

## Install (any Raspberry Pi OS)

```sh
sudo cp waveshare35a.dtbo /boot/firmware/overlays/
# /boot/firmware/config.txt — ensure/add:
#   dtparam=spi=on
#   dtoverlay=waveshare35a
# /boot/firmware/cmdline.txt — append to the single line:
#   fbcon=map:1
sudo reboot
```

After reboot: login console on the glass. Expected dmesg signature:

```
fb_ili9486 spi0.0: fbtft_property_value: regwidth = 16 ... rotate = 90 ...
graphics fb1: fb_ili9486 frame buffer, 480x320, 300 KiB video memory, 32 KiB buffer memory, fps=33, spi0.0 at 16 MHz
ads7846 spi0.1: touchscreen, irq 160
```

### Why `fbcon=map:1` (KMS gotcha)

With `dtoverlay=vc4-kms-v3d` present (default), vc4 claims fb0 early so the
LCD registers as **fb1**; running headless, vc4 later unregisters fb0 and the
console falls back to a dummy device → panel inits (black screen) but shows
nothing. `fbcon=map:1` makes fbcon adopt fb1 the moment it registers.

### Test / debug without rebooting

```sh
sudo dtoverlay /path/to/waveshare35a.dtbo   # hot-load; fbN + touch appear
sudo sh -c 'cat /dev/urandom > /dev/fb1'    # static on glass = pixel path OK
# point consoles at fb1 live (con2fbmap hardcodes /dev/fb0):
sudo ln -sf /dev/fb1 /dev/fb0
for c in 1 2 3 4 5 6; do sudo con2fbmap $c 1; done
sudo rm /dev/fb0
echo 1 | sudo tee /sys/class/vtconsole/vtcon1/bind   # fbcon takes over
```

Symptom guide: **white screen** = panel never initialized (overlay not
applied / wrong init); **black screen** = driver inited and blanked it, the
console just isn't mapped (see above). `bgr` warning in dmesg on 6.x
("boolean property with a value") is harmless; if red/blue ever look
swapped, `bgr` is the knob.

## X11 / desktop notes

- X on the panel: `xserver-xorg-video-fbdev` with `Option "fbdev" "/dev/fb1"`.
- Touch calibration (values from the vendor image, drop in
  `/etc/X11/xorg.conf.d/`): see `xorg-99-calibration.conf`
  (`Calibration "3932 300 294 3801"`, `SwapAxes 1`).
- The vendor image instead mirrored fb0→fb1 with `fbcp` and ran X/fbturbo on
  fb0 (`hdmi_cvt 480 320 60` forced fb0 to panel size). **fbcp needs legacy
  dispmanx and does not work under KMS** — don't chase it on modern kernels;
  drive fb1 directly.

## Provenance / recovery

- Overlay extracted from the official Waveshare **"3.5inch RPi LCD (A)"**
  pre-installed image `3.5inch RPi LCD (A)_20220404_32bit_bullusye.img`
  (Bullseye 32-bit, 2022-04-04), Google Drive link on
  <https://www.waveshare.com/wiki/3.5inch_RPi_LCD_(A)>. A copy of the image
  zip is archived as a **GitHub Release asset** on this repo in case that
  link ever dies.
- `waveshare35a.dts` is the dtc decompile of the dtbo (compile back with
  `dtc -I dts -O dtb -o waveshare35a.dtbo waveshare35a.dts` if ever needed).
- Full investigation history (probing, eliminated protocols, KeDei
  archaeology) lives in this repo's git history — see `DISPLAY_NOTES.md`
  in commits up to `9c220ab`.
