#!/usr/bin/env python3
"""PoC: render test subtitle textures, write cues.h, and (after build) patch a Mednafen state.
usage: mkpoc.py gen                      -> cues.h, tex.bin
       mkpoc.py patch <src.mcN> <dst.mcN> -> state with code, literal hook, textures, LUT"""
import gzip, json, struct, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont
HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))
sys.path.insert(0, HERE)
from mcs import find_all

FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc"
TEX_BASE = 0x70000          # VDP1 VRAM offset for textures
LUT_ADDR = 0x6FFE0          # 16-entry colour lookup table
H = 24
CODE_ADDR = 0x06058100
HOOK_LITERAL = 0x0600F5E4
LUT = [0x0000, 0x8000, 0xFFFF] + [0x8000] * 13   # transparent, black outline, white


def render(text):
    f = ImageFont.truetype(FONT, 20, index=1)
    w = int(f.getlength(text)) + 4
    w = (w + 15) // 16 * 16
    im = Image.new("L", (w, H), 0)
    ImageDraw.Draw(im).text((2, 20), text, font=f, fill=255, anchor="ls")
    a = np.array(im) >= 110
    o = np.zeros_like(a)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            o |= np.roll(np.roll(a, dy, 0), dx, 1)
    px = np.where(a, 2, np.where(o, 1, 0)).astype(np.uint8)
    return px


def gen():
    es = [e for e in json.load(open(f"{HERE}/../../translation/voice/ri.json"))["entries"] if e["movie"] == "RI01.CPK"]
    tex = bytearray(); rows = []
    for n, e in enumerate(es, 1):
        px = render(f"[시험] 리나 {n}번 줄 {e['start']:.1f}–{e['end']:.1f}초")
        h, w = px.shape
        addr = TEX_BASE + len(tex)
        tex += bytes((px[:, 0::2] << 4 | px[:, 1::2]).flatten())
        while len(tex) % 32: tex.append(0)
        rows.append(f"    {{{int(e['start'] * 600)}, {int(e['end'] * 600)}, 0x{addr // 8:04x}, 0x{(w // 8) << 8 | h:04x}, {-w // 2}, {111 - h - 4}}},")
    open(f"{HERE}/cues.h", "w").write(
        f"#define LUT_ADDR 0x{LUT_ADDR:x}\n#define Z_POS 0x{int(100 * 65536):x}\n#define NCUES {len(rows)}\n"
        "static const struct cue cues[NCUES] = {\n" + "\n".join(rows) + "\n};\n")
    open(f"{HERE}/tex.bin", "wb").write(tex)
    print(len(rows), "cues, tex", len(tex), "bytes")


def put16(region, off, data):
    """region is a LE16 Mednafen memory image; data is big-endian bytes"""
    for i in range(0, len(data), 2):
        region[off + i] = data[i + 1]; region[off + i + 1] = data[i]


def patch(src, dst):
    d = bytearray(gzip.open(src).read())
    wh = [(o, n) for _, n, o in find_all(d, "WorkRAMH")][0]
    v1 = [(o, n) for _, n, o in find_all(d, "VRAM") if n == 524288][0]
    code = open(f"{HERE}/poc.bin", "rb").read()
    code += b"\0" * (len(code) & 1)
    mv = memoryview(d)
    put16(mv[wh[0]:wh[0] + wh[1]], CODE_ADDR - 0x06000000, code)
    put16(mv[wh[0]:wh[0] + wh[1]], HOOK_LITERAL - 0x06000000, struct.pack(">I", CODE_ADDR))
    tex = open(f"{HERE}/tex.bin", "rb").read()
    put16(mv[v1[0]:v1[0] + v1[1]], TEX_BASE, tex)
    put16(mv[v1[0]:v1[0] + v1[1]], LUT_ADDR, struct.pack(">16H", *LUT))
    gzip.open(dst, "wb").write(bytes(d))
    print("patched", dst, "code", len(code), "bytes")


if __name__ == "__main__":
    gen() if sys.argv[1] == "gen" else patch(sys.argv[2], sys.argv[3])
