"""Shop item descriptions: the text panel of the seven 224x80 VDP1 pictures in SHOPDAT.BIN.

The shop draws the description of the item under the cursor as one 8bpp VDP1 sprite (colour
bank 0x100, palette = colour entry 53 of 1ST.BIN 0x0601576C, CRAM colour 0x100). SL.BIN
(loaded verbatim at 0x06060000) lists the shop sprites at 0x060A9A40 as 8-byte records
{u16 id, u16 mode, u32 source}; records 16-22 are the descriptions (item 1, 2, 3 at LV1, LV2,
LV3, item 4, 5), sources in SHOPDAT.BIN loaded at 0x20200000. Each picture holds the item
drawing on the left and a grey panel with three text lines on the right; only the panel
(x 92-219, y 5-76) changes.
"""
import json
import struct
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from . import shopmenu

SL_BASE = 0x06060000
SPRITE_TABLE = 0x060A9A40
FIRST_PICTURE = 16
PICTURES = 7
SHOP_LOAD = 0x20200000
PIC_W, PIC_H = 224, 80
PANEL_X, PANEL_Y, PANEL_W, PANEL_H = 92, 5, 128, 72
LINE_TOP = (4, 27, 51)           # panel rows of the three original text lines
TEXT_X = 5
INK, BG = 5, 6
EDGES = (9, 10, 11, 12)
OLD_EDGES = (99,)                # dark edge colour found only in the original item 4 text
PANEL_COLOURS = 53               # colour entry written to CRAM colour 0x100
SUPER = shopmenu.SUPER


IDS = ("shop.desc.1", "shop.desc.2", "shop.desc.3a", "shop.desc.3b", "shop.desc.3c", "shop.desc.4", "shop.desc.5")


class ShopDescError(ValueError):
    pass


def picture_offsets(sl: bytes, shop_size: int) -> list[int]:
    """SHOPDAT.BIN offsets of the seven description pictures, from the SL.BIN sprite table.
    They must end before the menu cell block (shopmenu.CELL_FILE) and inside the file."""
    if len(sl) < SPRITE_TABLE - SL_BASE + 8 * (FIRST_PICTURE + PICTURES):
        raise ShopDescError("SL.BIN ends before the sprite table")
    out = []
    for k in range(PICTURES):
        src = struct.unpack_from(">I", sl, SPRITE_TABLE - SL_BASE + 8 * (FIRST_PICTURE + k) + 4)[0]
        out.append(src - SHOP_LOAD)
    if any(b - a != PIC_W * PIC_H for a, b in zip(out, out[1:])) or out[0] < 0:
        raise ShopDescError(f"description pictures are not consecutive {PIC_W}x{PIC_H}: {[hex(o) for o in out]}")
    end = out[-1] + PIC_W * PIC_H
    if end > min(shop_size, shopmenu.CELL_FILE):
        raise ShopDescError(f"description pictures end at {end:#x}, past the menu cells or the file")
    return out


def panel_colours(first: bytes, sl: bytes) -> dict[int, tuple]:
    """{colour index: 5-bit RGB} of the panel colours."""
    src, count = struct.unpack_from(">IH", first, shopmenu.COLOUR_TABLE - shopmenu.FIRST_BASE + 8 * PANEL_COLOURS)
    if count <= max(EDGES) or not 0 <= src - SL_BASE <= len(sl) - 2 * count:
        raise ShopDescError(f"panel colours: {count} at {src:#x}")
    out = {}
    for i in (INK, BG) + EDGES:
        c = struct.unpack_from(">H", sl, src - SL_BASE + 2 * i)[0]
        out[i] = (c & 31, (c >> 5) & 31, (c >> 10) & 31)
    return out


def _panel(shop: bytes, off: int) -> np.ndarray:
    pic = np.frombuffer(shop[off:off + PIC_W * PIC_H], np.uint8).reshape(PIC_H, PIC_W)
    return pic[PANEL_Y:PANEL_Y + PANEL_H, PANEL_X:PANEL_X + PANEL_W]


def read_panel(shop: bytes, off: int) -> np.ndarray:
    """Copy of a picture's text panel (PANEL_H x PANEL_W colour indices)."""
    return _panel(shop, off).copy()


def check_panel(shop: bytes, off: int) -> None:
    """The panel holds only text colours and no pixel just outside it is a text colour."""
    pic = np.frombuffer(shop[off:off + PIC_W * PIC_H], np.uint8).reshape(PIC_H, PIC_W)
    text = (INK, BG) + EDGES + OLD_EDGES
    if not np.isin(_panel(shop, off), text).all():
        raise ShopDescError(f"picture at {off:#x}: panel holds non-text colours")
    ring = [pic[PANEL_Y - 1, PANEL_X:PANEL_X + PANEL_W], pic[PANEL_Y + PANEL_H, PANEL_X:PANEL_X + PANEL_W],
            pic[PANEL_Y:PANEL_Y + PANEL_H, PANEL_X - 1], pic[PANEL_Y:PANEL_Y + PANEL_H, PANEL_X + PANEL_W]]
    if any(np.isin(r, text).any() for r in ring):
        raise ShopDescError(f"picture at {off:#x}: panel is wider than {PANEL_W}x{PANEL_H}")


def render_panel(lines: list[str], colours: dict[int, tuple]) -> np.ndarray:
    """PANEL_H x PANEL_W index image: three left-aligned lines at the original line rows,
    each pixel the panel colour whose brightness is closest to the anti-aliased text."""
    if len(lines) != len(LINE_TOP):
        raise ShopDescError(f"{len(lines)} lines, the panel has {len(LINE_TOP)}")
    font, cmap = shopmenu.load_font()
    missing = sorted({c for c in "".join(lines) if c != " " and ord(c) not in cmap})
    if missing:
        raise ShopDescError(f"no glyph for {missing!r}")
    im = Image.new("L", (PANEL_W * SUPER, PANEL_H * SUPER), 0)
    draw = ImageDraw.Draw(im)
    for top, line in zip(LINE_TOP, lines):
        need = TEXT_X + int(np.ceil(font.getlength(line) / SUPER)) + 2
        if need > PANEL_W:
            raise ShopDescError(f"{line!r} needs {need} px, the panel is {PANEL_W} wide")
        draw.text((TEXT_X * SUPER, (top + 13) * SUPER), line, font=font, fill=255, anchor="ls")
    a = np.asarray(im.resize((PANEL_W, PANEL_H), Image.LANCZOS), dtype=np.float32) / 255
    a[a < 0.1] = 0                   # drop resampling ringing around the glyphs
    lum = {i: sum(c) / 3 for i, c in colours.items()}
    want = lum[BG] + a * (lum[INK] - lum[BG])
    idx = np.array(sorted(lum, key=lum.get))
    levels = np.array([lum[i] for i in idx])
    return idx[np.abs(want[..., None] - levels).argmin(-1)].astype(np.uint8)


def apply(shop: bytes, offs: list[int], panels: list[np.ndarray]) -> bytes:
    """SHOPDAT.BIN with each picture's panel replaced."""
    if len(panels) != len(offs):
        raise ShopDescError(f"{len(panels)} panels for {len(offs)} pictures")
    out = bytearray(shop)
    for off, p in zip(offs, panels):
        if p.shape != (PANEL_H, PANEL_W):
            raise ShopDescError(f"panel {p.shape}, expected {PANEL_H}x{PANEL_W}")
        for y in range(PANEL_H):
            o = off + (PANEL_Y + y) * PIC_W + PANEL_X
            out[o:o + PANEL_W] = p[y].tobytes()
    return bytes(out)


def editable(offs: list[int], shop_size: int) -> np.ndarray:
    """Mask of the SHOPDAT.BIN bytes this module may change (the seven panels)."""
    mask = np.zeros(shop_size, bool)
    for off in offs:
        for y in range(PANEL_H):
            o = off + (PANEL_Y + y) * PIC_W + PANEL_X
            mask[o:o + PANEL_W] = True
    return mask


def kept(offs: list[int], sl_size: int, shop_size: int) -> tuple[np.ndarray, np.ndarray]:
    """Masks of the bytes the descriptions rely on but this module leaves alone: the sprite
    table records and the pictures outside the panels."""
    sl_mask, shop_mask = np.zeros(sl_size, bool), np.zeros(shop_size, bool)
    sl_mask[SPRITE_TABLE - SL_BASE + 8 * FIRST_PICTURE:SPRITE_TABLE - SL_BASE + 8 * (FIRST_PICTURE + PICTURES)] = True
    for off in offs:
        shop_mask[off:off + PIC_W * PIC_H] = True
    return sl_mask, shop_mask & ~editable(offs, shop_size)


def check_colours(offs: list[int], shop: bytes) -> None:
    """Every panel pixel is a panel colour (the original's extra edge colour no longer)."""
    mask = editable(offs, len(shop))
    bad = np.flatnonzero(mask & ~np.isin(np.frombuffer(shop, np.uint8), (INK, BG) + EDGES))
    if len(bad):
        raise ShopDescError(f"protected: SHOPDAT.BIN byte {int(bad[0]):#x} is not a panel colour")


def check_protected(offs: list[int], before: bytes, after: bytes) -> None:
    """Fail unless only panel pixels differ and every changed panel uses panel colours."""
    if len(before) != len(after):
        raise ShopDescError("protected: SHOPDAT.BIN size changed")
    panel = np.zeros(len(before), bool)
    for off in offs:
        for y in range(PANEL_H):
            o = off + (PANEL_Y + y) * PIC_W + PANEL_X
            panel[o:o + PANEL_W] = True
    diff = np.frombuffer(before, np.uint8) != np.frombuffer(after, np.uint8)
    stray = np.flatnonzero(diff & ~panel)
    if len(stray):
        raise ShopDescError(f"protected: SHOPDAT.BIN byte {int(stray[0]):#x} changed")
    bad = np.flatnonzero(diff & ~np.isin(np.frombuffer(after, np.uint8), (INK, BG) + EDGES))
    if len(bad):
        raise ShopDescError(f"protected: SHOPDAT.BIN byte {int(bad[0]):#x} is not a panel colour")


def load_texts(path) -> list[list[str]]:
    """Korean description lines in picture order from translation/graphics/shop_desc.json."""
    entries = json.loads(Path(path).read_text(encoding="utf-8"))["entries"]
    texts = [[line.strip() for line in e["ko"]] for e in entries]
    if len(texts) != PICTURES or any(len(t) != len(LINE_TOP) for t in texts):
        raise ShopDescError(f"{path}: need {PICTURES} descriptions of {len(LINE_TOP)} lines")
    if [e["id"] for e in entries] != list(IDS):
        raise ShopDescError(f"{path}: entry ids must be {list(IDS)} in that order (sprite records 16-22)")
    return texts


def build_file(first: bytes, sl: bytes, shop: bytes, texts: list[list[str]]) -> bytes:
    """SHOPDAT.BIN with the descriptions drawn; the protected bytes are checked."""
    offs = picture_offsets(sl, len(shop))
    for o in offs:
        check_panel(shop, o)
    colours = panel_colours(first, sl)
    out = apply(shop, offs, [render_panel(t, colours) for t in texts])
    check_protected(offs, shop, out)
    return out
