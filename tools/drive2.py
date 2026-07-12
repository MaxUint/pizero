#!/usr/bin/env python3
"""Final candidate: verbatim MHS35-IPS recipe (lcdwiki high-speed IPS 3.5",
ST7796S init behind 16-bit SPI->parallel bridge, DC=GPIO24, RST=GPIO25).

Paints a test card: 8 color bars (top 200 rows) + 40px checkerboard (bottom).
Runs init+draw twice for warm-up robustness. Leaves DC/RST pins parked safe.
Run as root.
"""
import os, subprocess, time

import spidev

W, H = 480, 320            # MADCTL 0x28 = landscape
DC, RST = 24, 25
SPEED = 24_000_000

def log(s):
    print(s, flush=True)

def pc_set(*args):
    subprocess.run(["pinctrl", "set"] + [str(a) for a in args], capture_output=True)

def unbind(dev, drv):
    p = f"/sys/bus/spi/drivers/{drv}/{dev}"
    if os.path.exists(p):
        open(f"/sys/bus/spi/drivers/{drv}/unbind", "w").write(dev)

def override(dev, drv):
    open(f"/sys/bus/spi/devices/{dev}/driver_override", "w").write(drv or "\n")

def bindto(dev, drv):
    try: open(f"/sys/bus/spi/drivers/{drv}/bind", "w").write(dev)
    except OSError as e: log(f"  !! bind {dev} {drv}: {e}")

# --- 16-bit-word DBI ---

s = None

def wr(buf):
    try: s.writebytes2(buf)
    except AttributeError:
        for i in range(0, len(buf), 4096):
            s.writebytes(list(buf[i:i+4096]))

def cmd(c, params=(), delay=0):
    pc_set(DC, "op", "dl")
    wr(bytes([0, c]))
    if params:
        pc_set(DC, "dh")
        wr(bytes(b for p in params for b in (0, p)))
    if delay: time.sleep(delay / 1000)

MHS35IPS_INIT = [
    (0x11, [], 255),
    (0x36, [0x28], 0), (0x3A, [0x55], 0),
    (0xF0, [0xC3], 0), (0xF0, [0x96], 0),
    (0xB4, [0x01], 0), (0xB7, [0xC6], 0),
    (0xC0, [0x80, 0x45], 0), (0xC1, [0x13], 0), (0xC2, [0xA7], 0), (0xC5, [0x0A], 0),
    (0xE8, [0x40, 0x8A, 0x00, 0x00, 0x29, 0x19, 0xA5, 0x33], 0),
    (0xE0, [0xD0,0x08,0x0F,0x06,0x06,0x33,0x30,0x33,0x47,0x17,0x13,0x13,0x2B,0x31], 0),
    (0xE1, [0xD0,0x0A,0x11,0x0B,0x09,0x07,0x2F,0x33,0x47,0x38,0x15,0x16,0x2C,0x32], 0),
    (0xF0, [0x3C], 0), (0xF0, [0x69], 255),
    (0x21, [], 0), (0x29, [], 50),
]

def reset():
    pc_set(RST, "op", "dh"); time.sleep(0.02)
    pc_set(RST, "dl");       time.sleep(0.02)
    pc_set(RST, "dh");       time.sleep(0.15)

def px(c): return bytes([c >> 8, c & 0xFF])
BARS = [0xFFFF, 0xFFE0, 0x07FF, 0x07E0, 0xF81F, 0xF800, 0x001F, 0x0000]

def testcard():
    bar_row = b"".join(px(c) * (W // 8) for c in BARS)
    top = bar_row * 200
    black, white = px(0x0000) * 40, px(0xFFFF) * 40
    rowA = (black + white) * (W // 80)
    rowB = (white + black) * (W // 80)
    rows = []
    for y in range(120):
        rows.append(rowA if (y // 40) % 2 == 0 else rowB)
    return top + b"".join(rows)

def draw(buf):
    cmd(0x2A, [0, 0, (W-1) >> 8, (W-1) & 0xFF])
    cmd(0x2B, [0, 0, (H-1) >> 8, (H-1) & 0xFF])
    cmd(0x2C)
    pc_set(DC, "dh")
    wr(buf)
    cmd(0x00)

if __name__ == "__main__":
    log("=== drive2.py: MHS35-IPS verbatim recipe ===")
    subprocess.run(["modprobe", "spidev"], capture_output=True)
    unbind("spi0.0", "panel-mipi-dbi-spi")
    unbind("spi0.1", "ads7846")
    for d in ("spi0.0", "spi0.1"):
        override(d, "spidev")
        if not os.path.exists(f"/sys/bus/spi/drivers/spidev/{d}"):
            bindto(d, "spidev")
    time.sleep(0.3)

    s = spidev.SpiDev(); s.open(0, 0)
    s.max_speed_hz = SPEED; s.mode = 0
    card = testcard()
    log(f"test card: {len(card)} bytes")
    for run in (1, 2):
        t0 = time.time()
        reset()
        for c, p, d in MHS35IPS_INIT:
            cmd(c, p, d)
        draw(card)
        log(f"  pass {run}: init+draw in {time.time()-t0:.1f}s")
        time.sleep(1.0)
    s.close()

    # park pins: DC high (data-idle), RST high (deasserted) -- both held as outputs
    pc_set(DC, "op", "dh")
    pc_set(RST, "op", "dh")

    unbind("spi0.1", "spidev")
    override("spi0.1", "")
    pc_set(17, "ip", "pu")
    bindto("spi0.1", "ads7846")
    log("ads7846 restored; spi0.0 on spidev; DC=24/RST=25 parked high.")
    log("EXPECTED IF RECIPE CORRECT: 8 vertical color bars (white yellow cyan")
    log("green magenta red blue black) on top 2/3, b/w checkerboard below.")
    log("=== drive2.py done ===")
