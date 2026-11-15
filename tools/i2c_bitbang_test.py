#!/usr/bin/env python3
"""Hardware-vs-driver I2C test. Unbinds the kernel TWI0 driver, bit-bangs PA11(SCL)/PA12(SDA)
via libgpiod open-drain at a few kHz, reports ACK rates, then rebinds the driver."""
import gpiod, time, sys
from gpiod.line import Direction, Drive, Value, Bias
DRV = "/sys/bus/platform/drivers/mv64xxx_i2c"
SCL, SDA = 11, 12
H = 0.0001
open(DRV + "/unbind", "w").write("1c2ac00.i2c")
try:
    req = gpiod.request_lines("/dev/gpiochip0", consumer="i2c-bitbang", config={
        (SCL, SDA): gpiod.LineSettings(direction=Direction.OUTPUT, drive=Drive.OPEN_DRAIN, output_value=Value.ACTIVE)})
    def s(line, v): req.set_value(line, Value.ACTIVE if v else Value.INACTIVE); time.sleep(H)
    def g(line): return 1 if req.get_value(line) == Value.ACTIVE else 0
    def start(): s(SDA, 1); s(SCL, 1); s(SDA, 0); s(SCL, 0)
    def stop(): s(SDA, 0); s(SCL, 1); s(SDA, 1)
    def wbyte(b):
        for i in range(8): s(SDA, (b >> (7 - i)) & 1); s(SCL, 1); s(SCL, 0)
        s(SDA, 1); s(SCL, 1); ack = g(SDA) == 0; s(SCL, 0); return ack
    def rbyte(ack):
        s(SDA, 1); v = 0
        for i in range(8): s(SCL, 1); v = (v << 1) | g(SDA); s(SCL, 0)
        s(SDA, 0 if ack else 1); s(SCL, 1); s(SCL, 0); s(SDA, 1); return v
    print("idle SDA=%d SCL=%d" % (g(SDA), g(SCL)))
    # 9 clocks bus recovery
    for i in range(9): s(SCL, 1); s(SCL, 0)
    stop()
    stats = {}
    def rec(k, ok): stats.setdefault(k, [0, 0])[0 if ok else 1] += 1
    vals = set()
    for n in range(50):
        start(); a = wbyte(0x20 << 1 | 1); v = rbyte(False) if a else None; stop(); rec("pcf_read", a); vals.add(v)
        start(); a1 = wbyte(0x68 << 1); a2 = a1 and wbyte(0x75); start(); a3 = wbyte(0x68 << 1 | 1)
        w = rbyte(False) if a3 else None; stop(); rec("mpu_whoami", a1 and a2 and a3 and w == 0x68)
        start(); a = wbyte(0x68 << 1); a = a and wbyte(0x6B) and wbyte(0x00); stop(); rec("mpu_write", a)
        start(); a = wbyte(0x50 << 1); stop(); rec("absent_0x50_nack", not a)
    print("ok/fail:", stats, "pcf values:", [hex(x) for x in vals if x is not None])
    # quick accel read
    start(); wbyte(0x68 << 1); wbyte(0x3B); start(); wbyte(0x68 << 1 | 1)
    d = [rbyte(i < 5) for i in range(6)]; stop()
    import struct; ax, ay, az = struct.unpack(">3h", bytes(d)); print("accel g: %.2f %.2f %.2f" % (ax / 16384, ay / 16384, az / 16384))
    req.release()
finally:
    time.sleep(0.05)
    open(DRV + "/bind", "w").write("1c2ac00.i2c")
    print("driver rebound")
