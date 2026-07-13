#!/usr/bin/env python3
"""Minimal blink test — ONLY the known-working sequence (KeDei v6.3, the four
phases that produced taps), nothing else on the bus.

RUN ON THE PI:   sudo python3 ~/blink.py

Watch the glass. TAP SPACE the instant the screen changes (either direction).
Prints a correlation table at the end; also logs to ~/blink.log.
Press 'q' at the end to exit if it lingers.
"""
import ctypes, fcntl, os, select, subprocess, sys, termios, threading, time, tty

T0 = time.time()
EVENTS = []
LOG = open(os.path.expanduser("~/blink.log"), "a")

def ev(kind, text):
    t = time.time() - T0
    EVENTS.append((t, kind, text))
    line = f"[{t:6.2f}s] {kind:5s} {text}"
    print(line, flush=True)
    LOG.write(line + "\n"); LOG.flush()

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

class V63:
    def __init__(self, cs):
        self.fd = os.open(f"/dev/spidev0.{cs}", os.O_RDWR)
        fcntl.ioctl(self.fd, 0x40016B01, ctypes.c_uint8(3))          # mode 3
        fcntl.ioctl(self.fd, 0x40046B04, ctypes.c_uint32(8_000_000)) # 8 MHz
    def close(self): os.close(self.fd)
    def units(self, unit_list):
        for i in range(0, len(unit_list), CH):
            chunk = unit_list[i:i+CH]
            bufs = [ctypes.create_string_buffer(u, len(u)) for u in chunk]
            arr = (XF * len(chunk))()
            for j, b in enumerate(bufs):
                arr[j].tx_buf = ctypes.addressof(b)
                arr[j].len = len(chunk[j])
                arr[j].speed_hz = 8_000_000
                arr[j].cs_change = 1
            fcntl.ioctl(self.fd, IOC(len(chunk)), arr)
    def rep(self, unit, count):
        b = ctypes.create_string_buffer(unit, len(unit))
        arr = (XF * CH)()
        for j in range(CH):
            arr[j].tx_buf = ctypes.addressof(b)
            arr[j].len = len(unit)
            arr[j].speed_hz = 8_000_000
            arr[j].cs_change = 1
        full, rem = divmod(count, CH)
        for _ in range(full):
            fcntl.ioctl(self.fd, IOC(CH), arr)
        if rem:
            arr2 = (XF * rem)()
            for j in range(rem): arr2[j] = arr[j]
            fcntl.ioctl(self.fd, IOC(rem), arr2)
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
        self.cmd(0x2A, [0, 0, 1, 0x3F])
        self.cmd(0x2B, [0, 0, 1, 0xDF])
        self.cmd(0x2C)
        self.rep(bytes([0, 0x15, col >> 8, col & 0xFF]), 320 * 480)

STATE = {}
STEPS = [
    ("v63 CE0: magic+init", lambda: STATE.setdefault("c", V63(0)).magic_init()),
    ("v63 CE0: fill BLACK", lambda: STATE["c"].fill(0x0000)),
    ("v63 CE1: magic+init", lambda: STATE.setdefault("d", V63(1)).magic_init()),
    ("v63 CE1: fill BLACK", lambda: (STATE["d"].fill(0x0000), STATE["d"].close())),
]

INTERVALS = []

def worker():
    time.sleep(5)
    for label, fn in STEPS:
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
        raise SystemExit("run with sudo")
    spidev_bind()
    print("=" * 64)
    print("KNOWN-WORKING SEQUENCE ONLY (KeDei v6.3, 4 phases).")
    print("WATCH THE GLASS. Tap SPACE the instant anything changes.")
    print("Starting in 5 seconds...")
    print("=" * 64)
    w = threading.Thread(target=worker, daemon=True)
    w.start()
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
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
    print("done. (touch driver left unbound; log: ~/blink.log)")
