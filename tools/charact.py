#!/usr/bin/env python3
"""KeDei v6.3 dialect characterization — 8 numbered persistent states.
Watch the glass; afterwards describe what each state looked like.
Run as root (I run it over SSH; ~6s between steps).

 1. init + fill RED          (pumped CS per unit — known-good framing)
 2. fill GREEN               (no re-init: persistent-state check)
 3. 8 horizontal bands       (white,yellow,cyan,green,magenta,red,blue,black)
 4. fill BLUE via BULK       (1024 units per CS frame — fast-path check)
 5. RED square at top-left area (partial window check)
 6. re-init MODE 0 + fill CYAN  (SPI mode tolerance)
 7. fill MAGENTA bulk @32MHz    (speed ceiling)
 8. bands again (left on glass)
"""
import ctypes, fcntl, os, subprocess, time

T0 = time.time()
def log(s):
    print(f"[{time.time()-T0:6.1f}s] {s}", flush=True)

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

    def px_unit(self, col):
        return bytes([0, 0x15, col >> 8, col & 0xFF])

    def fill(self, col):
        self.window(0, 319, 0, 479)
        self.rep(self.px_unit(col), 320 * 480)

    def fill_bulk(self, col):
        self.window(0, 319, 0, 479)
        blob = self.px_unit(col) * 1024          # 4096 bytes = 1024 units/CS frame
        for _ in range(320 * 480 // 1024):
            os.write(self.fd, blob)

    def bands(self):
        self.window(0, 319, 0, 479)
        for col in (0xFFFF, 0xFFE0, 0x07FF, 0x07E0,
                    0xF81F, 0xF800, 0x001F, 0x0000):
            self.rep(self.px_unit(col), 320 * 60)

if __name__ == "__main__":
    subprocess.run(["modprobe", "spidev"], capture_output=True)
    unbind("spi0.0", "panel-mipi-dbi-spi")
    unbind("spi0.1", "ads7846")
    for d in ("spi0.0", "spi0.1"):
        override(d, "spidev")
        if not os.path.exists(f"/sys/bus/spi/drivers/spidev/{d}"):
            bindto(d, "spidev")
    time.sleep(0.3)

    k = V63()
    def step(n, label, fn):
        log(f">>> STATE {n}: {label}")
        t = time.time()
        try: fn()
        except Exception as e: log(f"    FAILED: {e}")
        log(f"    done in {time.time()-t:.1f}s")
        time.sleep(6)

    step(1, "init + fill RED (pumped)", lambda: (k.init(), k.fill(0xF800)))
    step(2, "fill GREEN (no re-init)", lambda: k.fill(0x07E0))
    step(3, "8 horizontal bands", k.bands)
    step(4, "fill BLUE via BULK writes", lambda: k.fill_bulk(0x001F))
    step(5, "partial window RED square", lambda: (k.window(50, 149, 50, 149),
                                                  k.rep(k.px_unit(0xF800), 100 * 100)))
    k.close()

    def mode0():
        k0 = V63(mode=0)
        k0.init(); k0.fill(0x07FF)
        k0.close()
    step(6, "re-init MODE 0 + fill CYAN", mode0)

    def fast():
        kf = V63(mode=3, speed=32_000_000)
        kf.init(); kf.fill_bulk(0xF81F)
        kf.close()
    step(7, "MAGENTA bulk @32MHz", fast)

    def final():
        kz = V63(mode=3)
        kz.init(); kz.bands()
        kz.close()
    step(8, "bands again (left on glass)", final)

    unbind("spi0.1", "spidev")
    override("spi0.1", "")
    pc_set(17, "ip", "pu")
    bindto("spi0.1", "ads7846")
    log("done — describe what each numbered state looked like")
