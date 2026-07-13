#!/usr/bin/env python3
"""LIVE protocol sweep — 18 numbered candidates, each ends with a full-screen
BLACK fill. Run as root while a human watches the glass. Fixed ~9s grid per
step; log mirrored to ~/sweep.log with timestamps.

Candidates: 16-bit DBI variants (endian/mode/DC-polarity/LSB/pins), byte-LSB,
9-bit 3-wire, ILI9488 init, KeDei v5 + v6.3 with REAL pixel fills (batched
ioctl, cs_change per unit), raw dumb-framebuffer streams, KeDei-wake combos.
"""
import ctypes, fcntl, os, subprocess, sys, time

import spidev

LOGF = open(os.path.expanduser("~/sweep.log"), "a")
T0 = time.time()

def log(s):
    line = f"[{time.time()-T0:6.1f}s] {s}"
    print(line, flush=True)
    LOGF.write(line + "\n"); LOGF.flush()

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
    except OSError: pass

REV = bytes(int(f"{i:08b}"[::-1], 2) for i in range(256))

# ---------------- generic DBI ----------------

class DBI:
    def __init__(self, dc, rst, wide=True, mode=0, endian="be", lsb=False,
                 dc_inv=False, cs=0, speed=12_000_000):
        self.dc, self.rst, self.wide = dc, rst, wide
        self.endian, self.lsb, self.dc_inv = endian, lsb, dc_inv
        self.s = spidev.SpiDev(); self.s.open(0, cs)
        self.s.max_speed_hz = speed; self.s.mode = mode

    def close(self):
        self.s.close()

    def _tx(self, buf):
        if self.lsb: buf = bytes(REV[b] for b in buf)
        try: self.s.writebytes2(buf)
        except AttributeError:
            for i in range(0, len(buf), 4096):
                self.s.writebytes(list(buf[i:i+4096]))

    def _word(self, b):
        return bytes([0, b]) if self.endian == "be" else bytes([b, 0])

    def _dc(self, level):
        if self.dc_inv: level = not level
        pc_set(self.dc, "op", "dh" if level else "dl")

    def cmd(self, c, params=(), delay=0):
        self._dc(False)
        self._tx(self._word(c) if self.wide else bytes([c]))
        if params:
            self._dc(True)
            self._tx(b"".join(self._word(p) for p in params) if self.wide
                     else bytes(params))
        if delay: time.sleep(delay / 1000)

    def reset(self):
        pc_set(self.rst, "op", "dh"); time.sleep(0.02)
        pc_set(self.rst, "dl");       time.sleep(0.02)
        pc_set(self.rst, "dh");       time.sleep(0.15)

    def init(self, seq):
        self.reset()
        for c, p, d in seq:
            self.cmd(c, p, d)

    def fill_black(self, w, h):
        self.cmd(0x2A, [0, 0, (w-1) >> 8, (w-1) & 0xFF])
        self.cmd(0x2B, [0, 0, (h-1) >> 8, (h-1) & 0xFF])
        self.cmd(0x2C)
        self._dc(True)
        self._tx(bytes(w * h * 2))
        self.cmd(0x00)

# init sequences (all landscape MADCTL 0x28 unless noted)
def st7796(colmod=0x55, madctl=0x28):
    return [
        (0x11, [], 255), (0x36, [madctl], 0), (0x3A, [colmod], 0),
        (0xF0, [0xC3], 0), (0xF0, [0x96], 0), (0xB4, [0x01], 0), (0xB7, [0xC6], 0),
        (0xC0, [0x80, 0x45], 0), (0xC1, [0x13], 0), (0xC2, [0xA7], 0), (0xC5, [0x0A], 0),
        (0xE8, [0x40, 0x8A, 0, 0, 0x29, 0x19, 0xA5, 0x33], 0),
        (0xE0, [0xD0,8,0x0F,6,6,0x33,0x30,0x33,0x47,0x17,0x13,0x13,0x2B,0x31], 0),
        (0xE1, [0xD0,0x0A,0x11,0x0B,9,7,0x2F,0x33,0x47,0x38,0x15,0x16,0x2C,0x32], 0),
        (0xF0, [0x3C], 0), (0xF0, [0x69], 200), (0x21, [], 0), (0x29, [], 100),
    ]

ILI9486_MHS = [
    (0x01, [], 150),
    (0xF1, [0x36, 4, 0, 0x3C, 0x0F, 0x8F], 0),
    (0xF2, [0x18, 0xA3, 0x12, 2, 0xB2, 0x12, 0xFF, 0x10, 0], 0),
    (0xF8, [0x21, 4], 0), (0xF9, [0, 8], 0),
    (0x36, [0x28], 0), (0xB4, [0], 0), (0xC1, [0x41], 0),
    (0xC5, [0, 0x91, 0x80, 0], 0),
    (0xE0, [0x0F,0x1F,0x1C,0x0C,0x0F,8,0x48,0x98,0x37,0x0A,0x13,4,0x11,0x0D,0], 0),
    (0xE1, [0x0F,0x32,0x2E,0x0B,0x0D,5,0x47,0x75,0x37,6,0x10,3,0x24,0x20,0], 0),
    (0x3A, [0x55], 0), (0x11, [], 150), (0x29, [], 255),
]

MIS35_9488 = [
    (0x01, [], 150),
    (0xC0, [0x0F, 0x0F], 0), (0xC1, [0x47], 0), (0xC5, [0, 0x47, 0x80], 0),
    (0xB0, [0x0A], 0), (0xB1, [0xA0], 0), (0xB4, [2], 0), (0xB6, [2, 2], 0),
    (0x36, [0x48], 0), (0x3A, [0x55], 0), (0x21, [], 0), (0xE9, [0], 0),
    (0xF7, [0xA9, 0x51, 0x2C, 0x82], 0),
    (0xE0, [0,7,0x0B,3,0x0F,5,0x30,0x56,0x47,4,0x0B,0x0A,0x2D,0x37,0x0F], 0),
    (0xE1, [0,0x0E,0x13,4,0x11,7,0x39,0x45,0x50,7,0x10,0x0D,0x32,0x36,0x0F], 0),
    (0x11, [], 255), (0x29, [], 120),
]

# ---------------- 9-bit 3-wire ----------------

def ninebit_words(pairs):
    """pairs = [(dcbit, byte), ...] -> packed bytes, padded with NOP cmds."""
    while len(pairs) % 8: pairs = pairs + [(0, 0)]
    acc = 0; nbits = 0; out = bytearray()
    for dc, b in pairs:
        acc = (acc << 9) | (dc << 8) | b; nbits += 9
        while nbits >= 8:
            out.append((acc >> (nbits - 8)) & 0xFF); nbits -= 8
    return bytes(out)

def step_3wire(rst):
    s = spidev.SpiDev(); s.open(0, 0)
    s.max_speed_hz = 12_000_000; s.mode = 0
    pc_set(rst, "op", "dh"); time.sleep(0.02)
    pc_set(rst, "dl"); time.sleep(0.02)
    pc_set(rst, "dh"); time.sleep(0.15)
    pairs = []
    for c, params, delay in st7796(0x55, 0x28):
        pairs.append((0, c))
        pairs += [(1, p) for p in params]
    s.writebytes2(ninebit_words(pairs)); time.sleep(0.4)
    # pad with NOPs at the FRONT so pixel words stay 8-aligned after 0x2C
    # (a NOP after 0x2C would terminate the RAM write)
    win = [(0, 0x2A), (1,0),(1,0),(1,1),(1,0xDF),
           (0, 0x2B), (1,0),(1,0),(1,1),(1,0x3F), (0, 0x2C)]
    pad = [(0, 0)] * ((-len(win)) % 8)
    s.writebytes2(ninebit_words(pad + win))
    pat = ninebit_words([(1, 0)] * 8)                # 8 data words = 9 bytes
    px = pat * (480 * 320 * 2 // 8)
    for i in range(0, len(px), 4095):                # 4095 = 9-byte multiple:
        s.writebytes2(px[i:i+4095])                  # CS pulses stay word-aligned
    s.close()

# ---------------- KeDei (batched ioctl, cs_change per unit) ----------------

class XF(ctypes.Structure):
    _fields_ = [("tx_buf", ctypes.c_uint64), ("rx_buf", ctypes.c_uint64),
                ("len", ctypes.c_uint32), ("speed_hz", ctypes.c_uint32),
                ("delay_usecs", ctypes.c_uint16), ("bits_per_word", ctypes.c_uint8),
                ("cs_change", ctypes.c_uint8), ("tx_nbits", ctypes.c_uint8),
                ("rx_nbits", ctypes.c_uint8), ("word_delay_usecs", ctypes.c_uint8),
                ("pad", ctypes.c_uint8)]

def spi_ioc_message(n):
    return 0x40006B00 | ((n * 32) << 16)

class Kedei:
    def __init__(self, cs, mode=0, speed=8_000_000):
        self.fd = os.open(f"/dev/spidev0.{cs}", os.O_RDWR)
        m = ctypes.c_uint8(mode)
        fcntl.ioctl(self.fd, 0x40016B01, m)          # WR_MODE
        sp = ctypes.c_uint32(speed)
        fcntl.ioctl(self.fd, 0x40046B04, sp)         # WR_MAX_SPEED
        self.speed = speed

    def close(self): os.close(self.fd)

    def units(self, unit_list):
        """each unit = bytes; sent as its own CS-framed transfer, batched."""
        CH = 128
        for i in range(0, len(unit_list), CH):
            chunk = unit_list[i:i+CH]
            bufs = [ctypes.create_string_buffer(u, len(u)) for u in chunk]
            arr = (XF * len(chunk))()
            for j, (u, b) in enumerate(zip(chunk, bufs)):
                arr[j].tx_buf = ctypes.addressof(b)
                arr[j].len = len(u)
                arr[j].speed_hz = self.speed
                arr[j].cs_change = 1
            fcntl.ioctl(self.fd, spi_ioc_message(len(chunk)), arr)

    def rep_units(self, unit, count, unit2=None):
        """Send `unit` count times (or alternate unit/unit2 pairs count times),
        each as its own CS-framed transfer."""
        CH = 128
        b = ctypes.create_string_buffer(bytes(unit), len(unit))
        b2 = ctypes.create_string_buffer(bytes(unit2), len(unit2)) if unit2 else None
        per = 2 if unit2 else 1
        arr = (XF * CH)()
        for j in range(CH):
            src = b if (per == 1 or j % 2 == 0) else b2
            arr[j].tx_buf = ctypes.addressof(src)
            arr[j].len = len(unit)
            arr[j].speed_hz = self.speed
            arr[j].cs_change = 1
        total = count * per
        full, rem = divmod(total, CH)
        for _ in range(full):
            fcntl.ioctl(self.fd, spi_ioc_message(CH), arr)
        if rem:
            arr2 = (XF * rem)()
            for j in range(rem):
                arr2[j] = arr[j]
            fcntl.ioctl(self.fd, spi_ioc_message(rem), arr2)

class KedeiV5(Kedei):
    def cmd(self, c):
        self.units([bytes([c >> 1, ((c & 1) << 5) | 0x11]),
                    bytes([c >> 1, ((c & 1) << 5) | 0x1B])])
    def dat(self, d):
        self.units([bytes([d >> 1, ((d & 1) << 5) | 0x15]),
                    bytes([d >> 1, ((d & 1) << 5) | 0x1F])])
    def go(self):
        self.units([b"\x00"]); time.sleep(0.15)
        self.units([b"\x01"]); time.sleep(0.25)
        self.cmd(0x00)
        self.cmd(0x11); time.sleep(0.2)
        seq = [(0xEE,[2,1,2,1]),
               (0xED,[0,0,0x9A,0x9A,0x9B,0x9B,0,0,0,0,0xAE,0xAE,1,0xA2,0]),
               (0xB4,[0]), (0xC0,[0x10,0x3B,0,2,0x11]), (0xC1,[0x10]),
               (0xC8,[0,0x46,0x12,0x20,0x0C,0,0x56,0x12,0x67,2,0,0x0C]),
               (0xD0,[0x44,0x42,6]), (0xD1,[0x43,0x16]), (0xD2,[4,0x22]),
               (0xD3,[4,0x12]), (0xD4,[7,0x12]), (0xE9,[0]), (0xC5,[8]),
               (0x3A,[0x66]), (0x36,[0x0A])]
        for c, ps in seq:
            self.cmd(c)
            for p in ps: self.dat(p)
        self.cmd(0x11); time.sleep(0.15)
        self.cmd(0x29); time.sleep(0.03)
        # window 480x320 + black fill (2 units per pixel, 18-bit style)
        self.cmd(0x2A)
        for v in (0, 0, 1, 0xDF): self.dat(v)
        self.cmd(0x2B)
        for v in (0, 0, 1, 0x3F): self.dat(v)
        self.cmd(0x2C)
        # faithful to FREEWING source: each pixel = 0x15 unit then 0x1F unit
        self.rep_units(b"\x00\x00\x15", 480 * 320, unit2=b"\x00\x00\x1f")
        self.cmd(0x29)

class KedeiV63(Kedei):
    def cmd(self, c, params=()):
        self.units([bytes([0, 0x11, 0, c])] +
                   [bytes([0, 0x15, 0, p]) for p in params])
    def go(self):
        self.units([bytes(4)]); time.sleep(0.12)
        self.units([bytes([0, 1, 0, 0])]); time.sleep(0.05)
        self.units([bytes([0, 0x11, 0, 0])]); time.sleep(0.06)
        self.cmd(0xB9, [0xFF, 0x83, 0x57]); time.sleep(0.005)
        self.cmd(0xB6, [0x2C])
        self.cmd(0x11); time.sleep(0.15)
        self.cmd(0x3A, [0x55])
        self.cmd(0xB0, [0x68])
        self.cmd(0xCC, [0x09])
        self.cmd(0xB3, [0x43, 0, 6, 6])
        self.cmd(0xB1, [0, 0x15, 0x1C, 0x1C, 0x83, 0x44])
        self.cmd(0xC0, [0x24, 0x24, 1, 0x3C, 0x1E, 8])
        self.cmd(0xB4, [2, 0x40, 0, 0x2A, 0x2A, 0x0D, 0x4F])
        self.cmd(0x36, [0x08])
        self.cmd(0x29); time.sleep(0.1)
        self.cmd(0x2A, [0, 0, 1, 0x3F])
        self.cmd(0x2B, [0, 0, 1, 0xDF])
        self.cmd(0x2C)
        self.rep_units(b"\x00\x15\x00\x00", 320 * 480)
        self.cmd(0x29)

# ---------------- raw framebuffer streams ----------------

def step_rawframes(cs):
    s = spidev.SpiDev(); s.open(0, cs)
    s.max_speed_hz = 16_000_000; s.mode = 0
    try: s.no_cs = True
    except Exception: pass
    frame = bytes(480 * 320 * 2)
    for _ in range(2):                       # CS-framed whole frames
        pc_set(8 if cs == 0 else 7, "op", "dl")
        for i in range(0, len(frame), 4096):
            s.writebytes2(frame[i:i+4096])
        pc_set(8 if cs == 0 else 7, "dh")
        time.sleep(0.1)
    # CS-less continuous stream (CS parked high)
    for i in range(0, len(frame), 4096):
        s.writebytes2(frame[i:i+4096])
    s.close()

def kedei_wake():
    for cs in (0, 1):
        k = Kedei(cs)
        k.units([b"\x00"]); time.sleep(0.12)
        k.units([b"\x01"]); time.sleep(0.15)
        k.close()

# ---------------- step table ----------------

def kedei_step(cls, cs, **kw):
    k = cls(cs, **kw)
    try: k.go()
    finally: k.close()

def dbi_step(seq, w=480, h=320, **kw):
    def fn():
        d = DBI(**kw)
        try:
            d.init(seq)
            d.fill_black(w, h)
        finally: d.close()
    return fn

STEPS = [
 ("16bit BE dc24 rst25 ILI9486-MHS init", dbi_step(ILI9486_MHS, dc=24, rst=25)),
 ("16bit BE dc24 rst25 ST7796 COLMOD 0x05", dbi_step(st7796(0x05), dc=24, rst=25)),
 ("16bit BE dc24 rst25 ST7796 mode3", dbi_step(st7796(), dc=24, rst=25, mode=3)),
 ("16bit LITTLE-endian dc24 rst25 ST7796", dbi_step(st7796(), dc=24, rst=25, endian="le")),
 ("16bit BE dc24 rst25 ST7796 DC-INVERTED", dbi_step(st7796(), dc=24, rst=25, dc_inv=True)),
 ("16bit BE LSB-first dc24 rst25 ST7796", dbi_step(st7796(), dc=24, rst=25, lsb=True)),
 ("16bit BE dc22 rst27 ILI9486-MHS init", dbi_step(ILI9486_MHS, dc=22, rst=27)),
 ("byte LSB-first dc22 rst27 ST7796", dbi_step(st7796(0x05), dc=22, rst=27, wide=False, lsb=True)),
 ("9-bit 3-wire (no DC) rst25 ST7796", lambda: step_3wire(25)),
 ("16bit BE dc24 rst25 ILI9488-mis35 init", dbi_step(MIS35_9488, 320, 480, dc=24, rst=25)),
 ("KeDei v5 framing CE0 + black fill", lambda: kedei_step(KedeiV5, 0)),
 ("KeDei v5 framing CE1 + black fill", lambda: kedei_step(KedeiV5, 1)),
 ("KeDei v6.3 CE0 mode3 + black fill", lambda: kedei_step(KedeiV63, 0, mode=3)),
 ("KeDei v6.3 CE1 mode3 + black fill", lambda: kedei_step(KedeiV63, 1, mode=3)),
 ("RAW frame stream CE0 (2 framed + 1 CS-less)", lambda: step_rawframes(0)),
 ("RAW frame stream CE1 (2 framed + 1 CS-less)", lambda: step_rawframes(1)),
 ("KeDei-wake bytes then 16bit dc24 rst25 ST7796", lambda: (kedei_wake(), dbi_step(st7796(), dc=24, rst=25)())),
 ("KeDei-wake bytes then byte dc22 rst27 ST7796 (G)", lambda: (kedei_wake(), dbi_step(st7796(0x05), dc=22, rst=27, wide=False)())),
]

if __name__ == "__main__":
    log("=== sweep.py: LIVE 18-step sweep — WATCH THE GLASS ===")
    subprocess.run(["modprobe", "spidev"], capture_output=True)
    unbind("spi0.0", "panel-mipi-dbi-spi")
    unbind("spi0.1", "ads7846")
    for d in ("spi0.0", "spi0.1"):
        override(d, "spidev")
        if not os.path.exists(f"/sys/bus/spi/drivers/spidev/{d}"):
            bindto(d, "spidev")
    time.sleep(0.3)
    pc_set(18, "op", "dh")

    only = {int(a) for a in sys.argv[1:]} if len(sys.argv) > 1 else None
    log("10 second countdown before step 1...")
    time.sleep(10)
    for i, (label, fn) in enumerate(STEPS, 1):
        if only and i not in only:
            continue
        log(f">>> STEP {i:2d}/18: {label}")
        t = time.time()
        try:
            fn()
            log(f"    step {i} done in {time.time()-t:.1f}s")
        except Exception as e:
            log(f"    step {i} FAILED: {e}")
        time.sleep(4)

    log("sweep complete; restoring touch driver")
    unbind("spi0.1", "spidev")
    override("spi0.1", "")
    pc_set(17, "ip", "pu")
    bindto("spi0.1", "ads7846")
    log("=== done — report which step numbers (or times) changed the glass ===")
