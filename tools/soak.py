#!/usr/bin/env python3
"""SOAK TEST — loops the closest-to-blink-era traffic for up to 15 minutes.

RUN ON THE PI:   sudo python3 ~/soak.py

Each ~30s cycle: v5 CE0 fill, v5 CE1 fill, v6.3 CE0 init+fill, v6.3 CE1
init+fill — BLACK on even cycles, WHITE on odd. TAP SPACE whenever you see
ANY change on the glass; press q to stop early. At the end it correlates
taps to cycle+phase: phase-locked taps = causal; random = spontaneous flicker.
Log: /root/soak.log
"""
import ctypes, fcntl, os, select, subprocess, sys, termios, threading, time, tty

T0 = time.time()
EVENTS = []
INTERVALS = []       # (start, end, label)
LOG = open("/root/soak.log" if os.geteuid() == 0
           else os.path.expanduser("~/soak.log"), "a")
DURATION = 15 * 60

def ev(kind, text, quiet=False):
    t = time.time() - T0
    EVENTS.append((t, kind, text))
    line = f"[{t:7.2f}s] {kind:5s} {text}"
    if not quiet: print(line, flush=True)
    LOG.write(line + "\n"); LOG.flush()

def pc_set(*args):
    subprocess.run(["pinctrl", "set"] + [str(a) for a in args], capture_output=True)

def spidev_bind():
    subprocess.run(["modprobe", "spidev"], capture_output=True)
    for drv in ("panel-mipi-dbi-spi", "ads7846"):
        for dev in ("spi0.0", "spi0.1"):
            p = f"/sys/bus/spi/drivers/{drv}/{dev}"
            if os.path.exists(p):
                open(f"/sys/bus/spi/drivers/{drv}/unbind", "w").write(dev)
    for d in ("spi0.0", "spi0.1"):
        open(f"/sys/bus/spi/devices/{d}/driver_override", "w").write("spidev")
        if not os.path.exists(f"/sys/bus/spi/drivers/spidev/{d}"):
            try: open("/sys/bus/spi/drivers/spidev/bind", "w").write(d)
            except OSError: pass
    time.sleep(0.3)

class XF(ctypes.Structure):
    _fields_ = [("tx_buf", ctypes.c_uint64), ("rx_buf", ctypes.c_uint64),
                ("len", ctypes.c_uint32), ("speed_hz", ctypes.c_uint32),
                ("delay_usecs", ctypes.c_uint16), ("bits_per_word", ctypes.c_uint8),
                ("cs_change", ctypes.c_uint8), ("tx_nbits", ctypes.c_uint8),
                ("rx_nbits", ctypes.c_uint8), ("word_delay_usecs", ctypes.c_uint8),
                ("pad", ctypes.c_uint8)]

def IOC(n): return 0x40006B00 | ((n * 32) << 16)
CH = 128

class Dev:
    def __init__(self, cs, mode, speed):
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

class V5(Dev):
    def __init__(self, cs): super().__init__(cs, 0, 8_000_000)
    def cmd(self, c):
        self.units([bytes([c >> 1, ((c & 1) << 5) | 0x11]),
                    bytes([c >> 1, ((c & 1) << 5) | 0x1B])])
    def dat(self, d):
        self.units([bytes([d >> 1, ((d & 1) << 5) | 0x15]),
                    bytes([d >> 1, ((d & 1) << 5) | 0x1F])])
    def full(self, col):
        self.units([b"\x00"]); time.sleep(0.15)
        self.units([b"\x01"]); time.sleep(0.25)
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

class V63(Dev):
    def __init__(self, cs): super().__init__(cs, 3, 8_000_000)
    def cmd(self, c, params=()):
        self.units([bytes([0, 0x11, 0, c])] +
                   [bytes([0, 0x15, 0, p]) for p in params])
    def go(self, col):
        self.units([bytes(4)]); time.sleep(0.12)
        self.units([bytes([0, 1, 0, 0])]); time.sleep(0.05)
        self.units([bytes([0, 0x11, 0, 0])]); time.sleep(0.06)
        self.cmd(0xB9, [0xFF, 0x83, 0x57]); time.sleep(0.005)
        self.cmd(0xB6, [0x2C])
        self.cmd(0x11); time.sleep(0.15)
        self.cmd(0x3A, [0x55])
        self.cmd(0xB0, [0x68]); self.cmd(0xCC, [0x09])
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

STOP = threading.Event()

def phase(label, fn):
    t0 = time.time() - T0
    try: fn()
    except Exception as e: ev("ERR", f"{label}: {e}")
    INTERVALS.append((t0, time.time() - T0, label))

def worker():
    time.sleep(5)
    n = 0
    while not STOP.is_set() and time.time() - T0 < DURATION:
        n += 1
        col = 0x0000 if n % 2 == 0 else 0xFFFF
        cname = "BLACK" if col == 0 else "WHITE"
        ev("CYCLE", f"--- cycle {n} ({cname}) ---")
        for label, fn in [
            (f"c{n} v5 CE0 fill {cname}",  lambda: (lambda v: (v.full(col), v.close()))(V5(0))),
            (f"c{n} v5 CE1 fill {cname}",  lambda: (lambda v: (v.full(col), v.close()))(V5(1))),
            (f"c{n} v63 CE0 init+fill {cname}", lambda: (lambda k: (k.go(col), k.close()))(V63(0))),
            (f"c{n} v63 CE1 init+fill {cname}", lambda: (lambda k: (k.go(col), k.close()))(V63(1))),
        ]:
            if STOP.is_set(): break
            phase(label, fn)
    ev("CYCLE", "soak finished — press q to exit")

if __name__ == "__main__":
    if os.geteuid() != 0:
        raise SystemExit("run with sudo")
    spidev_bind()
    for p, s in ((18, "dh"), (24, "dh"), (25, "dh"), (27, "dh")):
        pc_set(p, "op", s)
    pc_set(22, "op", "dl")
    print("=" * 64)
    print("SOAK: loops v5+v6.3 traffic, alternating BLACK/WHITE fills,")
    print(f"for up to {DURATION//60} minutes. TAP SPACE on ANY screen change.")
    print("Press q to stop whenever you want. Starting in 5s...")
    print("=" * 64)
    w = threading.Thread(target=worker, daemon=True)
    w.start()
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while w.is_alive():
            r, _, _ = select.select([sys.stdin], [], [], 0.1)
            if r:
                ch = sys.stdin.read(1)
                if ch == "q":
                    STOP.set(); break
                ev("KEY", f"*** keypress '{ch}' ***")
        w.join(timeout=30)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

    print("\n===== CORRELATION =====")
    keys = [(t, x) for t, k, x in EVENTS if k == "KEY"]
    if not keys:
        print("  no keypresses — no visible changes in the whole soak.")
    for t, _ in keys:
        where = "in a gap"
        for s, e, label in INTERVALS:
            if s - 0.3 <= t <= e + 1.0:
                where = f"during: {label}"
                break
        print(f"  key @ {t:7.2f}s -> {where}")
    print("log: /root/soak.log")
