"""Dress-up (kisekae) screen buttons: 終了 拡大 縮小 送る 戻る クリア redrawn in Korean.

KISEDAT.BIN is loaded verbatim at 0x06060000 for this screen. NBG3 (16-colour cells,
character base VDP2 0x50000) takes its cells from the copy list inside KISEDAT.BIN at
0x06060C08: pieces 2-4 ({source, size}) are written one after another from cell unit 2, so the
button cells sit at units 2-0x53, 0x54-0x89 and 0x8A-0xAF. The buttons are 7x3 byte maps of
the map table 0x06015124 (cell unit = byte + add): 118/119 終了, 120/121 拡大, 122/123 縮小,
124 送る, 126/127 戻る, 128/129 クリア (normal, pressed). Entry 125 (送る pressed) starts one
byte off in the original and draws garbage there; it is left as it is.

Each normal button keeps its frame; its white label (colour 1, with a colour-3 edge on クリア) is
painted over with the face colour (7) and the Korean label drawn in its place, in colour 1 alone. Cells are reallocated among the units only
the button labels use (105); cells equal to a kept cell reuse it. A map byte reaches only units
add..add+255, and the pressed 送る 戻る クリア buttons would need 49 new cells among the 46 units from
0x54, so each pressed map is pointed at its normal button (D-25). The screen's other maps are not
NBG3: 116 is drawn to NBG0, 117 to NBG2 (character base 0x60000), and on the running screen NBG3
shows no cell but 0 outside the buttons (130-133 are not drawn there).
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
KISE_BASE = 0x06060000
MAP_TABLE = 0x06015124
PIECE_LIST = 0x06060C08
BUTTON_PIECES = (2, 3, 4)
FIRST_UNIT = 2
LABELS = {118: "end", 119: "end", 120: "zoom", 121: "zoom", 122: "unzoom", 123: "unzoom", 124: "next",
          126: "back", 127: "back", 128: "clear", 129: "clear"}
PRESSED = {119: 118, 121: 120, 123: 122, 127: 126, 129: 128}   # pressed map -> its normal map
NORMAL = (118, 120, 122, 124, 126, 128)
KEPT_MAPS = (125,)   # 116 is NBG0, 117 NBG2; on the screen NBG3 shows no cell but 0 outside the buttons
IDS = ("end", "zoom", "unzoom", "next", "back", "clear")
FACE, TEXT, EDGE = 7, 1, 3
COLOURS = tuple(range(16))
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
FONT_INDEX = 1
FONT_PX = 12
SUPER = 4


class KiseError(ValueError):
    pass


@dataclass(frozen=True)
class Map:
    off: int                     # KISEDAT.BIN offset of the byte map
    w: int
    h: int
    stride: int
    add: int


@dataclass(frozen=True)
class Layout:
    maps: dict                   # entry -> Map (the eleven button maps)
    kept: dict                   # entry -> Map (maps left alone)
    pieces: tuple                # (first unit, KISEDAT offset, size)

    @staticmethod
    def read(first: bytes, kd: bytes) -> "Layout":
        pieces, unit = [], FIRST_UNIT
        for k in BUTTON_PIECES:
            src, size = struct.unpack_from(">II", kd, PIECE_LIST - KISE_BASE + 8 * k)
            o = src - KISE_BASE
            if size % 32 or not 0 <= o <= len(kd) - size:
                raise KiseError(f"kisekae cell piece {k}: {src:#x}+{size:#x}")
            pieces.append((unit, o, size))
            unit += size // 32
        maps, kept = {}, {}
        for i in list(LABELS) + list(KEPT_MAPS):
            w, h, add, src, stride = struct.unpack_from(">BBHIH", first, MAP_TABLE - FIRST_BASE + 12 * i)
            o = src - KISE_BASE
            if not 0 <= o <= len(kd) - stride * h:
                raise KiseError(f"kisekae map {i} at {src:#x} is outside KISEDAT.BIN")
            if i in LABELS and (w, h, stride) != (7, 3, 7):
                raise KiseError(f"button map {i} is {w}x{h}/{stride}")
            (maps if i in LABELS else kept)[i] = Map(o, w, h, stride, add)
        return Layout(maps, kept, tuple(pieces))

    def label(self, entry: int) -> str:
        return LABELS[entry]

    def map_off(self, entry: int) -> int:
        return (self.maps.get(entry) or self.kept[entry]).off


def cell_offset(lay: Layout, unit: int) -> int:
    for u0, o, size in lay.pieces:
        if u0 <= unit < u0 + size // 32:
            return o + (unit - u0) * 32
    raise KiseError(f"cell unit {unit:#x} is not in the button cell pieces")


def _units(kd: bytes, m: Map) -> list[int]:
    return [kd[m.off + y * m.stride + x] + m.add for y in range(m.h) for x in range(m.w)]


def _cell(lay: Layout, kd: bytes, unit: int) -> np.ndarray:
    o = cell_offset(lay, unit)
    b = np.frombuffer(kd[o:o + 32], np.uint8)
    return np.stack([b >> 4, b & 15], 1).reshape(8, 8)


def _encode(cell: np.ndarray) -> bytes:
    return bytes((cell[:, 0::2] << 4 | cell[:, 1::2]).astype(np.uint8).flatten())


def decode_map(lay: Layout, kd: bytes, entry: int) -> np.ndarray:
    m = lay.maps[entry]
    img = np.zeros((m.h * 8, m.w * 8), np.uint8)
    for k, u in enumerate(_units(kd, m)):
        y, x = divmod(k, m.w)
        img[y * 8:y * 8 + 8, x * 8:x * 8 + 8] = _cell(lay, kd, u)
    return img


@lru_cache(maxsize=None)
def _font():
    return ImageFont.truetype(FONT, FONT_PX * SUPER, index=FONT_INDEX), frozenset(TTCollection(FONT).fonts[FONT_INDEX].getBestCmap())


def label_mask(img: np.ndarray) -> np.ndarray:
    """The label's pixels: colour TEXT and the EDGE pixels touching it (the frame's own EDGE stays)."""
    text = img == TEXT
    p, (h, w) = np.pad(text, 1), text.shape
    near = np.zeros_like(text)
    for dy in range(3):
        for dx in range(3):
            near |= p[dy:dy + h, dx:dx + w]
    return text | ((img == EDGE) & near)


def draw_label(img: np.ndarray, text: str, centre: tuple) -> np.ndarray:
    """img with text drawn in TEXT, centred on (x, y), one colour without anti-aliasing."""
    font, cmap = _font()
    if any(c != " " and ord(c) not in cmap for c in text):
        raise KiseError(f"no glyph in {text!r}")
    l, t, r, b = font.getbbox(text)
    w, h = (r - l) / SUPER, (b - t) / SUPER
    if w > 38 or h > 14:
        raise KiseError(f"label {text!r} ({w:.0f}x{h:.0f}) does not fit the button face")
    H, W = img.shape
    im = Image.new("L", (W * SUPER, H * SUPER), 0)
    cx, cy = centre
    ImageDraw.Draw(im).text((cx * SUPER - (r - l) / 2 - l, cy * SUPER - (b - t) / 2 - t), text, font=font, fill=255)
    ink = np.asarray(im.resize((W, H), Image.LANCZOS)) > 110
    out = img.copy()
    out[ink] = TEXT
    return out


def _shared_units(lay: Layout, kd: bytes) -> tuple[set, set]:
    """(units only button labels use, units anything else shows)."""
    label_units, shown = set(), set()
    for i, m in lay.maps.items():
        for k, u in enumerate(_units(kd, m)):
            (label_units if (_cell(lay, kd, u) == TEXT).any() else shown).add(u)
    for m in lay.kept.values():
        shown |= {u for u in _units(kd, m)}
    return label_units - shown, shown


def editable(lay: Layout, kd: bytes) -> np.ndarray:
    """Mask of the KISEDAT.BIN bytes this module may change: button map bytes and label cells."""
    mask = np.zeros(len(kd), bool)
    for m in lay.maps.values():
        for y in range(m.h):
            mask[m.off + y * m.stride:m.off + y * m.stride + m.w] = True
    for u in _shared_units(lay, kd)[0]:
        o = cell_offset(lay, u)
        mask[o:o + 32] = True
    for m in lay.kept.values():                  # kept maps' bytes and the cells they show stay
        for y in range(m.h):
            mask[m.off + y * m.stride:m.off + y * m.stride + m.w] = False
    return mask


def check_protected(lay: Layout, before: bytes, after: bytes) -> None:
    """Only button map bytes and cells only button labels use differ."""
    if len(before) != len(after):
        raise KiseError("protected: KISEDAT.BIN size changed")
    diff = np.frombuffer(before, np.uint8) != np.frombuffer(after, np.uint8)
    stray = np.flatnonzero(diff & ~editable(lay, before))
    if len(stray):
        raise KiseError(f"protected: KISEDAT.BIN byte {int(stray[0]):#x} changed")


def load_texts(path) -> dict[str, str]:
    entries = json.loads(Path(path).read_text(encoding="utf-8"))["entries"]
    if [e.get("id") for e in entries] != [f"kise.{i}" for i in IDS]:
        raise KiseError(f"{path}: entry ids must be {['kise.' + i for i in IDS]} in that order")
    if not all(isinstance(e.get("ko"), str) and e["ko"].strip() for e in entries):
        raise KiseError(f"{path}: every button needs a Korean label")
    return {i: e["ko"].strip() for i, e in zip(IDS, entries)}


def build_file(first: bytes, kd: bytes, texts: dict[str, str]) -> bytes:
    """KISEDAT.BIN with the button labels redrawn; the protected bytes are checked."""
    lay = Layout.read(first, kd)
    pool_all, shown = _shared_units(lay, kd)
    known = {}                                   # cell content -> a unit that keeps showing it
    for u in sorted(shown):
        if any(u0 <= u < u0 + sz // 32 for u0, _, sz in lay.pieces):
            known.setdefault(_encode(_cell(lay, kd, u)), u)
    out = bytearray(kd)
    taken: dict[bytes, int] = {}
    free = sorted(pool_all)
    for i in NORMAL:
        m = lay.maps[i]
        img = decode_map(lay, kd, i)
        ys, xs = np.nonzero(img == TEXT)
        centre = ((xs.min() + xs.max() + 1) / 2, (ys.min() + ys.max() + 1) / 2)
        face = img.copy()
        face[label_mask(img)] = FACE
        new = draw_label(face, texts[LABELS[i]], centre)
        for k in range(m.w * m.h):
            y, x = divmod(k, m.w)
            cell = _encode(new[y * 8:y * 8 + 8, x * 8:x * 8 + 8])
            if cell in known and m.add <= known[cell] <= m.add + 255:
                u = known[cell]
            elif cell in taken and m.add <= taken[cell] <= m.add + 255:
                u = taken[cell]
            else:
                cand = [u for u in free if m.add <= u <= m.add + 255]
                if not cand:
                    raise KiseError(f"button map {i} needs more than {len(pool_all)} label cells")
                u = cand[0]
                free.remove(u)
                taken[cell] = u
                o = cell_offset(lay, u)
                out[o:o + 32] = cell
            out[m.off + y * m.stride + x] = u - m.add
    for pressed, normal in PRESSED.items():
        p, n = lay.maps[pressed], lay.maps[normal]
        if p.add != n.add:
            raise KiseError(f"pressed map {pressed} and {normal} use different cell bases")
        for y in range(3):
            out[p.off + y * p.stride:p.off + y * p.stride + 7] = out[n.off + y * n.stride:n.off + y * n.stride + 7]
    out = bytes(out)
    check_protected(lay, kd, out)
    return out
