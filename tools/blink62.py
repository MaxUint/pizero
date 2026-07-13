#!/usr/bin/env python3
"""KeDei v6.2 dialect test (from lzto/RaspberryPi_KeDei_35_lcd_v62).

RUN ON THE PI:   sudo python3 ~/blink62.py

New vs everything tried before:
  - 3-byte prefix-FIRST units: cmd={11 00 c}, data={15 00 d}, pixel={15 hi lo}
  - 4-byte reset toggle {00 01 00 00}/{00 00 00 00}/{00 01 00 00}
  - 0xFF warm-up burst (FPGA framing resync?)
  - OPPOSITE-CS PUMPING: other CS driven HIGH during each unit, LOW between
  - R61529 panel init, mode 0

Variants (self-paced, describe after each phase):
  V1 LCD=CE0, pump CE1   (mirrored layout — our touch is on CE1)
  V2 LCD=CE1, pump CE0   (faithful KeDei layout)
  V3 LCD=CE0, no pumping
  V4 LCD=CE1, no pumping
Each: reset+init (watch!), black band fill, red band fill.
"""
import ctypes, fcntl, mmap, os, struct, subprocess, time

LOG = open("/root/blink62.log" if os.geteuid() == 0
           else os.path.expanduser("~/blink62.log"), "a")
T0 = time.time()

def log(s):
    LOG.write(f"[{time.time()-T0:7.1f}s] {s}\n"); LOG.flush()

def say(s):
    print(s, flush=True); log(f"SAY: {s}")

def ask(q):
    a = input(f"\n>>> {q}\n    answer: ").strip()
    log(f"Q: {q}"); log(f"A: {a or '(no change)'}")
    return a

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

# fast GPIO via gpiomem (SET/CLR registers)
_gpio = None
def gpio_mm():
    global _gpio
    if _gpio is None:
        f = os.open("/dev/gpiomem", os.O_RDWR | os.O_SYNC)
        _gpio = mmap.mmap(f, 4096)
    return _gpio

def gpio_hi(pin):
    struct.pack_into("<I", gpio_mm(), 0x1C, 1 << pin)   # GPSET0

def gpio_lo(pin):
    struct.pack_into("<I", gpio_mm(), 0x28, 1 << pin)   # GPCLR0

class XF(ctypes.Structure):
    _fields_ = [("tx_buf", ctypes.c_uint64), ("rx_buf", ctypes.c_uint64),
                ("len", ctypes.c_uint32), ("speed_hz", ctypes.c_uint32),
                ("delay_usecs", ctypes.c_uint16), ("bits_per_word", ctypes.c_uint8),
                ("cs_change", ctypes.c_uint8), ("tx_nbits", ctypes.c_uint8),
                ("rx_nbits", ctypes.c_uint8), ("word_delay_usecs", ctypes.c_uint8),
                ("pad", ctypes.c_uint8)]

def IOC(n): return 0x40006B00 | ((n * 32) << 16)

class V62:
    """3-byte prefix-first units; optional opposite-CS pumping per unit."""
    def __init__(self, lcd_cs, pump_pin=None, speed=16_000_000):
        self.fd = os.open(f"/dev/spidev0.{lcd_cs}", os.O_RDWR)
        fcntl.ioctl(self.fd, 0x40016B01, ctypes.c_uint8(0))          # mode 0
        fcntl.ioctl(self.fd, 0x40046B04, ctypes.c_uint32(speed))
        self.speed = speed
        self.pump = pump_pin
        self.buf = ctypes.create_string_buffer(4)
        self.x = XF()
        self.x.tx_buf = ctypes.addressof(self.buf)
        self.x.speed_hz = speed
        self.x.cs_change = 1

    def close(self):
        os.close(self.fd)
        if self.pump is not None:
            gpio_hi(self.pump)      # park deselected (kernel idles CS high)

    def unit(self, b):
        self.buf.raw = bytes(b).ljust(4, b"\x00")
        self.x.len = len(b)
        if self.pump is not None: gpio_hi(self.pump)
        fcntl.ioctl(self.fd, IOC(1), self.x)
        if self.pump is not None: gpio_lo(self.pump)

    def cmd(self, c):  self.unit([0x11, 0x00, c])
    def dat(self, d):  self.unit([0x15, 0x00, d])
    def px(self, col): self.unit([0x15, col >> 8, col & 0xFF])

    def reset(self):
        self.unit([0x00, 0x01, 0x00, 0x00]); time.sleep(0.05)
        self.unit([0x00, 0x00, 0x00, 0x00]); time.sleep(0.10)
        self.unit([0x00, 0x01, 0x00, 0x00]); time.sleep(0.05)

    def init(self):
        self.reset()
        self.cmd(0x00); time.sleep(0.01)
        self.cmd(0xFF); self.cmd(0xFF); time.sleep(0.01)
        for _ in range(4): self.cmd(0xFF)
        time.sleep(0.015)
        self.cmd(0x11); time.sleep(0.15)
        seq = [(0xB0, [0x00]),
               (0xB3, [0x02, 0x00, 0x00, 0x00]),
               (0xB9, [0x01, 0x00, 0x0F, 0x0F]),
               (0xC0, [0x13, 0x3B, 0x00, 0x02, 0x00, 0x01, 0x00, 0x43]),
               (0xC1, [0x08, 0x0F, 0x08, 0x08]),
               (0xC4, [0x11, 0x07, 0x03, 0x04]),
               (0xC6, [0x00]),
               (0xC8, [0x03, 0x03, 0x13, 0x5C, 0x03, 0x07, 0x14, 0x08,
                       0x00, 0x21, 0x08, 0x14, 0x07, 0x53, 0x0C, 0x13,
                       0x03, 0x03, 0x21, 0x00]),
               (0x35, [0x00]),
               (0x36, [0x60]),
               (0x3A, [0x55]),
               (0x44, [0x00, 0x01]),
               (0xD0, [0x07, 0x07, 0x1D, 0x03]),
               (0xD1, [0x03, 0x30, 0x10]),
               (0xD2, [0x03, 0x14, 0x04]),
               (0x29, [])]
        for c, ps in seq:
            self.cmd(c)
            for p in ps: self.dat(p)
        time.sleep(0.03)
        self.cmd(0x2A); [self.dat(v) for v in (0x00, 0x00, 0x01, 0x3F)]
        self.cmd(0x2B); [self.dat(v) for v in (0x00, 0x00, 0x01, 0xE0)]
        self.cmd(0xB4); self.dat(0x00)
        self.cmd(0x2C); time.sleep(0.01)

    def band(self, col, rows=100):
        """fill top `rows` rows (320 wide) with color"""
        self.cmd(0x2A); [self.dat(v) for v in (0, 0, 1, 0x3F)]
        self.cmd(0x2B); [self.dat(v) for v in (0, 0, (rows-1) >> 8, (rows-1) & 0xFF)]
        self.cmd(0x2C)
        for _ in range(320 * rows):
            self.px(col)

def variant(name, lcd_cs, pump_pin):
    say(f"\n===== {name} =====")
    v = V62(lcd_cs, pump_pin)
    try:
        say("phase A: reset + FF warmup + R61529 init (~3s)...")
        v.init()
        ask(f"{name} A (init) — what did you see?")
        say("phase B: BLACK band fill, top ~100 rows (~5-8s)...")
        v.band(0x0000)
        ask(f"{name} B (black band) — what did you see?")
        say("phase C: RED band fill (~5-8s)...")
        v.band(0xF800)
        ask(f"{name} C (red band) — what did you see?")
    except Exception as e:
        say(f"    ERROR: {e}")
        ask(f"{name} errored — any change anyway?")
    finally:
        v.close()

if __name__ == "__main__":
    if os.geteuid() != 0:
        raise SystemExit("run with sudo")
    log("=== blink62 session ===")
    spidev_bind()
    say("KeDei v6.2 dialect test — 4 variants x 3 phases. Watch the glass.")
    ask("what is on the screen right now?")
    variant("V1 LCD=CE0 pump=CE1(GPIO7)", 0, 7)
    variant("V2 LCD=CE1 pump=CE0(GPIO8)", 1, 8)
    variant("V3 LCD=CE0 no pump", 0, None)
    variant("V4 LCD=CE1 no pump", 1, None)
    ask("FINAL summary — anything at all?")
    say("log: /root/blink62.log")
    log("=== blink62 end ===")
