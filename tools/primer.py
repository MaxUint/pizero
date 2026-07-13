#!/usr/bin/env python3
"""Which precondition did the blinks depend on? Self-paced, one boot.

RUN ON THE PI:   sudo python3 ~/primer.py

Stages (describe what you see after each; ENTER = no change):
  S1  rig check via touch chip (automatic — proves SPI bus + script sanity)
  S2  restore blink-era GPIO states (18/24/25/27 high, 22 out) + v6.3 sequence
  S3  KeDei v5 fill preamble (CE0+CE1, like the run that blinked) + v6.3
  S4  boot-overlay emulation (reset pulse + byte-DBI init @48MHz) + v6.3
  S5  touch-like CE1 traffic burst + v6.3
"""
import ctypes, fcntl, os, subprocess, time

LOG = open(os.path.expanduser("~/primer.log"), "a")
T0 = time.time()

def log(s):
    LOG.write(f"[{time.time()-T0:7.1f}s] {s}\n"); LOG.flush()

def say(s):
    print(s, flush=True); log(f"SAY: {s}")

def ask(q):
    a = input(f"\n>>> {q}\n    answer: ").strip()
    log(f"Q: {q}"); log(f"A: {a or '(no change)'}")
    return a

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
    def xfer_rx(self, tx):
        txb = ctypes.create_string_buffer(bytes(tx), len(tx))
        rxb = ctypes.create_string_buffer(len(tx))
        x = XF()
        x.tx_buf = ctypes.addressof(txb); x.rx_buf = ctypes.addressof(rxb)
        x.len = len(tx); x.speed_hz = self.speed; x.cs_change = 1
        fcntl.ioctl(self.fd, IOC(1), x)
        return list(rxb.raw)

class V63(Dev):
    def __init__(self, cs): super().__init__(cs, 3, 8_000_000)
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
        self.cmd(0xB0, [0x68]); self.cmd(0xCC, [0x09])
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
        self.rep2(bytes([0, 0x15, col >> 8, col & 0xFF]), None, 320 * 480)

class V5(Dev):
    def __init__(self, cs): super().__init__(cs, 0, 8_000_000)
    def cmd(self, c):
        self.units([bytes([c >> 1, ((c & 1) << 5) | 0x11]),
                    bytes([c >> 1, ((c & 1) << 5) | 0x1B])])
    def dat(self, d):
        self.units([bytes([d >> 1, ((d & 1) << 5) | 0x15]),
                    bytes([d >> 1, ((d & 1) << 5) | 0x1F])])
    def full_black(self):
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
        self.cmd(0x2A)
        for v in (0, 0, 1, 0xDF): self.dat(v)
        self.cmd(0x2B)
        for v in (0, 0, 1, 0x3F): self.dat(v)
        self.cmd(0x2C)
        self.rep2(b"\x00\x00\x15", b"\x00\x00\x1f", 480 * 320)
        self.cmd(0x29)

ST7796_BYTES = [
    (0x01, [], 150), (0x11, [], 120),
    (0x36, [0x48], 0), (0x3A, [0x05], 0),
    (0xF0, [0xC3], 0), (0xF0, [0x96], 0), (0xB4, [0x01], 0), (0xB7, [0xC6], 0),
    (0xC0, [0x80, 0x45], 0), (0xC1, [0x13], 0), (0xC2, [0xA7], 0), (0xC5, [0x0A], 0),
    (0xE8, [0x40, 0x8A, 0, 0, 0x29, 0x19, 0xA5, 0x33], 0),
    (0xE0, [0xD0,8,0x0F,6,6,0x33,0x30,0x33,0x47,0x17,0x13,0x13,0x2B,0x31], 0),
    (0xE1, [0xD0,0x0A,0x11,0x0B,9,7,0x2F,0x33,0x47,0x38,0x15,0x16,0x2C,0x32], 0),
    (0xF0, [0x3C], 0), (0xF0, [0x69], 0), (0x21, [], 0), (0x29, [], 100),
]

def v63_known_sequence():
    """the four known-blink phases"""
    for cs in (0, 1):
        k = V63(cs)
        k.magic_init()
        k.fill(0x0000)
        k.close()
        time.sleep(1.0)

def blink_era_pins():
    pc_set(18, "op", "dh")
    pc_set(24, "op", "dh")
    pc_set(25, "op", "dh")
    pc_set(27, "op", "dh")
    pc_set(22, "op", "dl")

def boot_overlay_emulation():
    """what panel-mipi-dbi did at boot: reset pulse on 27, byte-DBI ST7796S
    init at 48MHz with DC on 22, backlight-gpio 18 high, plus a pixel burst."""
    pc_set(18, "op", "dh")
    pc_set(27, "op", "dh"); time.sleep(0.02)
    pc_set(27, "dl"); time.sleep(0.02)
    pc_set(27, "dh"); time.sleep(0.12)
    d = Dev(0, 0, 48_000_000)
    def tx(buf):
        for i in range(0, len(buf), 4096):
            os.write(d.fd, buf[i:i+4096])
    for c, params, delay in ST7796_BYTES:
        pc_set(22, "op", "dl")
        tx(bytes([c]))
        if params:
            pc_set(22, "dh")
            tx(bytes(params))
        if delay: time.sleep(delay / 1000)
    pc_set(22, "dl"); tx(b"\x2c")
    pc_set(22, "dh"); tx(bytes(64 * 1024))
    d.close()

def touchlike_traffic():
    t = Dev(1, 0, 2_000_000)
    for _ in range(100):
        t.units([bytes([0x90, 0, 0]), bytes([0xD0, 0, 0]),
                 bytes([0xB0, 0, 0]), bytes([0xC0, 0, 0])])
    t.close()

if __name__ == "__main__":
    if os.geteuid() != 0:
        raise SystemExit("run with sudo")
    log("=== primer session ===")
    spidev_bind()

    say("\n--- S1: rig check (touch chip read, automatic)")
    t = Dev(1, 0, 2_000_000)
    ok = False
    for name, c in (("X", 0xD0), ("Y", 0x90), ("temp", 0x87)):
        r = t.xfer_rx([c, 0, 0])
        v = ((r[1] << 8) | r[2]) >> 3
        say(f"    touch {name}: raw={r} -> {v}")
        if r != [0, 0, 0] and r != [0xFF, 0xFF, 0xFF]:
            ok = True
    t.close()
    if not ok:
        say("!!! RIG CHECK FAILED — bus/script problem, everything else is void.")
        ask("acknowledge (ENTER)")
        raise SystemExit(1)
    say("    RIG OK — bus alive, script clocking real data.")

    ask("S1 done. Eyes on glass from here. ENTER when ready")

    say("\n--- S2: blink-era pin states + known v6.3 sequence (~10s)")
    blink_era_pins()
    v63_known_sequence()
    ask("S2 — pins high + v6.3: what did you see?")

    say("\n--- S3: v5 fill preamble CE0+CE1 (~16s) then v6.3 (~10s)")
    for cs in (0, 1):
        v = V5(cs); v.full_black(); v.close()
    v63_known_sequence()
    ask("S3 — v5 preamble + v6.3: what did you see (including during the preamble)?")

    say("\n--- S4: boot-overlay emulation (byte-DBI @48MHz + reset pulse) then v6.3")
    boot_overlay_emulation()
    v63_known_sequence()
    ask("S4 — boot-emulation + v6.3: what did you see?")

    say("\n--- S5: touch-like CE1 burst then v6.3")
    touchlike_traffic()
    v63_known_sequence()
    ask("S5 — touch-traffic + v6.3: what did you see?")

    ask("FINAL — anything at all during this whole session? summary")
    say("pins left in blink-era state (18/24/25/27 high, 22 low). log: ~/primer.log")
    log("=== primer end ===")
