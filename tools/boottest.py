#!/usr/bin/env python3
"""Per-boot display test runner. ONE experiment per power cycle.

USAGE (after each FULL power cycle — unplug 15s, replug, boot):

    sudo python3 ~/boottest.py            # runs the next scheduled test
    sudo python3 ~/boottest.py status     # show progress + past results
    sudo python3 ~/boottest.py reset      # start the whole plan over

It keeps state in ~/display_tests/state.json and appends everything to
~/display_tests/results.log. Each test is fully self-paced: it tells you
what it's about to do, waits for ENTER, does it, and asks what you saw.
Type what you saw (ENTER alone = "no change").

The plan adapts:
  T1  cold: ONLY the four v6.3 phases that actually blinked
  ├─ change → proto=v63 → T4 characterize (colors/bands/persistence/window)
  │           then T5..T8 wedge bisection (bulk / 32MHz / mode0 / touch)
  └─ dead   → T2 full original sequence (v5 preamble + v6.3)
        ├─ change → T3 v5 alone, then T9 minimal-preamble bisect
        └─ dead   → TB2 SCLK-idle/mode-3 artifact test → (exhausted: report)
"""
import ctypes, fcntl, json, os, subprocess, sys, time

HOME = os.path.expanduser("~")
DIR = os.path.join(HOME, "display_tests")
STATE_F = os.path.join(DIR, "state.json")
LOG_F = os.path.join(DIR, "results.log")
os.makedirs(DIR, exist_ok=True)
LOG = open(LOG_F, "a")

def log(s):
    LOG.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {s}\n"); LOG.flush()

def say(s):
    print(s, flush=True); log(f"SAY: {s}")

def ask(q):
    a = input(f"\n>>> {q}\n    answer: ").strip()
    log(f"Q: {q}")
    log(f"A: {a or '(no change)'}")
    return a

def load_state():
    if os.path.exists(STATE_F):
        return json.load(open(STATE_F))
    return {"outcomes": {}, "proto": None}

def save_state(st):
    json.dump(st, open(STATE_F, "w"), indent=2)

# ---------------- SPI plumbing ----------------

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
        self.cmd(0x29)

class V63(Kedei):
    def cmd(self, c, params=()):
        self.units([bytes([0, 0x11, 0, c])] +
                   [bytes([0, 0x15, 0, p]) for p in params])
    def magic(self):
        self.units([bytes(4)]); time.sleep(0.12)
        self.units([bytes([0, 1, 0, 0])]); time.sleep(0.05)
        self.units([bytes([0, 0x11, 0, 0])]); time.sleep(0.06)
    def init(self):
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
    def px(self, col): return bytes([0, 0x15, col >> 8, col & 0xFF])
    def fill(self, col):
        self.window(0, 319, 0, 479)
        self.rep2(self.px(col), None, 320 * 480)
    def bands(self):
        self.window(0, 319, 0, 479)
        for col in (0xFFFF, 0xFFE0, 0x07FF, 0x07E0,
                    0xF81F, 0xF800, 0x001F, 0x0000):
            self.rep2(self.px(col), None, 320 * 60)
    def bulk_fill(self, col):
        self.window(0, 319, 0, 479)
        blob = self.px(col) * 1024
        for _ in range(320 * 480 // 1024):
            os.write(self.fd, blob)

# baseline for wedge tests: whatever protocol was proven, black fill
def baseline_black(proto):
    if proto == "v5":
        v = V5(0); v.resetbytes(); v.init(); v.fill(0x0000); v.close()
    else:                      # "v63" / "v63p" (wake bytes) / "v63full" (v5 preamble)
        if proto == "v63p":
            for cs in (0, 1):
                w = Kedei(cs); w.units([b"\x00"]); time.sleep(0.15)
                w.units([b"\x01"]); time.sleep(0.25); w.close()
        elif proto == "v63full":
            v = V5(0); v.resetbytes(); v.init(); v.close()
        k = V63(0, mode=3); k.magic(); k.init(); k.fill(0x0000); k.close()

def fake_touch_traffic():
    """approximate ads7846 probe/read traffic on CE1"""
    t = Kedei(1, mode=0, speed=2_000_000)
    for _ in range(50):
        t.units([bytes([0x90, 0, 0]), bytes([0xD0, 0, 0]),
                 bytes([0xB0, 0, 0]), bytes([0xC0, 0, 0])])
    t.close()

# ---------------- test definitions ----------------
# each test = (title, [ (phase_label, phase_fn) ... ] ) built lazily

def phases_T1():
    """Cold boot: ONLY the four phases that produced taps in blink.py,
    in the same order, plus two bonus phases if it turns out alive."""
    S = {}
    return [
        ("v63 CE0 m3: magic+init", lambda: (S.setdefault("c", V63(0, mode=3)).magic(), S["c"].init())),
        ("v63 CE0 m3: fill BLACK", lambda: S["c"].fill(0x0000)),
        ("v63 CE1 m3: magic+init", lambda: (S.setdefault("d", V63(1, mode=3)).magic(), S["d"].init())),
        ("v63 CE1 m3: fill BLACK", lambda: (S["d"].fill(0x0000), S["d"].close())),
        ("bonus - v63 CE0: fill RED (blue => BGR)", lambda: S["c"].fill(0xF800)),
        ("bonus - v63 CE0: 8 color bands", lambda: (S["c"].bands(), S["c"].close())),
    ]

def phases_T2():
    """Full original blink.py sequence: v5 preamble then v6.3 (both CS)."""
    S = {}
    return [
        ("v5 CE0: reset bytes",  lambda: S.setdefault("a", V5(0)).resetbytes()),
        ("v5 CE0: init",         lambda: S["a"].init()),
        ("v5 CE0: fill BLACK",   lambda: S["a"].fill(0x0000)),
        ("v5 CE0: fill WHITE",   lambda: (S["a"].fill(0xFFFF), S["a"].close())),
        ("v5 CE1: reset bytes",  lambda: S.setdefault("b", V5(1)).resetbytes()),
        ("v5 CE1: init",         lambda: S["b"].init()),
        ("v5 CE1: fill BLACK",   lambda: S["b"].fill(0x0000)),
        ("v5 CE1: fill WHITE",   lambda: (S["b"].fill(0xFFFF), S["b"].close())),
        ("v63 CE0 m3: magic+init", lambda: (S.setdefault("c", V63(0, mode=3)).magic(), S["c"].init())),
        ("v63 CE0 m3: fill BLACK", lambda: (S["c"].fill(0x0000), S["c"].close())),
        ("v63 CE1 m3: magic+init", lambda: (S.setdefault("d", V63(1, mode=3)).magic(), S["d"].init())),
        ("v63 CE1 m3: fill BLACK", lambda: (S["d"].fill(0x0000), S["d"].close())),
    ]

def phases_T3():
    S = {}
    return [
        ("v5 CE0: reset bytes", lambda: S.setdefault("v", V5(0)).resetbytes()),
        ("v5 CE0: init",        lambda: S["v"].init()),
        ("v5 CE0: fill BLACK",  lambda: S["v"].fill(0x0000)),
        ("v5 CE0: fill RED",    lambda: (S["v"].fill(0xF800), S["v"].close())),
    ]

def phases_T9():
    def wake():
        for cs in (0, 1):
            w = Kedei(cs); w.units([b"\x00"]); time.sleep(0.15)
            w.units([b"\x01"]); time.sleep(0.25); w.close()
    def wake_v63():
        wake()
        k = V63(0, mode=3); k.magic(); k.init(); k.fill(0x0000); k.close()
    def v5init_v63():
        v = V5(0); v.resetbytes(); v.init(); v.close()
        k = V63(0, mode=3); k.magic(); k.init(); k.fill(0x0000); k.close()
    return [
        ("1-byte wake (both CS) + v63 init + BLACK", wake_v63),
        ("v5 full init (no fill) + v63 init + BLACK", v5init_v63),
    ]

def phases_T4(proto):
    S = {}
    def start():
        baseline_black(proto)
        S["k"] = V63(0, mode=3)
    return [
        ("baseline init + fill BLACK", start),
        ("fill RED (blue => BGR)",     lambda: S["k"].fill(0xF800)),
        ("fill GREEN",                 lambda: S["k"].fill(0x07E0)),
        ("8 bands: white/yellow/cyan/green/magenta/red/blue/black", lambda: S["k"].bands()),
        ("silence 15s — persistence check", lambda: time.sleep(15)),
        ("partial window: red square top-left", lambda: (S["k"].window(50, 149, 50, 149),
                                                          S["k"].rep2(S["k"].px(0xF800), None, 100 * 100),
                                                          S["k"].close())),
    ]

def phases_wedge(proto, poke_label, poke_fn):
    return [
        (f"baseline BLACK fill ({proto})", lambda: baseline_black(proto)),
        (poke_label, poke_fn),
        (f"baseline again — did the poke wedge it?", lambda: baseline_black(proto)),
    ]

def build_test(tid, proto):
    if tid == "T1": return ("cold: ONLY the v6.3 phases that blinked", phases_T1())
    if tid == "T2": return ("cold: FULL original sequence (v5 preamble + v6.3)", phases_T2())
    if tid == "T3": return ("v5 ALONE from cold boot", phases_T3())
    if tid == "T9": return ("minimal preamble bisect (wake bytes / v5-init + v6.3)", phases_T9())
    if tid == "T4": return ("characterization: colors, bands, persistence, windowing", phases_T4(proto))
    if tid == "T5":
        k = {}
        return ("wedge bisect: BULK writes", phases_wedge(proto, "BULK fill BLUE (1024 units/frame)",
                lambda: (k.setdefault("x", V63(0, mode=3)), k["x"].bulk_fill(0x001F), k["x"].close())))
    if tid == "T6":
        k = {}
        return ("wedge bisect: 32MHz bulk", phases_wedge(proto, "32MHz BULK fill MAGENTA",
                lambda: (k.setdefault("x", V63(0, mode=3, speed=32_000_000)), k["x"].bulk_fill(0xF81F), k["x"].close())))
    if tid == "T7":
        k = {}
        return ("wedge bisect: mode-0 traffic", phases_wedge(proto, "mode-0 init traffic",
                lambda: (k.setdefault("x", V63(0, mode=0)), k["x"].magic(), k["x"].init(), k["x"].close())))
    if tid == "T8":
        return ("wedge bisect: touch-like traffic on CE1", phases_wedge(proto,
                "200 fake touch reads on CE1", fake_touch_traffic))
    if tid == "TB2":
        def m3_unit():
            k = V63(0, mode=3); k.units([bytes([0, 0x11, 0, 0])]); k.close()
        def m0_unit():
            k = V63(0, mode=0); k.units([bytes([0, 0x11, 0, 0])]); k.close()
        return ("artifact test: bare mode-3/mode-0 idle-clock flips", [
            ("single mode-3 unit (SCLK idle goes HIGH)", m3_unit),
            ("single mode-0 unit (SCLK idle goes LOW)", m0_unit),
            ("single mode-3 unit again", m3_unit),
        ])
    return None

def choose(st):
    o = st["outcomes"]
    def obs(t): return o.get(t, {}).get("observed")
    if "T1" not in o: return "T1"
    if obs("T1"):
        st["proto"] = st["proto"] or "v63"
    else:
        if "T2" not in o: return "T2"
        if not obs("T2"):
            if "TB2" not in o: return "TB2"
            return None
        if "T3" not in o: return "T3"
        if obs("T3"):
            st["proto"] = st["proto"] or "v5"
        else:
            if "T9" not in o: return "T9"
            st["proto"] = st["proto"] or ("v63p" if obs("T9") else "v63full")
    for t in ("T4", "T5", "T6", "T7", "T8"):
        if t not in o: return t
    return None

FINAL_MSG = """
ALL SCHEDULED TESTS DONE (or plan exhausted).
Send ~/display_tests/results.log back for analysis.
If the plan exhausted with no observations at all: the original blinks are
unexplained — next stop is the seller listing / a logic analyzer.
"""

# ---------------- main ----------------

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        st = load_state()
        print(json.dumps(st, indent=2))
        nxt = choose(st)
        print(f"\nnext test: {nxt or 'NONE (plan complete)'}")
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] == "reset":
        if os.path.exists(STATE_F): os.remove(STATE_F)
        print("state reset; T1 will run next")
        sys.exit(0)
    if os.geteuid() != 0:
        raise SystemExit("run with: sudo python3 ~/boottest.py")

    st = load_state()
    tid = choose(st)
    if tid is None:
        print(FINAL_MSG); sys.exit(0)

    up = float(open("/proc/uptime").read().split()[0])
    log(f"=== boot test {tid} | uptime {up:.0f}s ===")
    if up > 1200:
        a = ask(f"System has been up {up/60:.0f} min — this test wants a FRESH "
                f"power cycle (unplug 15s). Type 'go' to run anyway, ENTER to abort")
        if a.lower() != "go":
            say("aborted — power cycle, then run me again."); sys.exit(0)

    title, phases = build_test(tid, st.get("proto"))
    say(f"\n===== TEST {tid}: {title} =====")
    say(f"{len(phases)} phases. Watch the glass; after each phase describe what")
    say("you saw (ENTER = no change). Nothing runs until you press ENTER.")
    ask("Ready? (press ENTER when eyes are on the screen)")

    spidev_bind()
    pc_set(18, "op", "dh")
    answers = []
    anything = False
    for i, (label, fn) in enumerate(phases, 1):
        say(f"\n--- phase {i}/{len(phases)}: {label}")
        input("    ENTER to fire...")
        t0 = time.time()
        try:
            fn()
            say(f"    fired ({time.time()-t0:.1f}s)")
        except Exception as e:
            say(f"    PHASE ERROR: {e}")
        a = ask(f"phase {i} '{label}' — what did you see?")
        answers.append({"phase": label, "saw": a})
        if a and a.lower() not in ("n", "no", "nothing", "no change", "-"):
            anything = True

    summary = ask(f"TEST {tid} SUMMARY — did ANYTHING change on the glass during "
                  f"this test? (y/n + anything extra)")
    observed = summary.lower().startswith("y") or (summary == "" and anything)
    st["outcomes"][tid] = {"observed": observed, "summary": summary,
                           "phases": answers, "uptime": up,
                           "when": time.strftime("%Y-%m-%d %H:%M:%S")}
    save_state(st)
    log(f"=== {tid} recorded: observed={observed} ===")

    nxt = choose(st)
    say(f"\nRecorded. NEXT TEST: {nxt or 'none — plan complete!'}")
    if nxt:
        say("Now: sudo poweroff, UNPLUG power ~15s, replug, boot, then run me again:")
        say("    sudo python3 ~/boottest.py")
    else:
        say(FINAL_MSG)
