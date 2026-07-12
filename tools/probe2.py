#!/usr/bin/env python3
"""Empirical SPI/pin prober for the 3.5" FPGA display board. Run as root.

Phases:
  A. bind spidev to spi0.0 (and unbind ads7846 from spi0.1)
  B. MISO drive test: does ANYTHING drive MISO during CE0 activity?
  C. pin connectivity scan: classify all non-SPI header GPIOs
  D. protocol-correct MIPI-DBI register reads (manual CS, DC toggled
     while CS stays low), across SPI modes/speeds and both pin theories
  E. restore drivers + pin state

Everything is runtime-only; nothing touches config.txt; no reboot.
"""
import os, subprocess, sys, time

import spidev

LOG = []
def log(s=""):
    print(s, flush=True)
    LOG.append(s)

def sh(cmd, check=True):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and r.returncode != 0:
        log(f"  !! cmd failed: {cmd}\n     {r.stderr.strip()}")
    return r.stdout.strip()

def pc_set(*args):
    subprocess.run(["pinctrl", "set"] + [str(a) for a in args],
                   capture_output=True)

def pc_level(g):
    out = subprocess.run(["pinctrl", "get", str(g)],
                         capture_output=True, text=True).stdout
    if "| hi" in out: return 1
    if "| lo" in out: return 0
    return -1

def hexs(b):
    return " ".join(f"{x:02X}" for x in b)

# ---------- driver bind plumbing ----------

def unbind(dev, drv):
    p = f"/sys/bus/spi/drivers/{drv}/{dev}"
    if os.path.exists(p):
        open(f"/sys/bus/spi/drivers/{drv}/unbind", "w").write(dev)
        log(f"  unbound {dev} from {drv}")

def override(dev, drv):
    open(f"/sys/bus/spi/devices/{dev}/driver_override", "w").write(drv or "\n")

def bind(dev, drv):
    open(f"/sys/bus/spi/drivers/{drv}/bind", "w").write(dev)
    log(f"  bound {dev} to {drv}")

def to_spidev():
    sh("modprobe spidev")
    unbind("spi0.0", "panel-mipi-dbi-spi")
    unbind("spi0.1", "ads7846")
    for d in ("spi0.0", "spi0.1"):
        override(d, "spidev")
        bind(d, "spidev")
    time.sleep(0.3)
    assert os.path.exists("/dev/spidev0.0"), "no /dev/spidev0.0 after bind"

def restore_drivers():
    log("[E] restoring drivers")
    for d in ("spi0.0", "spi0.1"):
        unbind(d, "spidev")
        override(d, "")
    # DC/RESET/BL pins back to something sane before rebinding
    pc_set(27, "op", "dh")
    pc_set(22, "op", "dl")
    pc_set(18, "op", "dl")
    pc_set(9, "a0", "pd")
    pc_set(10, "a0")
    pc_set(11, "a0")
    pc_set(17, "ip", "pu")
    try:
        bind("spi0.0", "panel-mipi-dbi-spi")
    except Exception as e:
        log(f"  !! rebind panel failed: {e}")
    try:
        bind("spi0.1", "ads7846")
    except Exception as e:
        log(f"  !! rebind ads7846 failed: {e}")

# ---------- SPI helpers (manual CS so CS stays low across DC toggles) ----------

CS0, CS1 = 8, 7

def spi_open(speed=1_000_000, mode=0):
    s = spidev.SpiDev()
    s.open(0, 0)
    s.max_speed_hz = speed
    s.mode = mode
    try:
        s.no_cs = True
        nocs = True
    except Exception:
        nocs = False
    return s, nocs

def cs_low(cs):  pc_set(cs, "op", "dl")
def cs_high(cs): pc_set(cs, "op", "dh")

def dbi_read(s, cmd, n, dc, cs=CS0):
    """command byte with DC low, then clock n reply bytes with DC high,
    CS held low the whole time (proper DBI read)."""
    cs_high(cs)
    pc_set(dc, "op", "dl")
    cs_low(cs)
    s.xfer2([cmd])
    pc_set(dc, "dh")
    rx = s.xfer2([0x00] * n)
    cs_high(cs)
    return rx

def reset_pulse(rst):
    pc_set(rst, "op", "dh"); time.sleep(0.12)
    pc_set(rst, "dl");       time.sleep(0.02)
    pc_set(rst, "dh");       time.sleep(0.15)

# ---------- Phase B: MISO drive test ----------

def miso_drive_test():
    log("\n[B] MISO drive test (pull-up vs pull-down on GPIO9 during clocking)")
    s, nocs = spi_open(speed=250_000)
    log(f"  no_cs supported: {nocs}")
    results = {}
    for pull in ("pu", "pd"):
        pc_set(9, "a0", pull)
        time.sleep(0.05)
        # 1. clock with NO CS asserted at all
        cs_high(CS0); cs_high(CS1)
        r_nocs = s.xfer2([0x00] * 12)
        # 2. CS0 asserted, DC(22) high, clock zeros then ones
        pc_set(22, "op", "dh")
        cs_low(CS0)
        r_ce0_z = s.xfer2([0x00] * 12)
        r_ce0_f = s.xfer2([0xFF] * 12)
        cs_high(CS0)
        # 3. CS0 asserted, proper read transaction of RDDID 0x04
        rid = dbi_read(s, 0x04, 4, dc=22)
        # 4. CS1 asserted (touch, manual CS), send 0x90 cmd + clock 2 bytes
        cs_low(CS1)
        r_touch = s.xfer2([0x90, 0x00, 0x00])
        cs_high(CS1)
        results[pull] = (r_nocs, r_ce0_z, r_ce0_f, rid, r_touch)
        log(f"  pull={pull}:")
        log(f"    no-CS clock   : {hexs(r_nocs)}")
        log(f"    CE0 tx=00     : {hexs(r_ce0_z)}")
        log(f"    CE0 tx=FF     : {hexs(r_ce0_f)}")
        log(f"    CE0 RDDID 0x04: {hexs(rid)}")
        log(f"    CE1 touch 0x90: {hexs(r_touch)}")
    s.close()
    pc_set(9, "a0", "pd")
    # verdict
    up, dn = results["pu"], results["pd"]
    def allv(b, v): return all(x == v for x in b)
    floats = allv(up[1], 0xFF) and allv(dn[1], 0x00)
    touch_drives = up[4] != [0xFF]*3 or dn[4] != [0x00]*3
    log(f"  VERDICT: CE0-MISO {'FLOATS (nothing drives it)' if floats else 'IS DRIVEN or partially driven'}; "
        f"touch {'actively drives MISO (control OK)' if touch_drives else 'control FAILED — rig problem!'}")

# ---------- Phase C: pin connectivity scan ----------

SCAN_PINS = [2, 3, 4, 14, 15, 17, 18, 22, 23, 24, 25, 27]

def pin_scan():
    log("\n[C] pin connectivity scan (input + internal pull, ~50k)")
    log("    pin  pd->  pu->  classification")
    for g in SCAN_PINS:
        pc_set(g, "ip", "pd"); time.sleep(0.05)
        v_pd = pc_level(g)
        pc_set(g, "ip", "pu"); time.sleep(0.05)
        v_pu = pc_level(g)
        if v_pd == 0 and v_pu == 1: cls = "floating / high-Z (follows pull)"
        elif v_pd == 1 and v_pu == 1: cls = "EXTERNALLY PULLED/DRIVEN HIGH"
        elif v_pd == 0 and v_pu == 0: cls = "EXTERNALLY PULLED/DRIVEN LOW"
        else: cls = "indeterminate"
        log(f"    GPIO{g:<3} {v_pd}     {v_pu}    {cls}")
        pc_set(g, "ip", "pn")

# ---------- Phase D: protocol-correct register reads ----------

READS = [(0x04, 4, "RDDID"), (0x09, 5, "RDDST"), (0x0A, 2, "RDPWR"),
         (0x0B, 2, "RDMADCTL"), (0x0C, 2, "RDCOLMOD"),
         (0xDA, 2, "ID1"), (0xDB, 2, "ID2"), (0xDC, 2, "ID3")]

def read_sweep():
    log("\n[D] register reads: manual CS held low across cmd+reply")
    for (dc, rst, label) in [(22, 27, "waveshare dc=22 rst=27"),
                             (24, 25, "lcdwiki   dc=24 rst=25")]:
        for mode in (0, 3, 1, 2):
            for speed in (500_000, 4_000_000):
                s, _ = spi_open(speed=speed, mode=mode)
                reset_pulse(rst)
                vals = []
                nonzero = False
                for cmd, n, name in READS:
                    rx = dbi_read(s, cmd, n, dc=dc)
                    vals.append((name, rx))
                    if any(rx): nonzero = True
                s.close()
                flat = "; ".join(f"{n}:{hexs(r)}" for n, r in vals)
                mark = "  <<< NONZERO!" if nonzero else ""
                log(f"  [{label} mode{mode} {speed//1000}kHz] {flat}{mark}")

# ---------- main ----------

if __name__ == "__main__":
    log("=== probe2.py start ===")
    log("[A] switching to spidev")
    to_spidev()
    try:
        miso_drive_test()
        pin_scan()
        read_sweep()
    finally:
        restore_drivers()
    log("=== probe2.py done ===")
