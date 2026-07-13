#!/usr/bin/env python3
"""KeDei bisection with live keyboard correlation.

RUN THIS ON THE PI IN YOUR SSH SESSION:   sudo python3 ~/blink.py

Watch the glass. TAP SPACE (or any key) THE INSTANT the screen changes,
in either direction (white->black, black->white, flicker, garbage...).
The script runs 12 short sub-steps with gaps and logs your keypresses on
the same clock, then prints which sub-step each keypress landed in.
Press 'q' at the very end to exit if it doesn't exit by itself.
"""
import ctypes, fcntl, os, select, subprocess, sys, termios, threading, time, tty

T0 = time.time()
EVENTS = []            # (t, kind, text)
LOG = open(os.path.expanduser("~/blink.log"), "a")

def ev(kind, text):
    t = time.time() - T0
    EVENTS.append((t, kind, text))
    line = f"[{t:6.2f}s] {kind:5s} {text}"
    print(line, flush=True)
    LOG.write(line + "\n"); LOG.flush()

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

# ---------------- KeDei plumbing (batched ioctl, cs_change per unit) --------

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
    def init(self):
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
    def fill(self, col):
        pseud = ((col >> 5) & 0x40) | ((col << 5) & 0x20)
        hi, lo = col >> 8, col & 0xFF
        self.cmd(0x2A)
        for v in (0, 0, 1, 0xDF): self.dat(v)
        self.cmd(0x2B)
        for v in (0, 0, 1, 0x3F): self.dat(v)
        self.cmd(0x2C)
        self.rep2(bytes([hi, lo, pseud | 0x15]),
                  bytes([hi, lo, pseud | 0x1F]), 480 * 320)

class V63(Kedei):
    def cmd(self, c, params=()):
        self.units([bytes([0, 0x11, 0, c])] +
                   [bytes([0, 0x15, 0, p]) for p in params])
    def magic_init(self):
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
    def fill(self, col):
        hi, lo = col >> 8, col & 0xFF
        self.cmd(0x2A, [0, 0, 1, 0x3F])
        self.cmd(0x2B, [0, 0, 1, 0xDF])
        self.cmd(0x2C)
        self.rep2(bytes([0, 0x15, hi, lo]), None, 320 * 480)

# ---------------- step list ----------------

STATE = {}
def steps():
    yield "v5 CE0: reset bytes only", lambda: STATE.setdefault("a", V5(0)).resetbytes()
    yield "v5 CE0: full init",        lambda: STATE["a"].init()
    yield "v5 CE0: fill BLACK",       lambda: STATE["a"].fill(0x0000)
    yield "v5 CE0: fill WHITE",       lambda: (STATE["a"].fill(0xFFFF), STATE["a"].close(), STATE.pop("a"))
    yield "v5 CE1: reset bytes only", lambda: STATE.setdefault("b", V5(1)).resetbytes()
    yield "v5 CE1: full init",        lambda: STATE["b"].init()
    yield "v5 CE1: fill BLACK",       lambda: STATE["b"].fill(0x0000)
    yield "v5 CE1: fill WHITE",       lambda: (STATE["b"].fill(0xFFFF), STATE["b"].close(), STATE.pop("b"))
    yield "v63 CE0 m3: magic+init",   lambda: STATE.setdefault("c", V63(0, mode=3)).magic_init()
    yield "v63 CE0 m3: fill BLACK",   lambda: (STATE["c"].fill(0x0000), STATE["c"].close(), STATE.pop("c"))
    yield "v63 CE1 m3: magic+init",   lambda: STATE.setdefault("d", V63(1, mode=3)).magic_init()
    yield "v63 CE1 m3: fill BLACK",   lambda: (STATE["d"].fill(0x0000), STATE["d"].close(), STATE.pop("d"))

INTERVALS = []          # (start, end, label)

def worker():
    time.sleep(5)
    for label, fn in steps():
        ev("STEP", f">>> {label}")
        t0 = time.time() - T0
        try: fn()
        except Exception as e: ev("ERR", f"{label}: {e}")
        INTERVALS.append((t0, time.time() - T0, label))
        ev("STEP", f"    {label} done")
        time.sleep(3.5)
    ev("STEP", "ALL DONE — press q to finish")

if __name__ == "__main__":
    if os.geteuid() != 0:
        sys.exit("run with sudo")
    subprocess.run(["modprobe", "spidev"], capture_output=True)
    unbind("spi0.0", "panel-mipi-dbi-spi")
    unbind("spi0.1", "ads7846")
    for d in ("spi0.0", "spi0.1"):
        override(d, "spidev")
        if not os.path.exists(f"/sys/bus/spi/drivers/spidev/{d}"):
            bindto(d, "spidev")
    time.sleep(0.3)

    print("=" * 64)
    print("WATCH THE GLASS. Tap SPACE the instant the screen changes")
    print("(either direction). Starting in 5 seconds...")
    print("=" * 64)

    w = threading.Thread(target=worker, daemon=True)
    w.start()

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while w.is_alive() or True:
            r, _, _ = select.select([sys.stdin], [], [], 0.05)
            if r:
                ch = sys.stdin.read(1)
                if ch == "q" and not w.is_alive():
                    break
                ev("KEY", f"*** keypress '{ch}' ***")
            if not w.is_alive() and not select.select([sys.stdin], [], [], 2.0)[0]:
                break
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

    print("\n===== CORRELATION =====")
    for t, kind, text in EVENTS:
        if kind != "KEY": continue
        where = "in a GAP"
        for s, e, label in INTERVALS:
            if s - 0.3 <= t <= e + 1.0:
                where = f"during: {label}"
                break
        else:
            for s, e, label in INTERVALS:
                if t > e: where = f"gap after: {label}"
        print(f"  key @ {t:6.2f}s  ->  {where}")

    # restore touch
    unbind("spi0.1", "spidev")
    override("spi0.1", "")
    pc_set(17, "ip", "pu")
    bindto("spi0.1", "ads7846")
    print("touch driver restored. done.")
