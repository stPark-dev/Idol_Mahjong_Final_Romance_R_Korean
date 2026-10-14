"""Mahjong win result: yaku names and score glyphs on NBG3, redrawn in Korean (D-20 to D-22).

The result panel copies a 16-colour cell bank from MJDAT2.BIN (loaded at 0x00200000) to VDP2
0x50040 through the copy list at 1ST.BIN 0x060174BC (4 x {source, size}); NBG3's character base
is 0x50000 (PNCN3 0x80EA). The yaku table at 0x06016818 has 53 slots of {u16 first unit,
u16 width in cells}; a yaku name is w cells for its top row followed by w cells for its bottom
row (0, 0 = empty slot). The score line is built from 16x16 glyphs of four units (top-left,
top-right, bottom-left, bottom-right) from unit 0x104: 飜 符 点, the digits 0-9 at 0x110, and
the kanji numerals 一-十 at 0x138 used for each yaku's han. Colours: entry 9 of the colour
table 0x0601576C from its 48th colour (CRAM colour 0x730).

Names and glyphs are redrawn in their own cells, so no map changes: 飜/符/点 become 판/부/점,
一-九 the digits 1-9 (copied from the digit glyphs) and 十 a narrow 「10」. A yaku's han picks one
numeral by index from the table at 0x06014794 (一-十), so numerals are never combined.
"""
import json
import struct
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from fontTools.ttLib import TTCollection
from PIL import Image, ImageDraw, ImageFont

FIRST_BASE = 0x06004000
YAKU_TABLE = 0x06016818
SLOTS = 53
BANK_LIST = 0x060174BC
PIECES = 4
BANK_VRAM = 0x50040
CHAR_BASE = 0x50000
LOAD = 0x20200000
COLOUR_TABLE = 0x0601576C
COLOUR_ENTRY, COLOUR_FIRST = 9, 0x30
GLYPHS = {"han": 0x104, "fu": 0x108, "ten": 0x10C}
DIGITS = 0x110                   # 0-9, four units each
KANJI = 0x138                    # 一 二 … 十, four units each
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc"
FONT_INDEX = 1
SUPER = 4
MAX_PX, MIN_PX = 14, 9


class MjYakuError(ValueError):
    pass


@dataclass(frozen=True)
class Layout:
    pieces: tuple                # (VRAM start, MJDAT2 offset, size)
    yaku: dict                   # slot -> (first unit, width)
    colours: dict                # colour index -> 5-bit RGB, the colours names and glyphs use
    bg: int

    @staticmethod
    def read(first: bytes, mj2: bytes) -> "Layout":
        pieces, v = [], BANK_VRAM
        for k in range(PIECES):
            src, size = struct.unpack_from(">II", first, BANK_LIST - FIRST_BASE + 8 * k)
            o = src - LOAD
            if size % 32 or not 0 <= o <= len(mj2) - size:
                raise MjYakuError(f"result cell piece {k}: {src:#x}+{size:#x} outside MJDAT2.BIN")
            pieces.append((v, o, size))
            v += size
        yaku = {}
        for s in range(SLOTS):
            u, w = struct.unpack_from(">HH", first, YAKU_TABLE - FIRST_BASE + 4 * s)
            if (u, w) == (0, 0):
                continue
            if not 1 <= w <= 16:
                raise MjYakuError(f"yaku slot {s}: width {w}")
            yaku[s] = (u, w)
        src, count = struct.unpack_from(">IH", first, COLOUR_TABLE - FIRST_BASE + 8 * COLOUR_ENTRY)
        if count < COLOUR_FIRST + 16 or not 0 <= src - FIRST_BASE <= len(first) - 2 * count:
            raise MjYakuError(f"result colours: entry {COLOUR_ENTRY} at {src:#x} count {count}")
        palette = struct.unpack_from(">16H", first, src - FIRST_BASE + 2 * COLOUR_FIRST)
        lay = Layout(tuple(pieces), yaku, {}, 0)
        used = np.zeros(16, int)        # text colours: the greys of the score glyphs (two names add brown accents)
        for g in list(GLYPHS.values()) + [DIGITS + 4 * k for k in range(10)]:
            used += np.bincount(read_glyph(lay, mj2, g).flatten(), minlength=16)
        colours = {i: (palette[i] & 31, (palette[i] >> 5) & 31, (palette[i] >> 10) & 31) for i in range(16) if used[i]}
        return Layout(tuple(pieces), yaku, colours, int(used.argmax()))

    def glyph(self, name: str) -> int:
        return GLYPHS[name]


def cell_offset(lay: Layout, unit: int) -> int:
    """MJDAT2.BIN offset of an NBG3 cell unit of the result bank."""
    a = CHAR_BASE + unit * 0x20
    for v, o, size in lay.pieces:
        if v <= a and a + 32 <= v + size:
            return o + a - v
    raise MjYakuError(f"result cell unit {unit:#x} is not in the bank")


def _cell(lay: Layout, data: bytes, unit: int) -> np.ndarray:
    o = cell_offset(lay, unit)
    b = np.frombuffer(data[o:o + 32], np.uint8)
    return np.stack([b >> 4, b & 15], 1).reshape(8, 8)


def _put(lay: Layout, out: bytearray, unit: int, img: np.ndarray) -> None:
    o = cell_offset(lay, unit)
    out[o:o + 32] = bytes((img[:, 0::2] << 4 | img[:, 1::2]).astype(np.uint8).flatten())


def _yaku_units(u: int, w: int):
    return [(r, c, u + r * w + c) for r in (0, 1) for c in range(w)]


def read_yaku(lay: Layout, data: bytes, slot: int) -> np.ndarray:
    u, w = lay.yaku[slot]
    img = np.zeros((16, w * 8), np.uint8)
    for r, c, unit in _yaku_units(u, w):
        img[r * 8:r * 8 + 8, c * 8:c * 8 + 8] = _cell(lay, data, unit)
    return img


def _glyph_units(u: int):
    return [(0, 0, u), (0, 1, u + 1), (1, 0, u + 2), (1, 1, u + 3)]


def read_glyph(lay: Layout, data: bytes, u: int) -> np.ndarray:
    img = np.zeros((16, 16), np.uint8)
    for r, c, unit in _glyph_units(u):
        img[r * 8:r * 8 + 8, c * 8:c * 8 + 8] = _cell(lay, data, unit)
    return img


@lru_cache(maxsize=None)
def _font(px: int):
    return ImageFont.truetype(FONT, px * SUPER, index=FONT_INDEX)


@lru_cache(maxsize=None)
def _cmap():
    return frozenset(TTCollection(FONT).fonts[FONT_INDEX].getBestCmap())


def render_text(text: str, w: int, h: int, colours: dict, bg: int, align: str = "centre") -> np.ndarray:
    """h x w image on bg: the text centred (or from x 1 with align "left", as the original yaku
    names), at the largest size from MAX_PX down to MIN_PX that
    leaves a pixel on each side (D-21), each pixel the colour closest in brightness."""
    if any(c != " " and ord(c) not in _cmap() for c in text):
        raise MjYakuError(f"no glyph in {text!r}")
    for px in range(MAX_PX, MIN_PX - 1, -1):
        l, t, r, b = _font(px).getbbox(text)
        if (r - l) / SUPER + 2 <= w and (b - t) / SUPER <= h:
            break
    else:
        raise MjYakuError(f"{text!r} does not fit {w}x{h} even at {MIN_PX}px")
    im = Image.new("L", (w * SUPER, h * SUPER), 0)
    x = SUPER - l if align == "left" else (w * SUPER - (r - l)) / 2 - l
    ImageDraw.Draw(im).text((x, (h * SUPER - (b - t)) / 2 - t), text, font=_font(px), fill=255)
    a = np.asarray(im.resize((w, h), Image.LANCZOS), dtype=np.float32) / 255
    a[a < 0.1] = 0
    lum = {i: sum(c) / 3 for i, c in colours.items()}
    ink = min(lum, key=lum.get)
    want = lum[bg] + a * (lum[ink] - lum[bg])
    idx = np.array(sorted(lum, key=lum.get))
    levels = np.array([lum[i] for i in idx])
    return idx[np.abs(want[..., None] - levels).argmin(-1)].astype(np.uint8)


def editable(lay: Layout, size: int) -> np.ndarray:
    """Mask of the MJDAT2.BIN bytes this module may change: yaku name and score glyph cells."""
    mask = np.zeros(size, bool)
    units = [unit for u, w in lay.yaku.values() for _, _, unit in _yaku_units(u, w)]
    for g in list(GLYPHS.values()) + [KANJI + 4 * k for k in range(10)]:
        units += [unit for _, _, unit in _glyph_units(g)]
    for unit in units:
        mask[cell_offset(lay, unit):cell_offset(lay, unit) + 32] = True
    return mask


def check_protected(lay: Layout, before: bytes, after: bytes) -> None:
    """Only yaku and glyph cells differ, and they use the result panel's text colours."""
    if len(before) != len(after):
        raise MjYakuError("protected: MJDAT2.BIN size changed")
    mask = editable(lay, len(before))
    diff = np.frombuffer(before, np.uint8) != np.frombuffer(after, np.uint8)
    stray = np.flatnonzero(diff & ~mask)
    if len(stray):
        raise MjYakuError(f"protected: MJDAT2.BIN byte {int(stray[0]):#x} changed")
    for s in lay.yaku:
        if not set(np.unique(read_yaku(lay, after, s)).tolist()) <= set(lay.colours):
            raise MjYakuError(f"protected: yaku slot {s} uses a colour outside the panel's text colours")
    for g in list(GLYPHS.values()) + [KANJI + 4 * k for k in range(10)]:
        if not set(np.unique(read_glyph(lay, after, g)).tolist()) <= set(lay.colours):
            raise MjYakuError(f"protected: glyph {g:#x} uses a colour outside the panel's text colours")


def load_yaku(path) -> dict[int, str]:
    """{yaku slot: Korean name} from translation/graphics/mj_yaku.json (ids mj.yaku.NN)."""
    entries = json.loads(Path(path).read_text(encoding="utf-8"))["entries"]
    out = {}
    for e in entries:
        sid = str(e.get("id", ""))
        if not sid.startswith("mj.yaku.") or not sid[8:].isdigit() or not 0 <= int(sid[8:]) < SLOTS:
            raise MjYakuError(f"{path}: bad yaku slot id {sid!r}")
        if not isinstance(e.get("ko"), str) or not e["ko"].strip():
            raise MjYakuError(f"{path}: {sid} needs a Korean name")
        if int(sid[8:]) in out:
            raise MjYakuError(f"{path}: {sid} listed twice")
        out[int(sid[8:])] = e["ko"].strip()
    return out


def load_score(path) -> dict[str, str]:
    """{han, fu, ten: Korean glyph} from translation/graphics/mj_score.json."""
    entries = json.loads(Path(path).read_text(encoding="utf-8"))["entries"]
    ids = [str(e.get("id", "")) for e in entries]
    out = {i[9:]: str(e.get("ko", "")).strip() for i, e in zip(ids, entries) if i.startswith("mj.score.")}
    if len(out) != len(entries) or set(out) != set(GLYPHS) or not all(len(v) == 1 for v in out.values()):
        raise MjYakuError(f"{path}: need one-character han, fu and ten")
    return out


def build_file(first: bytes, mj2: bytes, yaku: dict[int, str], score: dict[str, str]) -> bytes:
    """MJDAT2.BIN with the yaku names and score glyphs redrawn; the protected bytes are checked."""
    lay = Layout.read(first, mj2)
    if set(yaku) != set(lay.yaku):
        raise MjYakuError(f"yaku names for slots {sorted(set(lay.yaku) ^ set(yaku))} missing or extra")
    out = bytearray(mj2)
    for s, text in yaku.items():
        u, w = lay.yaku[s]
        img = render_text(text, w * 8, 16, lay.colours, lay.bg, align="left")
        for r, c, unit in _yaku_units(u, w):
            _put(lay, out, unit, img[r * 8:r * 8 + 8, c * 8:c * 8 + 8])
    glyphs = {GLYPHS[k]: render_text(v, 16, 16, lay.colours, lay.bg) for k, v in score.items()}
    for k in range(9):                                  # 一-九 -> 1-9
        glyphs[KANJI + 4 * k] = read_glyph(lay, mj2, DIGITS + 4 * (k + 1))
    glyphs[KANJI + 4 * 9] = render_text("10", 16, 16, lay.colours, lay.bg)
    for g, img in glyphs.items():
        for r, c, unit in _glyph_units(g):
            _put(lay, out, unit, img[r * 8:r * 8 + 8, c * 8:c * 8 + 8])
    out = bytes(out)
    check_protected(lay, mj2, out)
    return out
