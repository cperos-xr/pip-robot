#!/usr/bin/env python3
"""Clear the PF_X flag on PT_GNU_STACK in an ELF shared object (what `execstack -c` does).
glibc >= 2.41 refuses to dlopen libraries that request an executable stack (e.g. vosk's libvosk.so)."""
import struct, sys
PT_GNU_STACK, PF_X = 0x6474E551, 1
p = sys.argv[1]
b = bytearray(open(p, "rb").read())
assert b[:4] == b"\x7fELF"
is64, le = b[4] == 2, b[5] == 1
e = "<" if le else ">"
if is64:
    phoff, = struct.unpack_from(e + "Q", b, 0x20); phentsize, phnum = struct.unpack_from(e + "HH", b, 0x36); flag_off = 4
else:
    phoff, = struct.unpack_from(e + "I", b, 0x1C); phentsize, phnum = struct.unpack_from(e + "HH", b, 0x2A); flag_off = 24
for i in range(phnum):
    o = phoff + i * phentsize
    ptype, = struct.unpack_from(e + "I", b, o)
    if ptype == PT_GNU_STACK:
        flags, = struct.unpack_from(e + "I", b, o + flag_off)
        print(f"PT_GNU_STACK flags {flags:#x} -> {flags & ~PF_X:#x}")
        struct.pack_into(e + "I", b, o + flag_off, flags & ~PF_X)
open(p, "wb").write(bytes(b))
