#!/usr/bin/env python3
"""Oracle-driven protocol identification for the 3.5" FPGA display. Run as root.

Oracles (no eyes needed):
  1. TE scan  — after each candidate init (with TE-on 0x35 inserted), sample all
     header GPIOs at high rate via /dev/gpiomem. A working panel with a wired
     TE/VSYNC line shows a ~60Hz pulse train.
  2. BL test  — GPIO24 is externally pulled high. If it gates the backlight
     (~100mA load), driving it low sags the 3.3V rail slightly; the XPT2046
     TEMP channel (absolute diode vs VCC-derived ref) shifts measurably.

Candidates (P1..P9) cover: byte-DBI (Waveshare G), 16-bit-word DBI (MHS35),
KeDei v5 2/3-byte framing (CE0 and CE1), KeDei v6.3 32-bit framing, CS
polarity and SPI mode variants.
"""
import os, statistics, struct, subprocess, time

import mmap as mmap_mod
import spidev

GPLEV0 = 0x34
SAMPLE_PINS = [4, 14, 15, 17, 18, 22, 23, 24, 25, 27]

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
    try: open(f"/sys/bus/spi/drivers/{drv}/bind", "w").write(dev)
    except OSError as e: log(f"  !! bind {dev} {drv}: {e}")

def to_spidev():
    subprocess.run(["modprobe", "spidev"], capture_output=True)
    unbind("spi0.0", "panel-mipi-dbi-spi")
    unbind("spi0.1", "ads7846")
    for d in ("spi0.0", "spi0.1"):
        override(d, "spidev")
        if not os.path.exists(f"/sys/bus/spi/drivers/spidev/{d}"):
            bindto(d, "spidev")
    time.sleep(0.3)

# ---------- high-rate GPIO sampler ----------

_gm = None
def gpiomem():
    global _gm
    if _gm is None:
        f = os.open("/dev/gpiomem", os.O_RDWR | os.O_SYNC)
        _gm = mmap_mod.mmap(f, 4096)
    return _gm

def sample_pins(dur=1.2):
    """Return (nsamples, {pin: (transitions, last_level)})."""
    m = gpiomem()
    up = struct.unpack_from
    trans = dict.fromkeys(SAMPLE_PINS, 0)
    prev = up("<I", m, GPLEV0)[0]
    n = 0
    end = time.monotonic() + dur
    while time.monotonic() < end:
        for _ in range(2048):
            cur = up("<I", m, GPLEV0)[0]
            d = cur ^ prev
            if d:
                for p in SAMPLE_PINS:
                    if d & (1 << p):
                        trans[p] += 1
            prev = cur
            n += 1
    return n, {p: (trans[p], (prev >> p) & 1) for p in SAMPLE_PINS}

def report_sample(tag, n, res):
    hot = {p: t for p, (t, _) in res.items() if t > 2}
    lv = " ".join(f"{p}:{'H' if l else 'L'}{'*'+str(t) if t>2 else ''}"
                  for p, (t, l) in sorted(res.items()))
    log(f"  [{tag}] {n} samples | {lv}" + (f"  <<< TOGGLING: {hot}" if hot else ""))
    return hot

def pins_input_pu(exclude=()):
    for p in SAMPLE_PINS:
        if p not in exclude:
            pc_set(p, "ip", "pu")

# ---------- touch ADC ----------

def touch_open():
    t = spidev.SpiDev(); t.open(0, 1)
    t.max_speed_hz = 2_000_000; t.mode = 0
    return t

def adc_mean(t, cmd, n=300):
    vals = []
    for _ in range(n):
        r = t.xfer2([cmd, 0, 0])
        vals.append(((r[1] << 8) | r[2]) >> 3)
    return statistics.mean(vals), statistics.pstdev(vals)

# ---------- DBI panel (byte or 16-bit word framing) ----------

class DBI:
    def __init__(self, dc, rst, wide, mode=0, cs_invert=False, speed=16_000_000):
        self.dc, self.rst, self.wide, self.cs_invert = dc, rst, wide, cs_invert
        self.s = spidev.SpiDev(); self.s.open(0, 0)
        self.s.max_speed_hz = speed; self.s.mode = mode
        if cs_invert:
            self.s.no_cs = True
            pc_set(8, "op", "dl")          # idle LOW for active-high CS

    def close(self):
        if self.cs_invert: pc_set(8, "op", "dh")
        self.s.close()
        pc_set(self.dc, "ip", "pu")
        if self.rst is not None: pc_set(self.rst, "ip", "pu")

    def wr(self, buf):
        if self.cs_invert:
            pc_set(8, "dh")
        try: self.s.writebytes2(buf)
        except AttributeError:
            for i in range(0, len(buf), 4096):
                self.s.writebytes(list(buf[i:i+4096]))
        if self.cs_invert:
            pc_set(8, "dl")

    def cmd(self, c, params=(), delay=0):
        pc_set(self.dc, "op", "dl")
        self.wr(bytes([0, c]) if self.wide else bytes([c]))
        if params:
            pc_set(self.dc, "dh")
            self.wr(bytes(b for p in params for b in (0, p)) if self.wide
                    else bytes(params))
        if delay: time.sleep(delay / 1000)

    def reset(self):
        if self.rst is None: return
        pc_set(self.rst, "op", "dh"); time.sleep(0.02)
        pc_set(self.rst, "dl");       time.sleep(0.02)
        pc_set(self.rst, "dh");       time.sleep(0.15)

    def init(self, seq):
        self.reset()
        for c, params, delay in seq:
            self.cmd(c, params, delay)

    def blit(self, buf, w=320, h=480):
        self.cmd(0x2A, [0, 0, (w-1) >> 8, (w-1) & 0xFF])
        self.cmd(0x2B, [0, 0, (h-1) >> 8, (h-1) & 0xFF])
        self.cmd(0x2C)
        pc_set(self.dc, "dh")
        self.wr(buf)
        self.cmd(0x00)

GAMMA_P = [0xD0,0x08,0x0F,0x06,0x06,0x33,0x30,0x33,0x47,0x17,0x13,0x13,0x2B,0x31]
GAMMA_N = [0xD0,0x0A,0x11,0x0B,0x09,0x07,0x2F,0x33,0x47,0x38,0x15,0x16,0x2C,0x32]
TE_ON = (0x35, [0x00], 5)

def st7796_seq(colmod=0x55):
    return [
        (0x01, [], 150), (0x11, [], 120), TE_ON,
        (0x36, [0x48], 0), (0x3A, [colmod], 0),
        (0xF0, [0xC3], 0), (0xF0, [0x96], 0), (0xB4, [0x01], 0), (0xB7, [0xC6], 0),
        (0xC0, [0x80, 0x45], 0), (0xC1, [0x13], 0), (0xC2, [0xA7], 0), (0xC5, [0x0A], 0),
        (0xE8, [0x40, 0x8A, 0, 0, 0x29, 0x19, 0xA5, 0x33], 0),
        (0xE0, GAMMA_P, 0), (0xE1, GAMMA_N, 0),
        (0xF0, [0x3C], 0), (0xF0, [0x69], 0),
        (0x21, [], 0), (0x29, [], 100), (0x13, [], 20),
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
    (0x3A, [0x55], 0), (0x11, [], 150), TE_ON, (0x36, [0x08], 0), (0x29, [], 255),
]

# ---------- KeDei framings ----------

class KedeiV5:
    """2-byte cmd/data units, control bits packed, CS pulse per unit."""
    def __init__(self, cs):
        self.s = spidev.SpiDev(); self.s.open(0, cs)
        self.s.max_speed_hz = 16_000_000; self.s.mode = 0

    def close(self): self.s.close()

    def reset(self):
        self.s.writebytes([0x00]); time.sleep(0.15)
        self.s.writebytes([0x01]); time.sleep(0.25)

    def cmd(self, c):
        self.s.writebytes([c >> 1, ((c & 1) << 5) | 0x11])
        self.s.writebytes([c >> 1, ((c & 1) << 5) | 0x1B])

    def dat(self, d):
        self.s.writebytes([d >> 1, ((d & 1) << 5) | 0x15])
        self.s.writebytes([d >> 1, ((d & 1) << 5) | 0x1F])

    def init(self):
        self.reset()
        self.cmd(0x00)
        self.cmd(0x11); time.sleep(0.2)
        for c, ps in [(0xEE,[2,1,2,1]),
                      (0xED,[0,0,0x9A,0x9A,0x9B,0x9B,0,0,0,0,0xAE,0xAE,1,0xA2,0]),
                      (0xB4,[0]), (0xC0,[0x10,0x3B,0,2,0x11]), (0xC1,[0x10]),
                      (0xC8,[0,0x46,0x12,0x20,0x0C,0,0x56,0x12,0x67,2,0,0x0C]),
                      (0xD0,[0x44,0x42,6]), (0xD1,[0x43,0x16]), (0xD2,[4,0x22]),
                      (0xD3,[4,0x12]), (0xD4,[7,0x12]), (0xE9,[0]), (0xC5,[8]),
                      (0x35,[0]), (0x3A,[0x66]), (0x36,[0x0A])]:
            self.cmd(c)
            for p in ps: self.dat(p)
        self.cmd(0x11); time.sleep(0.15)
        self.cmd(0x29); time.sleep(0.03)

class KedeiV63:
    """32-bit units: {00 11 00 cmd} / {00 15 00 dat}, mode 3, CS pulse per unit."""
    def __init__(self, cs):
        self.s = spidev.SpiDev(); self.s.open(0, cs)
        self.s.max_speed_hz = 4_000_000; self.s.mode = 3

    def close(self): self.s.close()

    def unit(self, b): self.s.writebytes(list(b))

    def cmd(self, c, params=()):
        self.unit([0x00, 0x11, 0x00, c])
        for p in params:
            self.unit([0x00, 0x15, 0x00, p])

    def init(self):
        self.unit([0, 0, 0, 0]); time.sleep(0.12)       # reset
        self.unit([0, 1, 0, 0]); time.sleep(0.05)
        self.unit([0, 0x11, 0, 0]); time.sleep(0.06)
        self.cmd(0xB9, [0xFF, 0x83, 0x57]); time.sleep(0.005)
        self.cmd(0xB6, [0x2C])
        self.cmd(0x11); time.sleep(0.15)
        self.cmd(0x35, [0x00])
        self.cmd(0x3A, [0x55])
        self.cmd(0xB0, [0x68])
        self.cmd(0xCC, [0x09])
        self.cmd(0xB3, [0x43, 0x00, 0x06, 0x06])
        self.cmd(0xB1, [0x00, 0x15, 0x1C, 0x1C, 0x83, 0x44])
        self.cmd(0xC0, [0x24, 0x24, 0x01, 0x3C, 0x1E, 0x08])
        self.cmd(0xB4, [0x02, 0x40, 0x00, 0x2A, 0x2A, 0x0D, 0x4F])
        self.cmd(0x36, [0x08])
        self.cmd(0x29); time.sleep(0.2)

# ---------- main ----------

if __name__ == "__main__":
    log("=== probe3.py ===")
    to_spidev()
    pc_set(18, "op", "dh")

    log("\n[1] baseline TE scan (pins as input+pullup, nothing initialized)")
    pins_input_pu(exclude=(18,))
    n, res = sample_pins(1.5)
    report_sample("baseline", n, res)

    log("\n[2] GPIO24 backlight-gate test (temp channel vs GPIO24 level)")
    t = touch_open()
    for tag, act in [("24=pullup", lambda: pc_set(24, "ip", "pu")),
                     ("24=LOW   ", lambda: pc_set(24, "op", "dl")),
                     ("24=pullup", lambda: pc_set(24, "ip", "pu"))]:
        act(); time.sleep(0.15)
        tm, ts = adc_mean(t, 0x87)
        ym, ys = adc_mean(t, 0x90)
        log(f"  {tag}: TEMP0 {tm:7.1f}±{ts:4.1f}   Y(ctl) {ym:7.1f}±{ys:4.1f}")
    t.close()

    log("\n[3] protocol candidates + TE scan after each init")
    def dbi_attempt(tag, seq, **kw):
        d = DBI(**kw)
        try: d.init(seq)
        finally: d.close()
        n, res = sample_pins(1.2)
        report_sample(tag, n, res)

    dbi_attempt("P1 byteDBI ST7796 dc22 rst27 m0", st7796_seq(0x05),
                dc=22, rst=27, wide=False)
    dbi_attempt("P2 16bit ILI9486 dc22 rst27   ", ILI9486_MHS,
                dc=22, rst=27, wide=True)
    dbi_attempt("P3 16bit ILI9486 dc24 rst25   ", ILI9486_MHS,
                dc=24, rst=25, wide=True)
    dbi_attempt("P4 16bit ST7796 dc24 rst25    ", st7796_seq(0x55),
                dc=24, rst=25, wide=True)
    dbi_attempt("P5 byteDBI ST7796 dc22 rst27 m3", st7796_seq(0x05),
                dc=22, rst=27, wide=False, mode=3)
    dbi_attempt("P6 byteDBI ST7796 CS-activeHIGH", st7796_seq(0x05),
                dc=22, rst=27, wide=False, cs_invert=True)

    for cs in (0, 1):
        k = KedeiV5(cs)
        try: k.init()
        finally: k.close()
        n, res = sample_pins(1.2)
        report_sample(f"P7 KeDeiV5 framing CE{cs}      ", n, res)

    k = KedeiV63(0)
    try: k.init()
    finally: k.close()
    n, res = sample_pins(1.2)
    report_sample("P8 KeDeiV63 framing CE0 m3    ", n, res)

    log("\n[4] restore ads7846 (spi0.0 stays on spidev)")
    unbind("spi0.1", "spidev")
    override("spi0.1", "")
    pc_set(17, "ip", "pu")
    bindto("spi0.1", "ads7846")
    log("=== probe3.py done ===")
