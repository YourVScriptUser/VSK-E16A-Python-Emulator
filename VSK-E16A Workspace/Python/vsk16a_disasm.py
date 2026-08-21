#!/usr/bin/env python3
"""
VSK-16A Disassembler
--------------------
Disassembles big-endian (default) or little-endian 16-bit word images
produced by the VSK-16A assembler / emulator.

Output is valid Xasm16A source that can be fed back into the assembler.

Usage examples:
  python vsk16a_disasm.py BIOS.rom -o bios.xasm16A
  python vsk16a_disasm.py old.rom --le -o old.asm          # little-endian
  python vsk16a_disasm.py disk.vhd -s 0 -e 0x1000 -o part.asm
  python vsk16a_disasm.py BIOS.rom --verbose               # addr + raw hex
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------------
# Mode constants (must match the assembler)
# ---------------------------------------------------------------------------
MODE_REGISTER        = 1
MODE_IMMEDIATE       = 2
MODE_MEMORY_REGISTER = 3
MODE_MEMORY_ADDRESS  = 4

MOVFS_MAP = {
    44: "rFLAGS",
    99: "rIP",
    22: "rSP",
}


def _fmt_reg(r: int) -> str:
    return f"r{r}"


def _fmt_addr(a: int) -> str:
    return f"0x{a:04X}"


def _fmt_imm(v: int) -> str:
    return f"0x{v:04X}"


# ---------------------------------------------------------------------------
# Decoders
# ---------------------------------------------------------------------------

def decode_mov(words: List[int], ip: int) -> Tuple[str, int]:
    if ip + 4 >= len(words):
        return "MOV  ; truncated", 1
    mode_a = words[ip + 1]
    mode_b = words[ip + 2]
    dest   = words[ip + 3]
    src    = words[ip + 4]

    def operand(mode: int, val: int) -> str:
        if mode == MODE_REGISTER:
            return _fmt_reg(val)
        if mode == MODE_IMMEDIATE:
            return _fmt_imm(val)
        if mode == MODE_MEMORY_REGISTER:
            return f"[{_fmt_reg(val)}]"
        if mode == MODE_MEMORY_ADDRESS:
            return f"[{_fmt_addr(val)}]"
        return _fmt_imm(val)

    return f"MOV {operand(mode_a, dest)}, {operand(mode_b, src)}", 5


def decode_math(op: str, words: List[int], ip: int) -> Tuple[str, int]:
    if ip + 3 >= len(words):
        return f"{op}  ; truncated", 1
    ra, rb, dest = words[ip + 1], words[ip + 2], words[ip + 3]
    return f"{op} {_fmt_reg(ra)}, {_fmt_reg(rb)}, {_fmt_reg(dest)}", 4


def decode_jmp_like(mnem: str, words: List[int], ip: int) -> Tuple[str, int]:
    if ip + 1 >= len(words):
        return f"{mnem}  ; truncated", 1
    return f"{mnem} {_fmt_addr(words[ip + 1])}", 2


def decode_reg1(mnem: str, words: List[int], ip: int) -> Tuple[str, int]:
    if ip + 1 >= len(words):
        return f"{mnem}  ; truncated", 1
    return f"{mnem} {_fmt_reg(words[ip + 1])}", 2


def decode_cmp(words: List[int], ip: int) -> Tuple[str, int]:
    if ip + 2 >= len(words):
        return "CMP  ; truncated", 1
    return f"CMP {_fmt_reg(words[ip + 1])}, {_fmt_reg(words[ip + 2])}", 3


def decode_load(words: List[int], ip: int) -> Tuple[str, int]:
    if ip + 1 >= len(words):
        return "LOAD ; truncated", 1
    reg = words[ip + 1]
    chars: List[str] = []
    offset = 2
    while ip + offset < len(words):
        ch = words[ip + offset]
        if ch == 0:
            offset += 1
            break
        if 32 <= ch <= 126 and ch not in (34, 92):
            chars.append(chr(ch))
        else:
            chars.append(f"\\x{ch:02X}")
        offset += 1
    return f'LOAD {_fmt_reg(reg)}, "{"".join(chars)}"', offset


def decode_nump(words: List[int], ip: int) -> Tuple[str, int]:
    if ip + 2 >= len(words):
        return "NUMP ; truncated", 1
    return f"NUMP {_fmt_reg(words[ip + 1])}, {_fmt_reg(words[ip + 2])}", 3


def decode_int(words: List[int], ip: int) -> Tuple[str, int]:
    if ip + 1 >= len(words):
        return "INT  ; truncated", 1
    return f"INT {_fmt_addr(words[ip + 1])}", 2


def decode_movfs(words: List[int], ip: int) -> Tuple[str, int]:
    if ip + 2 >= len(words):
        return "MOVFS ; truncated", 1
    dest = words[ip + 1]
    src  = words[ip + 2]
    src_name = MOVFS_MAP.get(src, f"0x{src:02X}")
    return f"MOVFS {_fmt_reg(dest)}, {src_name}", 3


def decode_jptr(words: List[int], ip: int) -> Tuple[str, int]:
    if ip + 1 >= len(words):
        return "JPTR ; truncated", 1
    return f"JPTR {_fmt_reg(words[ip + 1])}", 2


def decode_movsp(words: List[int], ip: int) -> Tuple[str, int]:
    if ip + 1 >= len(words):
        return "MOVSP ; truncated", 1
    return f"MOVSP {_fmt_reg(words[ip + 1])}", 2


OPCODES = {
    1:  ("HLT", 1),
    4:  (decode_mov, 0),
    8:  ("VOUT", 1),
    12: (lambda w, i: decode_math("ADD", w, i), 0),
    16: (lambda w, i: decode_math("SUB", w, i), 0),
    20: (lambda w, i: decode_math("MUL", w, i), 0),
    24: (lambda w, i: decode_math("DIV", w, i), 0),
    28: (lambda w, i: decode_jmp_like("JMP", w, i), 0),
    32: (lambda w, i: decode_jmp_like("CALL", w, i), 0),
    36: ("RET", 1),
    40: (lambda w, i: decode_reg1("PUSH", w, i), 0),
    44: (lambda w, i: decode_reg1("POP", w, i), 0),
    48: (decode_cmp, 0),
    52: (lambda w, i: decode_jmp_like("JE", w, i), 0),
    56: (lambda w, i: decode_jmp_like("JNE", w, i), 0),
    60: ("KEY", 1),
    64: (decode_load, 0),
    68: (lambda w, i: decode_reg1("INC", w, i), 0),
    72: (lambda w, i: decode_reg1("DEC", w, i), 0),
    76: (decode_nump, 0),
    78: ("DREAD", 1),
    80: ("DWRITE", 1),
    82: (decode_int, 0),
    84: (lambda w, i: decode_jmp_like("JH", w, i), 0),
    86: (lambda w, i: decode_jmp_like("JL", w, i), 0),
    88: ("CLF", 1),
    90: (decode_jptr, 0),
    92: (decode_movfs, 0),
    94: (decode_movsp, 0),
}


def load_image(path: Path, little_endian: bool = False) -> List[int]:
    data = path.read_bytes()
    if len(data) % 2 != 0:
        data += b"\x00"
    endian = "little" if little_endian else "big"
    return [
        int.from_bytes(data[i : i + 2], endian)
        for i in range(0, len(data), 2)
    ]


def find_last_nonzero(words: List[int]) -> int:
    """Index after the last non-zero word."""
    for i in range(len(words) - 1, -1, -1):
        if words[i] != 0:
            return i + 1
    return 0


def disassemble(
    words: List[int],
    start: int = 0,
    end: Optional[int] = None,
    verbose: bool = False,
) -> str:
    """Skip zero words entirely. Only emit real instructions / non-zero data."""
    if end is None:
        end = len(words)
    end = min(end, len(words))
    start = max(0, start)

    lines: List[str] = []
    ip = start

    while ip < end:
        if words[ip] == 0:
            ip += 1
            continue

        opcode = words[ip]
        entry = OPCODES.get(opcode)

        if entry is None:
            text = f"%DEFINEWORD 0x{opcode:04X}"
            size = 1
            raw = f"{opcode:04X}"
        else:
            handler, fixed = entry
            if callable(handler):
                text, size = handler(words, ip)
            else:
                text = handler
                size = fixed
            raw = " ".join(
                f"{words[ip + k]:04X}"
                for k in range(size)
                if ip + k < len(words)
            )

        if verbose:
            lines.append(f"{ip:04X}:  {raw:<28}  {text}")
        else:
            lines.append(text)

        ip += size

    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="VSK-16A disassembler – produces re-assemblable Xasm16A source"
    )
    ap.add_argument("image", type=Path, help="Binary image (.rom / .vhd / .img …)")
    ap.add_argument("-s", "--start", default="0", help="Start address (default 0)")
    ap.add_argument(
        "-e", "--end", default=None,
        help="End address (exclusive). Default = last non-zero word",
    )
    ap.add_argument("-o", "--output", type=Path, help="Write clean source to this file")
    ap.add_argument(
        "--le", "--little-endian", action="store_true",
        help="Interpret image as little-endian 16-bit words",
    )
    ap.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show address + raw hex (console only)",
    )
    ap.add_argument(
        "--full", action="store_true",
        help="Disassemble the entire image (no auto-trim)",
    )
    args = ap.parse_args()

    def parse_addr(s: str) -> int:
        return int(s, 0)

    start = parse_addr(args.start)

    try:
        words = load_image(args.image, little_endian=args.le)
    except Exception as exc:
        print(f"Failed to load image: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.full:
        end = len(words)
    elif args.end is not None:
        end = parse_addr(args.end)
    else:
        end = find_last_nonzero(words)
        if end == 0:
            print("Image is entirely zero – nothing to disassemble.", file=sys.stderr)
            sys.exit(0)
        end = min(len(words), end + 4)

    text = disassemble(words, start=start, end=end, verbose=False)

    endian_note = "little-endian" if args.le else "big-endian"
    header = (
        f"; VSK-16A disassembly of {args.image.name}\n"
        f"; endian: {endian_note}\n"
        f"; words in file: {len(words)} (0x{len(words):04X})\n"
        f"; disassembled range: 0x{start:04X} .. 0x{end:04X}\n"
        f"; Zero words are skipped.\n"
        f";\n"
    )
    full = header + text + "\n"

    if args.output:
        args.output.write_text(full, encoding="utf-8")
        print(f"Wrote re-assemblable source → {args.output}")
        print(f"  range 0x{start:04X}..0x{end:04X}  ({end - start} words)")
        if args.verbose:
            print()
            print(disassemble(words, start=start, end=end, verbose=True))
    else:
        if args.verbose:
            print(header, end="")
            print(disassemble(words, start=start, end=end, verbose=True))
        else:
            print(full, end="")


if __name__ == "__main__":
    main()