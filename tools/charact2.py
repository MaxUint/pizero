#!/usr/bin/env python3
"""Self-paced KeDei v6.3 characterization. RUN ON THE PI:

    sudo python3 ~/charact2.py

It paints a state, asks you to describe what you see, and only then moves
on. Answers + timing go to ~/charact2.log. Just type what you see (short
is fine: "black", "white", "bands of color", "garbage") and press ENTER.
"""
import ctypes, fcntl, os, subprocess, time

LOG = open(os.path.expanduser("~/charact2.log"), "a")
T0 = time.time()

def log(s):
    line = f"[{time.time()-T0:7.1f}s] {s}"
    LOG.write(line + "\n"); LOG.flush()

def say(s):
    print(s, flush=True); log(f"PROMPT: {s}")

def ask(q):
    a = input(f"\n>>> {q}\n    your answer: ").strip()
    log(f"Q: {q}")
    log(f"A: {a}")
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
    except OSError as e: print(f"bind {dev} {drv}: {e}")

def spidev_mode():
    subprocess.run(["modprobe", "spidev"], capture_output=True)
    unbind("spi0.0", "panel-mipi-dbi-spi")
    unbind("spi0.1", "ads7846")
    for d in ("spi0.0", "spi0.1"):
        override(d, "spidev")
        if not os.path.exists(f"/sys/bus/spi/drivers/spidev/{d}"):
            bindto(d, "spidev")
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
    def __init__(self, cs=0, mode=3, speed=8_000_000):
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

    def rep(self, unit, count):
        b = ctypes.create_string_buffer(unit, len(unit))
        arr = (XF * CH)()
        for j in range(CH):
            arr[j].tx_buf = ctypes.addressof(b)
            arr[j].len = len(unit)
            arr[j].speed_hz = self.speed
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

    def init(self):
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

    def window(self, x0, x1, y0, y1):
        self.cmd(0x2A, [x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF])
        self.cmd(0x2B, [y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF])
        self.cmd(0x2C)

    def px(self, col):
        return bytes([0, 0x15, col >> 8, col & 0xFF])

    def fill(self, col):
        self.window(0, 319, 0, 479)
        self.rep(self.px(col), 320 * 480)

    def bands(self):
        self.window(0, 319, 0, 479)
        for col in (0xFFFF, 0xFFE0, 0x07FF, 0x07E0,
                    0xF81F, 0xF800, 0x001F, 0x0000):
            self.rep(self.px(col), 320 * 60)

if __name__ == "__main__":
    if os.geteuid() != 0:
        raise SystemExit("run with sudo")
    log("=== charact2 session start ===")
    say("Self-paced test. Watch the glass; answer each question, ENTER to move on.")

    ask("STATE 0 — before I touch anything: what is on the screen right now?")

    spidev_mode()
    k = V63()

    say("painting STATE 1: v6.3 init + fill BLACK (takes ~4s)...")
    k.init(); k.fill(0x0000)
    ask("STATE 1 — expect solid BLACK. What do you see? (and did you see it happen?)")

    say("STATE 2: doing NOTHING for 15 seconds — watch whether it changes on its own...")
    time.sleep(15)
    ask("STATE 2 — after 15s of silence: did the screen change by itself? what is it now?")

    say("painting STATE 3: fill RED (no re-init, ~4s)...")
    k.fill(0xF800)
    ask("STATE 3 — expect solid RED (blue would mean BGR). What do you see?")

    say("painting STATE 4: 8 color bands (~4s)...")
    k.bands()
    ask("STATE 4 — expect 8 horizontal bands: white/yellow/cyan/green/magenta/red/blue/black."
        " What do you see (order top to bottom)?")

    say("STATE 5: REBINDING the touch driver (ads7846) — its probe sends SPI traffic...")
    unbind("spi0.1", "spidev")
    override("spi0.1", "")
    bindto("spi0.1", "ads7846")
    time.sleep(1.5)
    ask("STATE 5 — did rebinding the touch driver change the screen? what is it now?")

    say("STATE 6: now TOUCH the screen firmly a few times (drag a finger around)...")
    time.sleep(8)
    ask("STATE 6 — after touching: did the display content change/corrupt? what is it now?")

    say("STATE 7: unbinding touch again, then re-init + GREEN fill...")
    unbind("spi0.1", "ads7846")
    override("spi0.1", "spidev")
    bindto("spi0.1", "spidev")
    time.sleep(0.3)
    k.init(); k.fill(0x07E0)
    ask("STATE 7 — expect solid GREEN. What do you see?")

    say("STATE 8: leaving the image alone, touch driver stays UNBOUND this time.")
    ask("STATE 8 — final check 10s later: is the green still there?")

    k.close()
    ask("Anything else you noticed during the whole run (flickers, partial rows, tint)?")
    say("Done. Touch driver left UNBOUND deliberately (suspect it corrupts the FPGA).")
    say("Log written to ~/charact2.log")
    log("=== charact2 session end ===")
