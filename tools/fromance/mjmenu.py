"""Mahjong call window: 「ポン カン チー / ロン リーチ」 redrawn as 퐁 깡 치 / 론 리치 (D-18).

The window is one 16-colour VDP1 sprite in SL.BIN (loaded verbatim at 0x06060000): record 1
of the sprite table at the start of SL.BIN ({u16 id, u16 mode, u32 source}); its size comes from
the SGL texture table at 1ST.BIN 0x06015AF4 ({u16 w, u16 h, u16 cg, u16 hv} per id, the table
0x0600FC50 reads). Each option has its own pair of colours, a face and a shadow one pixel to
the lower right, so the game can light the options that are available by palette alone; the
background inside the frame is colour 15. Only the inside of the frame changes.
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
SL_BASE = 0x06060000
SPRITE_TABLE = 0x06060000
TEX_TABLE = 0x06015AF4
RECORD = 1
MENU_ID, MENU_MODE = 1, 3
INTERIOR = (4, 4, 100, 44)       # x0, y0, x1, y1 of the background inside the frame
BG = 15
OPTIONS = ((3, 4), (5, 6), (7, 8), (1, 2), (9, 10))   # (face, shadow): ポン カン チー ロン リーチ
IDS = ("mj.call.pon", "mj.call.kan", "mj.call.chi", "mj.call.ron", "mj.call.riichi")
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
FONT_INDEX = 1
FONT_PX = 14
SUPER = 4
LABEL_H = 16


class MjMenuError(ValueError):
    pass


@dataclass(frozen=True)
class Option:
    face: int
    shadow: int
    x: int                       # left edge and top row of the original label's box
    y: int
    width: int                   # room up to the next label or the frame


@dataclass(frozen=True)
class Layout:
    off: int                     # SL.BIN offset of the window picture
    w: int
    h: int
    options: tuple

    @staticmethod
    def read(first: bytes, sl: bytes) -> "Layout":
        """Window position and the place of each original label."""
        sid, mode, src = struct.unpack_from(">HHI", sl, SPRITE_TABLE - SL_BASE + 8 * RECORD)
        if (sid, mode) != (MENU_ID, MENU_MODE):
            raise MjMenuError(f"sprite record {RECORD} is id {sid:#x} mode {mode}, expected the call window")
        w, h = struct.unpack_from(">HH", first, TEX_TABLE - FIRST_BASE + 8 * sid)
        off = src - SL_BASE
        if (w, h) != (104, 48) or not 0 <= off <= len(sl) - w * h // 2:
            raise MjMenuError(f"call window {w}x{h} at {src:#x}")
        img = _decode(sl[off:off + w * h // 2], w, h)
        x0, y0, x1, y1 = INTERIOR
        boxes = []
        for face, shadow in OPTIONS:
            ys, xs = np.nonzero(np.isin(img, (face, shadow)))
            if not len(xs) or xs.min() < x0 or xs.max() >= x1 or ys.min() < y0 or ys.max() >= y1:
                raise MjMenuError(f"option colours {face}/{shadow} are not inside the window")
            boxes.append((int(xs.min()), int(ys.min()) - 1))
        options = []
        for k, ((face, shadow), (x, y)) in enumerate(zip(OPTIONS, boxes)):
            right = min([bx for bx, by in boxes if abs(by - y) <= 3 and bx > x] + [x1])
            options.append(Option(face, shadow, x, y, right - x - 2))
        return Layout(off, w, h, tuple(options))


def _decode(b: bytes, w: int, h: int) -> np.ndarray:
    a = np.frombuffer(b, np.uint8)
    return np.stack([a >> 4, a & 15], 1).reshape(h, w)


def _encode(img: np.ndarray) -> bytes:
    return bytes((img[:, 0::2] << 4 | img[:, 1::2]).astype(np.uint8).flatten())


def read_image(sl: bytes, lay: Layout) -> np.ndarray:
    """The window as a h x w colour index image."""
    return _decode(sl[lay.off:lay.off + lay.w * lay.h // 2], lay.w, lay.h)


@lru_cache(maxsize=None)
def _font():
    return (ImageFont.truetype(FONT, FONT_PX * SUPER, index=FONT_INDEX),
            frozenset(TTCollection(FONT).fonts[FONT_INDEX].getBestCmap()))


def render_label(text: str, width: int, height: int, face: int, shadow: int, bg: int) -> np.ndarray:
    """height x width image: the text in the face colour from x 0, its shadow one pixel to the
    lower right in the shadow colour, on bg. Two colours only, so no anti-aliasing."""
    font, cmap = _font()
    if any(c != " " and ord(c) not in cmap for c in text):
        raise MjMenuError(f"no glyph in {text!r}")
    need = int(np.ceil(max(font.getlength(text), font.getbbox(text)[2]) / SUPER)) + 1
    if need > width:
        raise MjMenuError(f"{text!r} is {need} px wide, the label has {width}")
    im = Image.new("L", (width * SUPER, height * SUPER), 0)
    ImageDraw.Draw(im).text((0, 13 * SUPER), text, font=font, fill=255, anchor="ls")
    ink = np.asarray(im.resize((width, height), Image.LANCZOS)) > 110
    out = np.full((height, width), bg, np.uint8)
    drop = np.zeros_like(ink)
    drop[1:, 1:] = ink[:-1, :-1]
    out[drop & ~ink] = shadow
    out[ink] = face
    return out


def editable(lay: Layout, sl_size: int) -> np.ndarray:
    """Mask of the SL.BIN bytes this module may change (the inside of the frame)."""
    x0, y0, x1, y1 = INTERIOR
    mask = np.zeros(sl_size, bool)
    for y in range(y0, y1):
        o = lay.off + (y * lay.w + x0) // 2
        mask[o:o + (x1 - x0) // 2] = True
    return mask


def kept(lay: Layout, sl_size: int) -> np.ndarray:
    """Mask of the SL.BIN bytes the window relies on: its sprite record and its frame."""
    mask = np.zeros(sl_size, bool)
    o = SPRITE_TABLE - SL_BASE + 8 * RECORD
    mask[o:o + 8] = True
    mask[lay.off:lay.off + lay.w * lay.h // 2] = True
    return mask & ~editable(lay, sl_size)


def check_protected(lay: Layout, before: bytes, after: bytes) -> None:
    """Only the inside of the frame differs, and it holds the background and option colours."""
    if len(before) != len(after):
        raise MjMenuError("protected: SL.BIN size changed")
    diff = np.frombuffer(before, np.uint8) != np.frombuffer(after, np.uint8)
    stray = np.flatnonzero(diff & ~editable(lay, len(before)))
    if len(stray):
        raise MjMenuError(f"protected: SL.BIN byte {int(stray[0]):#x} changed")
    x0, y0, x1, y1 = INTERIOR
    inside = read_image(after, lay)[y0:y1, x0:x1]
    allowed = (BG,) + tuple(c for o in lay.options for c in (o.face, o.shadow))
    if not np.isin(inside, allowed).all():
        raise MjMenuError("protected: call window holds a colour that is not an option colour")


def load_texts(path) -> list[str]:
    """Korean labels in IDS order from translation/graphics/mj_call.json."""
    entries = json.loads(Path(path).read_text(encoding="utf-8"))["entries"]
    if [e.get("id") for e in entries] != list(IDS):
        raise MjMenuError(f"{path}: entry ids must be {list(IDS)} in that order")
    texts = [e["ko"].strip() for e in entries]
    if not all(texts):
        raise MjMenuError(f"{path}: empty label")
    return texts


def build_file(first: bytes, sl: bytes, texts: list[str], base_sl: bytes = None) -> bytes:
    """SL.BIN with the call window labels redrawn; the protected bytes are checked. The
    layout is read from base_sl (the original SL.BIN) when given."""
    lay = Layout.read(first, sl if base_sl is None else base_sl)
    img = read_image(sl, lay).copy()
    x0, y0, x1, y1 = INTERIOR
    img[y0:y1, x0:x1] = BG
    for o, text in zip(lay.options, texts):
        label = render_label(text, o.width, LABEL_H, o.face, o.shadow, BG)
        box = img[o.y:o.y + LABEL_H, o.x:o.x + o.width]
        keep = label != BG
        box[keep] = label[keep]
    out = sl[:lay.off] + _encode(img) + sl[lay.off + lay.w * lay.h // 2:]
    check_protected(lay, sl, out)
    return out
