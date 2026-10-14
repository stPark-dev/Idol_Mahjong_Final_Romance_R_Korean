"""Shop menu item names: NBG0 cells in SHOPDAT.BIN and the NBG0 map (entry 56) in SL.BIN.

The shop (1ST.BIN 0x06018100) loads SHOPDAT.BIN whole at 0x00200000 and copies
SHOPDAT[0x1FF80:0x29840] to VDP2 VRAM 0x00040 (8bpp cells, one cell = 2 units of 0x20 bytes);
SL.BIN sits at 0x06060000 unchanged and holds the maps. Map entries are 12-byte records at
0x06015124 {u8 w, u8 h, u16 add, u32 src, u16 stride, u16}; entry 56 is written to NBG0 as
(word + add) | 0x4000. Each item row is two map rows; it uses its own 16-colour block
(160 + 16k) whose brightness the game changes by palette (affordable or not), so one drawing
serves every state. Entry 57/58 overlay item 3 from column 19 (LV2/LV3), so item 3 ends at 17.

Only the text columns of the five item rows and the cells they used (the pool) change.
Other shop maps reuse the same unit numbers but on other character bases, so they do not
touch these cells: 59 goes to NBG1 (+0x1000 units), 60-62 to NBG2 (+0x3000 units); 54, 55, 63
and 64 belong to other screens (0x06011E1A, 0x06012488, 0x06018DF2). The menu cells are
shared only by entry 56 itself and the LV overlays 57/58 (NBG0, 0x06018738).
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
MAP_TABLE = 0x06015124
MENU_MAP = 56
LEVEL_MAPS = (57, 58)            # map8 overlays of item 3 (0x06018738)
COLOUR_TABLE = 0x0601576C        # 8-byte records {u32 src, u16 count, u16}
MENU_COLOURS = 42                # written to CRAM colour 0x440 (0x06018134)
MENU_COLOUR_FIRST = 0x440
MENU_PALETTE = 0x400             # map palette 4 x 0x100
CELL_FILE = 0x1FF80              # SHOPDAT offset copied to VDP2 0x00040
CELL_VRAM = 0x40
CELL_END = 0x29840
ITEM_ROWS = (5, 7, 9, 11, 13)
TEXT_X0 = 4
TEXT_X1 = (23, 23, 18, 22, 22)   # first column after the text (price, LV overlay)
COLOUR_BASE = 160
INK, BG, EDGE = 9, 10, (11, 12)

FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc"
FONT_INDEX = 1                   # Noto Sans CJK KR
FONT_PX = 15
SUPER = 4


class ShopMenuError(ValueError):
    pass


@dataclass(frozen=True)
class Ramp:
    ink: int
    near: int                    # edge colour closer to the ink
    far: int                     # edge colour closer to the background
    bg: int


@dataclass(frozen=True)
class Item:
    row: int
    cells: int                   # text width in 8 px cells
    ramp: Ramp
    bg_units: tuple


@dataclass(frozen=True)
class Layout:
    map_off: int                 # SL.BIN offset of entry 56's words
    stride: int
    add: int
    items: tuple
    pool: tuple                  # units free for text cells (first unit of each 8bpp cell)
    protected: frozenset         # units shown elsewhere while the menu is up

    @staticmethod
    def read(first: bytes, sl: bytes) -> "Layout":
        w, h, add, src, stride = _entry(first, MENU_MAP)
        if (w, h, add) != (44, 30, 2):
            raise ShopMenuError(f"menu map entry {MENU_MAP} is {w}x{h}+{add}")
        off = src - SL_BASE
        if off % 2 or not 0 <= off <= len(sl) - 2 * w * h:
            raise ShopMenuError(f"menu map at {src:#x} is outside SL.BIN")
        unit = lambda x, y: struct.unpack_from(">H", sl, off + 2 * (y * stride + x))[0] + add
        palette = _palette(first, sl)
        items, text_units = [], set()
        for k, (row, x1) in enumerate(zip(ITEM_ROWS, TEXT_X1)):
            bg = tuple(sorted({unit(TEXT_X0 - 1, y) for y in (row, row + 1)}))
            if len(bg) != 1:
                raise ShopMenuError(f"item {k}: two background cells {bg}")
            base = MENU_PALETTE + COLOUR_BASE + 16 * k
            dist = lambda c: sum((a - b) ** 2 for a, b in zip(palette[base + c], palette[base + INK]))
            near, far = sorted(EDGE, key=dist)
            items.append(Item(row, x1 - TEXT_X0, Ramp(INK + 16 * k + COLOUR_BASE, near + 16 * k + COLOUR_BASE,
                                                       far + 16 * k + COLOUR_BASE, BG + 16 * k + COLOUR_BASE), bg))
            text_units |= {unit(x, y) for y in (row, row + 1) for x in range(TEXT_X0, x1)}
        shown = set()
        for y in range(h):
            for x in range(w):
                k = next((k for k, it in enumerate(items) if y in (it.row, it.row + 1)), None)
                if k is None or not TEXT_X0 <= x < TEXT_X0 + items[k].cells:
                    shown |= {unit(x, y), unit(x, y) + 1}
        for i in LEVEL_MAPS:
            lw, lh, ladd, lsrc, lstride = _entry(first, i)
            if (lw, lh) != (8, 2) or not 0 <= lsrc - SL_BASE <= len(sl) - lh * lstride:
                raise ShopMenuError(f"level map {i} is {lw}x{lh} at {lsrc:#x}")
            shown |= {sl[lsrc - SL_BASE + y * lstride + x] + ladd + d for y in range(lh) for x in range(lw) for d in (0, 1)}
        pool = tuple(sorted(u for u in text_units if u not in shown and u + 1 not in shown))
        for u in pool:
            if u % 2 or not (CELL_VRAM <= u * 0x20 and (u + 2) * 0x20 <= CELL_VRAM + CELL_END - CELL_FILE):
                raise ShopMenuError(f"text cell unit {u:#x} outside the menu cell block")
        return Layout(off, stride, add, tuple(items), pool, frozenset(shown))


def _entry(first: bytes, i: int):
    w, h, add, src, stride = struct.unpack_from(">BBHIH", first, MAP_TABLE - FIRST_BASE + 12 * i)
    return w, h, add, src, stride


def _palette(first: bytes, sl: bytes) -> dict[int, tuple]:
    src, count = struct.unpack_from(">IH", first, COLOUR_TABLE - FIRST_BASE + 8 * MENU_COLOURS)
    need = MENU_PALETTE + COLOUR_BASE + 16 * len(ITEM_ROWS) - MENU_COLOUR_FIRST
    if count < need or not 0 <= src - SL_BASE <= len(sl) - 2 * count:
        raise ShopMenuError(f"menu colours: {count} at {src:#x}, need {need} inside SL.BIN")
    out = {}
    for i in range(count):
        c = struct.unpack_from(">H", sl, src - SL_BASE + 2 * i)[0]
        out[MENU_COLOUR_FIRST + i] = (c & 31, (c >> 5) & 31, (c >> 10) & 31)
    return out


def load_texts(path) -> list[str]:
    """Korean item names in table order from translation/graphics/shop_menu.json."""
    entries = json.loads(Path(path).read_text(encoding="utf-8"))["entries"]
    texts = [e["ko"].strip() for e in entries]
    if len(texts) != len(ITEM_ROWS) or not all(texts):
        raise ShopMenuError(f"{path}: need {len(ITEM_ROWS)} non-empty item names")
    return texts


def build_files(first: bytes, sl: bytes, shop: bytes, texts: list[str]) -> tuple[bytes, bytes]:
    """SL.BIN and SHOPDAT.BIN with the item names drawn; the protected bytes are checked."""
    lay = Layout.read(first, sl)
    images = [render_text(t, it.cells * 8, it.ramp) for t, it in zip(texts, lay.items)]
    out_sl, out_shop = apply(lay, sl, shop, images)
    check_protected(lay, sl, shop, out_sl, out_shop)
    return out_sl, out_shop


def cell_offset(unit: int) -> int:
    """SHOPDAT offset of a VDP2 cell unit in the menu cell block."""
    return CELL_FILE + unit * 0x20 - CELL_VRAM


@lru_cache(maxsize=None)
def load_font():
    return (ImageFont.truetype(FONT, FONT_PX * SUPER, index=FONT_INDEX),
            frozenset(TTCollection(FONT).fonts[FONT_INDEX].getBestCmap()))


def render_text(text: str, width: int, ramp: Ramp) -> np.ndarray:
    """16 x width index image: text left-aligned from x 1 on the ramp's background."""
    font, cmap = load_font()
    missing = sorted({c for c in text if c != " " and ord(c) not in cmap})
    if missing:
        raise ShopMenuError(f"no glyph for {missing!r}")
    need = int(np.ceil(font.getlength(text) / SUPER)) + 2
    if need > width:
        raise ShopMenuError(f"{text!r} is {need} px wide, the box has {width}")
    im = Image.new("L", (width * SUPER, 16 * SUPER), 0)
    ImageDraw.Draw(im).text((1 * SUPER, 13 * SUPER), text, font=font, fill=255, anchor="ls")
    a = np.asarray(im.resize((width, 16), Image.LANCZOS), dtype=np.float32) / 255
    out = np.full((16, width), ramp.bg, np.uint8)
    out[a > 0.2] = ramp.far
    out[a > 0.45] = ramp.near
    out[a > 0.7] = ramp.ink
    return out


def cut_cells(img: np.ndarray) -> list[bytes]:
    """8x8 cells of a 16-row image, top row first, left to right."""
    return [bytes(img[y:y + 8, x:x + 8].flatten()) for y in (0, 8) for x in range(0, img.shape[1], 8)]


def assign(items: list[list[bytes]], pool: list[int], bg_units: list[int], bg_cells: list[bytes]):
    """Units for every cell (background-only cells keep the row's background cell, equal cells
    share one unit) and {unit: cell} to write, taking units from the pool in order."""
    free = iter(pool)
    given: dict[bytes, int] = {}
    writes, out = {}, []
    for cells, bu, bc in zip(items, bg_units, bg_cells):
        row = []
        for c in cells:
            if c == bc:
                row.append(bu)
                continue
            if c not in given:
                u = next(free, None)
                if u is None:
                    raise ShopMenuError(f"menu needs more than {len(pool)} text cells")
                given[c], writes[u] = u, c
            row.append(given[c])
        out.append(row)
    return out, writes


def _cell(shop: bytes, unit: int) -> bytes:
    o = cell_offset(unit)
    return shop[o:o + 64]


def _word(lay: Layout, it: Item, dy: int, x: int) -> int:
    """SL.BIN offset of the map word for text column x of an item's top (dy 0) or bottom row."""
    return lay.map_off + 2 * ((it.row + dy) * lay.stride + TEXT_X0 + x)


def decode_items(lay: Layout, sl: bytes, shop: bytes) -> list[np.ndarray]:
    """Index images (16 x text width) of the five item rows as the game would draw them."""
    out = []
    for it in lay.items:
        img = np.zeros((16, it.cells * 8), np.uint8)
        for dy in (0, 1):
            for x in range(it.cells):
                u = struct.unpack_from(">H", sl, _word(lay, it, dy, x))[0] + lay.add
                img[dy * 8:dy * 8 + 8, x * 8:x * 8 + 8] = np.frombuffer(_cell(shop, u), np.uint8).reshape(8, 8)
        out.append(img)
    return out


def apply(lay: Layout, sl: bytes, shop: bytes, images: list[np.ndarray]) -> tuple[bytes, bytes]:
    """SL.BIN and SHOPDAT.BIN with the item rows replaced by the given index images."""
    if len(images) != len(lay.items):
        raise ShopMenuError(f"{len(images)} images for {len(lay.items)} items")
    for img, it in zip(images, lay.items):
        if img.shape != (16, it.cells * 8):
            raise ShopMenuError(f"item image {img.shape}, box is 16x{it.cells * 8}")
    units, writes = assign([cut_cells(im) for im in images], list(lay.pool),
                           [it.bg_units[0] for it in lay.items], [_cell(shop, it.bg_units[0]) for it in lay.items])
    out_sl, out_shop = bytearray(sl), bytearray(shop)
    for it, row in zip(lay.items, units):
        for i, u in enumerate(row):
            dy, x = divmod(i, it.cells)
            struct.pack_into(">H", out_sl, _word(lay, it, dy, x), u - lay.add)
    for u, c in writes.items():
        out_shop[cell_offset(u):cell_offset(u) + 64] = c
    return bytes(out_sl), bytes(out_shop)


def check_protected(lay: Layout, sl0: bytes, shop0: bytes, sl1: bytes, shop1: bytes) -> None:
    """Fail unless only item text map words and pool cells differ, and every text word points at
    a pool cell or its row's background cell."""
    if len(sl0) != len(sl1) or len(shop0) != len(shop1):
        raise ShopMenuError("protected: file size changed")
    words = {_word(lay, it, dy, x) for it in lay.items for dy in (0, 1) for x in range(it.cells)}
    for i in np.flatnonzero(np.frombuffer(sl0, np.uint8) != np.frombuffer(sl1, np.uint8)):
        if int(i) & ~1 not in words:
            raise ShopMenuError(f"protected: SL.BIN byte {int(i):#x} changed")
    pool = set(lay.pool)
    cells = {cell_offset(u) + i for u in pool for i in range(64)}
    for i in np.flatnonzero(np.frombuffer(shop0, np.uint8) != np.frombuffer(shop1, np.uint8)):
        if int(i) not in cells:
            raise ShopMenuError(f"protected: SHOPDAT.BIN byte {int(i):#x} changed")
    for it in lay.items:
        allowed = pool | {it.bg_units[0]}
        for dy in (0, 1):
            for x in range(it.cells):
                u = struct.unpack_from(">H", sl1, _word(lay, it, dy, x))[0] + lay.add
                if u not in allowed:
                    raise ShopMenuError(f"protected: item row {it.row} column {x} points at cell {u:#x}")
