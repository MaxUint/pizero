#!/usr/bin/env python3
"""Bisect WHY the screen only reacted when v6.3 ran after v5 traffic.

RUN ON THE PI:   sudo python3 ~/charact3.py

Cumulative stages, self-paced: after each stage, type what (if anything)
changed and press ENTER. Answers logged to ~/charact3.log.

  S1  v6.3 alone (init + BLACK)           <- control, expect nothing
  S2  1-byte wake units (v5-style reset) then v6.3 init + BLACK
  S3  v5 CE0 full (reset+init+fill BLACK) <- is v5 itself the protocol?
  S4  v6.3 CE0 init + BLACK right after   <- exact adjacency from blink.py
  S5  v5 CE1 full (reset+init+fill BLACK)
  S6  v6.3 CE1 init + BLACK               <- completes exact blink.py order
"""
import ctypes, fcntl, os, subprocess, time

LOG = open(os.path.expanduser("~/charact3.log"), "a")
T0 = time.time()

def log(s):
    LOG.write(f"[{time.time()-T0:7.1f}s] {s}\n"); LOG.flush()

def say(s):
    print(s, flush=True); log(f"SAY: {s}")

def ask(q):
    a = input(f"\n>>> {q}\n    your answer: ").strip()
    log(f"Q: {q}"); log(f"A: {a}")
    return a

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

class XF(ctypes.Structure):
    _fields_ = [("tx_buf", ctypes.c_uint64), ("rx_buf", ctypes.c_uint64),
                ("len", ctypes.c_uint32), ("speed_hz", ctypes.c_uint32),
                ("delay_usecs", ctypes.c_uint16), ("bits_per_word", ctypes.c_uint8),
                ("cs_change", ctypes.c_uint8), ("tx_nbits", ctypes.c_uint8),
                ("rx_nbits", ctypes.c_uint8), ("word_delay_usecs", ctypes.c_uint8),
                ("pad", ctypes.c_uint8)]

def IOC(n): return 0x40006B00 | ((n * 32) << 16)
CH = 128

class Kedei:
    def __init__(self, cs, mode=0, speed=8_000_000):
        self.fd = os.open(f"/dev/spidev0.{cs}", os.O_RDWR)
        fcntl.ioctl(self.fd, 0x40016B01, ctypes.c_uint8(mode))
        fcntl.ioctl(self.fd, 0x40046B04, ctypes.c_uint32(speed))
        self.speed = speed

    def close(self): os.close(self.fd)

    def units(self, unit_list):
        for i in range(0, len(unit_list), CH):
            chunk = unit_list[i:i+CH]
            bufs = [ctypes.create_string_buffer(u, len(u)) for u in chunk]
            arr = (XF * len(chunk))()
            for j, b in enumerate(bufs):
                arr[j].tx_buf = ctypes.addressof(b)
                arr[j].len = len(chunk[j])
                arr[j].speed_hz = self.speed
                arr[j].cs_change = 1
            fcntl.ioctl(self.fd, IOC(len(chunk)), arr)

    def rep2(self, unitA, unitB, pairs):
        a = ctypes.create_string_buffer(unitA, len(unitA))
        b = ctypes.create_string_buffer(unitB, len(unitB)) if unitB else None
        per = 2 if unitB else 1
        arr = (XF * CH)()
        for j in range(CH):
            src = a if (per == 1 or j % 2 == 0) else b
            arr[j].tx_buf = ctypes.addressof(src)
            arr[j].len = len(unitA)
            arr[j].speed_hz = self.speed
            arr[j].cs_change = 1
        total = pairs * per
        full, rem = divmod(total, CH)
        for _ in range(full):
            fcntl.ioctl(self.fd, IOC(CH), arr)
        if rem:
            arr2 = (XF * rem)()
            for j in range(rem): arr2[j] = arr[j]
            fcntl.ioctl(self.fd, IOC(rem), arr2)

class V5(Kedei):
    def cmd(self, c):
        self.units([bytes([c >> 1, ((c & 1) << 5) | 0x11]),
                    bytes([c >> 1, ((c & 1) << 5) | 0x1B])])
    def dat(self, d):
        self.units([bytes([d >> 1, ((d & 1) << 5) | 0x15]),
                    bytes([d >> 1, ((d & 1) << 5) | 0x1F])])
    def resetbytes(self):
        self.units([b"\x00"]); time.sleep(0.15)
        self.units([b"\x01"]); time.sleep(0.25)
    def full(self, col=0x0000):
        self.resetbytes()
        self.cmd(0x00)
        self.cmd(0x11); time.sleep(0.2)
        for c, ps in [(0xEE,[2,1,2,1]),
                      (0xED,[0,0,0x9A,0x9A,0x9B,0x9B,0,0,0,0,0xAE,0xAE,1,0xA2,0]),
                      (0xB4,[0]), (0xC0,[0x10,0x3B,0,2,0x11]), (0xC1,[0x10]),
                      (0xC8,[0,0x46,0x12,0x20,0x0C,0,0x56,0x12,0x67,2,0,0x0C]),
                      (0xD0,[0x44,0x42,6]), (0xD1,[0x43,0x16]), (0xD2,[4,0x22]),
                      (0xD3,[4,0x12]), (0xD4,[7,0x12]), (0xE9,[0]), (0xC5,[8]),
                      (0x3A,[0x66]), (0x36,[0x0A])]:
            self.cmd(c)
            for p in ps: self.dat(p)
        self.cmd(0x11); time.sleep(0.15)
        self.cmd(0x29); time.sleep(0.03)
        pseud = ((col >> 5) & 0x40) | ((col << 5) & 0x20)
        hi, lo = col >> 8, col & 0xFF
        self.cmd(0x2A)
        for v in (0, 0, 1, 0xDF): self.dat(v)
        self.cmd(0x2B)
        for v in (0, 0, 1, 0x3F): self.dat(v)
        self.cmd(0x2C)
        self.rep2(bytes([hi, lo, pseud | 0x15]),
                  bytes([hi, lo, pseud | 0x1F]), 480 * 320)
        self.cmd(0x29)

class V63(Kedei):
    def cmd(self, c, params=()):
        self.units([bytes([0, 0x11, 0, c])] +
                   [bytes([0, 0x15, 0, p]) for p in params])
    def go(self, col=0x0000):
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
        self.rep2(bytes([0, 0x15, col >> 8, col & 0xFF]), None, 320 * 480)

if __name__ == "__main__":
    if os.geteuid() != 0:
        raise SystemExit("run with sudo")
    log("=== charact3 start ===")
    subprocess.run(["modprobe", "spidev"], capture_output=True)
    unbind("spi0.0", "panel-mipi-dbi-spi")
    unbind("spi0.1", "ads7846")
    for d in ("spi0.0", "spi0.1"):
        override(d, "spidev")
        if not os.path.exists(f"/sys/bus/spi/drivers/spidev/{d}"):
            bindto(d, "spidev")
    time.sleep(0.3)

    say("Watch the glass through each stage. Report ANY change, however small.")
    ask("S0 — what is on the screen right now?")

    say("S1: v6.3 CE0 mode3, init + BLACK fill (~5s)...")
    k = V63(0, mode=3); k.go(); k.close()
    ask("S1 — any change at all? what?")

    say("S2: 1-byte WAKE units (00, 01) on CE0 then CE1, then v6.3 init + BLACK (~6s)...")
    for cs in (0, 1):
        w = Kedei(cs); w.units([b"\x00"]); time.sleep(0.15)
        w.units([b"\x01"]); time.sleep(0.25); w.close()
    k = V63(0, mode=3); k.go(); k.close()
    ask("S2 — any change at all? what?")

    say("S3: KeDei v5 CE0 FULL: reset + init + BLACK fill (~8s)...")
    v = V5(0); v.full(0x0000); v.close()
    ask("S3 — any change at all? what?")

    say("S4: immediately v6.3 CE0 init + BLACK again (~5s)...")
    k = V63(0, mode=3); k.go(); k.close()
    ask("S4 — any change at all? what?")

    say("S5: KeDei v5 CE1 FULL: reset + init + BLACK fill (~8s)...")
    v = V5(1); v.full(0x0000); v.close()
    ask("S5 — any change at all? what?")

    say("S6: v6.3 CE1 init + BLACK (~5s)...")
    k = V63(1, mode=3); k.go(); k.close()
    ask("S6 — any change at all? what?")

    ask("Final — anything else you noticed across the whole run?")
    say("Done (touch driver left unbound on purpose). Log: ~/charact3.log")
    log("=== charact3 end ===")
