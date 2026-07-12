#!/usr/bin/env python3
"""Behavioral protocol sweep for the 3.5" FPGA display board. Run as root.

For each candidate (protocol, framing, pins): reset, init, fill black,
sample touch ADC noise, fill white, sample again, draw a distinctive
signature pattern. The touch overlay couples electrically to the LC glass,
so a black-vs-white statistical shift in touch ADC noise = pixels changing.

Candidates ordered most->least likely; the sweep reruns the best candidate
last so its signature pattern stays on the glass.

  A1 ILI9486 init, 16-bit framing, DC=24 RST=25  -> checkerboard
  A2 ST7796S init, 16-bit framing, DC=24 RST=25  -> solid MAGENTA
  A3 ILI9486 init, 16-bit framing, DC=22 RST=27  -> vertical stripes
  A4 ST7796S init, 16-bit framing, DC=22 RST=27  -> horizontal stripes
  A5 ST7796S init,  8-bit framing, DC=22 RST=27  -> solid GREEN (official G recipe)

Leaves spi0.0 unbound from the panel driver (bus quiet) and restores ads7846.
"""
import os, statistics, subprocess, sys, time

import spidev

W, H = 320, 480
SPEED = 16_000_000

def log(s=""):
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
    open(f"/sys/bus/spi/drivers/{drv}/bind", "w").write(dev)

def to_spidev():
    subprocess.run(["modprobe", "spidev"], capture_output=True)
    unbind("spi0.0", "panel-mipi-dbi-spi")
    unbind("spi0.1", "ads7846")
    for d in ("spi0.0", "spi0.1"):
        override(d, "spidev")
        try: bindto(d, "spidev")
        except OSError: pass
    time.sleep(0.3)

# ---------- display access ----------

class Panel:
    def __init__(self, dc, rst, wide):
        self.dc, self.rst, self.wide = dc, rst, wide
        self.s = spidev.SpiDev(); self.s.open(0, 0)
        self.s.max_speed_hz = SPEED; self.s.mode = 0

    def close(self):
        self.s.close()
        pc_set(self.dc, "ip")          # release DC (ext pull decides)

    def wr(self, buf):
        try: self.s.writebytes2(buf)
        except AttributeError:
            for i in range(0, len(buf), 4096):
                self.s.writebytes(list(buf[i:i+4096]))

    def cmd(self, c, params=(), delay=0):
        pc_set(self.dc, "op", "dl")
        self.wr(bytes([0, c]) if self.wide else bytes([c]))
        if params:
            pc_set(self.dc, "dh")
            if self.wide:
                self.wr(bytes(b for p in params for b in (0, p)))
            else:
                self.wr(bytes(params))
        if delay: time.sleep(delay / 1000)

    def reset(self):
        pc_set(self.rst, "op", "dh"); time.sleep(0.02)
        pc_set(self.rst, "dl");       time.sleep(0.02)
        pc_set(self.rst, "dh");       time.sleep(0.15)

    def init(self, seq):
        self.reset()
        for c, params, delay in seq:
            self.cmd(c, params, delay)

    def window(self, x0, y0, x1, y1):
        self.cmd(0x2A, [x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF])
        self.cmd(0x2B, [y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF])

    def blit(self, buf):
        self.window(0, 0, W - 1, H - 1)
        self.cmd(0x2C)
        pc_set(self.dc, "dh")
        self.wr(buf)
        self.cmd(0x00)                 # NOP terminates RAM write

# ---------- init sequences ----------

GAMMA_P = [0xD0,0x08,0x0F,0x06,0x06,0x33,0x30,0x33,0x47,0x17,0x13,0x13,0x2B,0x31]
GAMMA_N = [0xD0,0x0A,0x11,0x0B,0x09,0x07,0x2F,0x33,0x47,0x38,0x15,0x16,0x2C,0x32]

def st7796_seq(colmod):
    return [
        (0x01, [], 150), (0x36, [0x48], 0), (0x3A, [colmod], 0),
        (0xF0, [0xC3], 0), (0xF0, [0x96], 0), (0xB4, [0x01], 0), (0xB7, [0xC6], 0),
        (0xC0, [0x80, 0x45], 0), (0xC1, [0x13], 0), (0xC2, [0xA7], 0), (0xC5, [0x0A], 0),
        (0xE8, [0x40, 0x8A, 0, 0, 0x29, 0x19, 0xA5, 0x33], 0),
        (0xE0, GAMMA_P, 0), (0xE1, GAMMA_N, 0),
        (0xF0, [0x3C], 0), (0xF0, [0x69], 0),
        (0x21, [], 0), (0x11, [], 120), (0x29, [], 100), (0x13, [], 20),
    ]

ILI9486_MHS = [
    (0x01, [], 150),
    (0xF1, [0x36, 0x04, 0x00, 0x3C, 0x0F, 0x8F], 0),
    (0xF2, [0x18, 0xA3, 0x12, 0x02, 0xB2, 0x12, 0xFF, 0x10, 0x00], 0),
    (0xF8, [0x21, 0x04], 0), (0xF9, [0x00, 0x08], 0),
    (0x36, [0x08], 0), (0xB4, [0x00], 0), (0xC1, [0x41], 0),
    (0xC5, [0x00, 0x91, 0x80, 0x00], 0),
    (0xE0, [0x0F,0x1F,0x1C,0x0C,0x0F,0x08,0x48,0x98,0x37,0x0A,0x13,0x04,0x11,0x0D,0x00], 0),
    (0xE1, [0x0F,0x32,0x2E,0x0B,0x0D,0x05,0x47,0x75,0x37,0x06,0x10,0x03,0x24,0x20,0x00], 0),
    (0x3A, [0x55], 0), (0x11, [], 150), (0x36, [0x08], 0), (0x29, [], 255),
]

# ---------- patterns (RGB565 big-endian) ----------

def px(color): return bytes([color >> 8, color & 0xFF])
BLACK, WHITE = px(0x0000), px(0xFFFF)
MAGENTA, GREEN = px(0xF81F), px(0x07E0)

def solid(c): return c * (W * H)

def checker(sq=64):
    rowA = b"".join((BLACK if (x // sq) % 2 == 0 else WHITE) for x in range(W))
    rowB = b"".join((WHITE if (x // sq) % 2 == 0 else BLACK) for x in range(W))
    return b"".join((rowA if (y // sq) % 2 == 0 else rowB) for y in range(H))

def vstripes(sq=40):
    row = b"".join((BLACK if (x // sq) % 2 == 0 else WHITE) for x in range(W))
    return row * H

def hstripes(sq=40):
    return b"".join((BLACK if (y // sq) % 2 == 0 else WHITE) * W for y in range(H))

# ---------- touch-ADC oracle ----------

def touch_stats(n=150):
    t = spidev.SpiDev(); t.open(0, 1)
    t.max_speed_hz = 2_000_000; t.mode = 0
    out = {}
    for name, c in (("Y", 0x90), ("X", 0xD0), ("Z1", 0xB0), ("Z2", 0xC0)):
        vals = []
        for _ in range(n):
            r = t.xfer2([c, 0, 0])
            vals.append(((r[1] << 8) | r[2]) >> 3)
        out[name] = (statistics.mean(vals), statistics.pstdev(vals), min(vals), max(vals))
    t.close()
    return out

def fmt_stats(st):
    return " ".join(f"{k}:{m:6.1f}±{s:4.1f}[{lo},{hi}]" for k, (m, s, lo, hi) in st.items())

def delta_score(a, b):
    score = 0.0
    for k in a:
        ma, sa = a[k][0], a[k][1]
        mb, sb = b[k][0], b[k][1]
        score += abs(ma - mb) / (max(sa, sb, 0.5)) + abs(sa - sb) / max(min(sa, sb), 0.5)
    return score

# ---------- sweep ----------

ATTEMPTS = [
    ("A1 ILI9486 16bit dc24/rst25 -> CHECKERBOARD", ILI9486_MHS,      True,  24, 25, checker),
    ("A2 ST7796S 16bit dc24/rst25 -> MAGENTA",      st7796_seq(0x55), True,  24, 25, lambda: solid(MAGENTA)),
    ("A3 ILI9486 16bit dc22/rst27 -> V-STRIPES",    ILI9486_MHS,      True,  22, 27, vstripes),
    ("A4 ST7796S 16bit dc22/rst27 -> H-STRIPES",    st7796_seq(0x55), True,  22, 27, hstripes),
    ("A5 ST7796S  8bit dc22/rst27 -> GREEN (official G)", st7796_seq(0x05), False, 22, 27, lambda: solid(GREEN)),
]

def run_attempt(label, seq, wide, dc, rst, pattern, oracle=True):
    log(f"\n--- {label}")
    p = Panel(dc, rst, wide)
    t0 = time.time()
    p.init(seq)
    score = None
    if oracle:
        p.blit(solid(BLACK)); time.sleep(0.15)
        st_b = touch_stats()
        p.blit(solid(WHITE)); time.sleep(0.15)
        st_w = touch_stats()
        score = delta_score(st_b, st_w)
        log(f"  black: {fmt_stats(st_b)}")
        log(f"  white: {fmt_stats(st_w)}")
        log(f"  ORACLE delta score: {score:.2f}")
    p.blit(pattern())
    p.close()
    log(f"  done in {time.time()-t0:.1f}s")
    return score

if __name__ == "__main__":
    log("=== drive1.py: protocol sweep ===")
    to_spidev()
    pc_set(18, "op", "dh")            # possible BL: drive high, harmless if unused

    log("\n--- A0 control (display in reset, oracle should show ~no delta)")
    pc_set(25, "op", "dl"); pc_set(27, "op", "dl")
    st1 = touch_stats(); st2 = touch_stats()
    log(f"  s1: {fmt_stats(st1)}")
    log(f"  s2: {fmt_stats(st2)}")
    ctrl = delta_score(st1, st2)
    log(f"  CONTROL delta score (noise floor): {ctrl:.2f}")
    pc_set(25, "dh"); pc_set(27, "dh")

    scores = []
    for a in ATTEMPTS:
        try:
            s = run_attempt(*a)
            scores.append((s if s is not None else -1, a))
        except Exception as e:
            log(f"  !! attempt failed: {e}")
            scores.append((-1, a))

    best = max(scores, key=lambda x: x[0])
    log(f"\n=== ORACLE SUMMARY (control noise floor {ctrl:.2f}) ===")
    for s, a in scores:
        log(f"  {s:7.2f}  {a[0]}")
    winner = best[1] if best[0] > max(3 * ctrl, 6.0) else ATTEMPTS[0]
    log(f"\nRe-running final candidate to leave its pattern on glass: {winner[0]}")
    run_attempt(*winner, oracle=False)

    # restore touch; leave spi0.0 on spidev so the bus stays quiet
    unbind("spi0.1", "spidev")
    override("spi0.1", "")
    pc_set(17, "ip", "pu")
    try: bindto("spi0.1", "ads7846")
    except OSError as e: log(f"!! ads7846 rebind failed: {e}")
    log("\nads7846 restored; spi0.0 left on spidev (panel driver NOT rebound).")
    log("=== drive1.py done ===")
