"""Shop message boxes: welcome (map entry 61) and goodbye (62) on NBG2.

Both are 28x10 byte maps in SL.BIN (1ST.BIN map table 0x06015124, written by 0x0600E370 as
(byte + add) | palette 1 << 12 to NBG2 at 0x25E48902). NBG2 uses 16-colour cells whose
character base is VDP2 0x60000 (PNCN2 0x80CC); one cell = one unit of 0x20 bytes. The cells
come from SHOPDAT.BIN through the copy list at SL.BIN 0x060604F0 (4 x {src, size}, written
contiguously from VDP2 0x60040 by 0x06018178). Colours: entry 54 of the colour table (CRAM
colour 0x610): background 6, white 9, edges 10-13.

Each box keeps its frame, its background cell and, on the welcome box, the C and START
buttons (columns 19-22); only the text columns of each line and the cells they used change,
each map drawing from its own cells (a byte map reaches add .. add + 255 only).
NBG2 shows only maps 60-62 in the shop (0x06018230-0x0601823C, 0x06017FEC); the gold digits
come from map 60's own cells, and the box stays intact when the gold changes (1000G -> 0G).
"""
import json
import struct
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from fontTools.ttLib import TTCollection
from PIL import Image, ImageDraw, ImageFont

from . import shopmenu

FIRST_BASE = 0x06004000
SL_BASE = 0x06060000
MAP_TABLE = 0x06015124
MAPS = (61, 62)
GOLD_MAP = 60                    # gold box, same NBG2 cells
COPY_LIST = 0x060604F0
COPIES = 4
COPY_VRAM = 0x60040
CHAR_BASE = 0x60000
SHOP_LOAD = 0x20200000
COLOURS = 54                     # colour entry written to CRAM colour 0x610 (0x0601814E)
BG, INK = 6, 9
EDGES = (10, 11, 12, 13)
SUPER = shopmenu.SUPER
LINES = (                        # (map, top cell row, first column, columns, align, x in the line box)
    (61, 1, 1, 26, "left", 2),
    (61, 4, 1, 18, "right", 142),   # ends just before the C button
    (61, 7, 1, 18, "right", 142),   # ends just before the START button
    (62, 2, 1, 26, "left", 10),
    (62, 5, 1, 26, "left", 10),
)


BUTTON = (61, 7, 19, 4)          # START button: map, top cell row, first column, columns
BUTTON_FACE = (2, 1, 31, 14)     # x0, y0, x1, y1 of the button face inside its 32x16 cells
BUTTON_FACE_COLOUR, BUTTON_INK, BUTTON_EDGE, BUTTON_LIGHT = 12, 8, 13, 10
BUTTON_FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc"   # bold strokes like the original
BUTTON_PX = 12


IDS = ("shop.welcome.1", "shop.welcome.2", "shop.welcome.3", "shop.exit.1", "shop.exit.2", "shop.button.start")


class ShopMsgError(ValueError):
    pass


@dataclass(frozen=True)
class Map:
    off: int                     # SL.BIN offset of the byte map
    add: int
    bg: int                      # unit of the box background cell


@dataclass(frozen=True)
class Line:
    map: int
    row: int
    col: int
    cols: int
    align: str
    x: int
    pool: tuple                  # units free for this line's map


@dataclass(frozen=True)
class Layout:
    maps: dict
    lines: tuple
    copies: tuple                # (VRAM start, SHOPDAT offset, size)
    colours: dict
    button: tuple                # START button units, top and bottom row

    @staticmethod
    def read(first: bytes, sl: bytes) -> "Layout":
        """Map, copy-list and colour facts of the two boxes; refuses an unexpected layout."""
        copies, v = [], COPY_VRAM
        for k in range(COPIES):
            src, size = struct.unpack_from(">II", sl, COPY_LIST - SL_BASE + 8 * k)
            if src < SHOP_LOAD or (src - SHOP_LOAD) % 0x20 or size % 0x20:
                raise ShopMsgError(f"NBG2 copy {k}: {src:#x}+{size:#x} is not 0x20-aligned SHOPDAT data")
            copies.append((v, src - SHOP_LOAD, size))
            v += size
        maps, units, src_off, adds = {}, {}, {}, {}
        for i in MAPS + (GOLD_MAP,):
            w, h, add, src, stride = struct.unpack_from(">BBHIH", first, MAP_TABLE - FIRST_BASE + 12 * i)
            off = src - SL_BASE
            src_off[i], adds[i] = off, add
            want = (28, 10, 28) if i in MAPS else (10, 2, 10)
            if (w, h, stride) != want or not 0 <= off <= len(sl) - w * h:
                raise ShopMsgError(f"map {i} is {w}x{h}/{stride} at {src:#x}, expected {want}")
            units[i] = [[sl[off + y * stride + x] + add for x in range(w)] for y in range(h)]
        text = {(i, y, x) for i, row, col, cols, _, _ in LINES for y in (row, row + 1) for x in range(col, col + cols)}
        for i in MAPS:      # background: the commonest cell inside the frame outside the text
            inner = Counter(units[i][y][x] for y in range(1, 9) for x in range(1, 27) if (i, y, x) not in text)
            maps[i] = Map(src_off[i], adds[i], inner.most_common(1)[0][0])
        shown = {u for i, m in units.items() for y, r in enumerate(m) for x, u in enumerate(r) if (i, y, x) not in text}
        lines = []
        for i, row, col, cols, align, x in LINES:
            mine = {units[i][y][c] for j, rr, cc, n, _, _ in LINES if j == i
                    for y in (rr, rr + 1) for c in range(cc, cc + n)}
            pool = tuple(sorted(mine - shown))
            lines.append(Line(i, row, col, cols, align, x, pool))
        bi, brow, bcol, bcols = BUTTON
        button = [[units[bi][brow + dy][bcol + x] for x in range(bcols)] for dy in (0, 1)]
        flat = [u for r in button for u in r]
        elsewhere = [u for i, m in units.items() for y, r in enumerate(m) for x, u in enumerate(r)
                     if not (i == bi and brow <= y < brow + 2 and bcol <= x < bcol + bcols)]
        if len(set(flat)) != len(flat) or set(flat) & set(elsewhere):
            raise ShopMsgError("START button cells are shared")
        pools = {ln.map: set(ln.pool) for ln in lines}
        if pools[bi] & set(flat):
            raise ShopMsgError("START button cells are in the text pool")
        if pools[MAPS[0]] & pools[MAPS[1]]:
            raise ShopMsgError("the two message boxes share text cells")
        lay = Layout(maps, tuple(lines), tuple(copies), _colours(first, sl), tuple(map(tuple, button)))
        for ln in lines:
            for u in ln.pool:
                cell_offset(lay, u)
        return lay


def _colours(first: bytes, sl: bytes) -> dict[int, tuple]:
    src, count = struct.unpack_from(">IH", first, shopmenu.COLOUR_TABLE - FIRST_BASE + 8 * COLOURS)
    if count < 16 or not 0 <= src - SL_BASE <= len(sl) - 2 * count:
        raise ShopMsgError(f"message colours: {count} at {src:#x}")
    out = {}
    for i in (BG, INK) + EDGES:
        c = struct.unpack_from(">H", sl, src - SL_BASE + 2 * i)[0]
        out[i] = (c & 31, (c >> 5) & 31, (c >> 10) & 31)
    return out


def cell_offset(lay: Layout, unit: int) -> int:
    """SHOPDAT.BIN offset of an NBG2 cell unit."""
    a = CHAR_BASE + unit * 0x20
    for v, o, size in lay.copies:
        if v <= a and a + 0x20 <= v + size:
            return o + a - v
    raise ShopMsgError(f"NBG2 cell {unit:#x} is not copied from SHOPDAT.BIN")


def render_line(text: str, width: int, x: int, align: str, colours: dict[int, tuple]) -> np.ndarray:
    """16 x width index image: text starting at x (left) or ending at x (right), each pixel the
    box colour whose brightness is closest to the anti-aliased text."""
    font, cmap = shopmenu.load_font()
    missing = sorted({c for c in text if c != " " and ord(c) not in cmap})
    if missing:
        raise ShopMsgError(f"no glyph for {missing!r}")
    need = int(np.ceil(font.getlength(text) / SUPER))
    left = x if align == "left" else x - need
    if left < 1 or left + need + 1 > width:
        raise ShopMsgError(f"{text!r} is {need} px wide, does not fit {width} px at {x} ({align})")
    im = Image.new("L", (width * SUPER, 16 * SUPER), 0)
    ImageDraw.Draw(im).text((left * SUPER, 13 * SUPER), text, font=font, fill=255, anchor="ls")
    a = np.asarray(im.resize((width, 16), Image.LANCZOS), dtype=np.float32) / 255
    a[a < 0.1] = 0                   # drop resampling ringing so empty cells stay background
    lum = {i: sum(c) / 3 for i, c in colours.items()}
    want = lum[BG] + a * (lum[INK] - lum[BG])
    idx = np.array(sorted(lum, key=lum.get))
    levels = np.array([lum[i] for i in idx])
    return idx[np.abs(want[..., None] - levels).argmin(-1)].astype(np.uint8)


def _byte(lay: Layout, ln: Line, dy: int, x: int) -> int:
    """SL.BIN offset of the map byte for column x of a line's top (dy 0) or bottom row."""
    return lay.maps[ln.map].off + (ln.row + dy) * 28 + ln.col + x


def _decode(cell: bytes) -> np.ndarray:
    b = np.frombuffer(cell, np.uint8)
    return np.stack([b >> 4, b & 15], 1).reshape(8, 8)


def _encode(img: np.ndarray) -> bytes:
    return bytes((img[:, 0::2] << 4 | img[:, 1::2]).astype(np.uint8).flatten())


def decode_lines(lay: Layout, sl: bytes, shop: bytes) -> list[np.ndarray]:
    """Index images (16 x line width) of the five lines as the game would draw them."""
    out = []
    for ln in lay.lines:
        img = np.zeros((16, ln.cols * 8), np.uint8)
        for dy in (0, 1):
            for x in range(ln.cols):
                o = cell_offset(lay, sl[_byte(lay, ln, dy, x)] + lay.maps[ln.map].add)
                img[dy * 8:dy * 8 + 8, x * 8:x * 8 + 8] = _decode(shop[o:o + 32])
        out.append(img)
    return out


def decode_button(lay: Layout, sl: bytes, shop: bytes) -> np.ndarray:
    """16 x 32 index image of the START button as the game would draw it."""
    i, row, col, cols = BUTTON
    img = np.zeros((16, cols * 8), np.uint8)
    for dy in (0, 1):
        for x in range(cols):
            o = cell_offset(lay, sl[lay.maps[i].off + (row + dy) * 28 + col + x] + lay.maps[i].add)
            img[dy * 8:dy * 8 + 8, x * 8:x * 8 + 8] = _decode(shop[o:o + 32])
    return img


def render_button(text: str, base: np.ndarray) -> np.ndarray:
    """The button with its face cleared and text drawn centred in black, a light edge to the
    lower right of each stroke as on the original."""
    x0, y0, x1, y1 = BUTTON_FACE
    w, h = x1 - x0, y1 - y0
    try:
        font = ImageFont.truetype(BUTTON_FONT, BUTTON_PX * SUPER, index=shopmenu.FONT_INDEX)
        cmap = TTCollection(BUTTON_FONT).fonts[shopmenu.FONT_INDEX].getBestCmap()
    except OSError as ex:
        raise ShopMsgError(f"button font {BUTTON_FONT}: {ex}") from ex
    if any(c != " " and ord(c) not in cmap for c in text):
        raise ShopMsgError(f"no glyph in {text!r}")
    l, t, r, b = font.getbbox(text)
    if (r - l) / SUPER + 2 > w or (b - t) / SUPER > h:
        raise ShopMsgError(f"{text!r} does not fit the {w}x{h} button face")
    im = Image.new("L", (w * SUPER, h * SUPER), 0)
    ImageDraw.Draw(im).text(((w * SUPER - (r - l)) / 2 - l, (h * SUPER - (b - t)) / 2 - t), text, font=font, fill=255)
    a = np.asarray(im.resize((w, h), Image.LANCZOS), dtype=np.float32) / 255
    face = np.full((h, w), BUTTON_FACE_COLOUR, np.uint8)
    face[a > 0.25] = BUTTON_EDGE
    face[a > 0.5] = BUTTON_INK
    ink = face == BUTTON_INK
    light = np.zeros_like(ink)
    light[1:, 1:] = ink[:-1, :-1]
    face[light & (face == BUTTON_FACE_COLOUR)] = BUTTON_LIGHT
    out = base.copy()
    out[y0:y1, x0:x1] = face
    return out


def _put_button(lay: Layout, sl: bytes, shop: bytearray, img: np.ndarray) -> None:
    i, row, col, cols = BUTTON
    for dy in (0, 1):
        for x in range(cols):
            o = cell_offset(lay, sl[lay.maps[i].off + (row + dy) * 28 + col + x] + lay.maps[i].add)
            shop[o:o + 32] = _encode(img[dy * 8:dy * 8 + 8, x * 8:x * 8 + 8])


def apply(lay: Layout, sl: bytes, shop: bytes, images: list[np.ndarray]) -> tuple[bytes, bytes]:
    """SL.BIN and SHOPDAT.BIN with the lines replaced by the given index images."""
    if len(images) != len(lay.lines):
        raise ShopMsgError(f"{len(images)} images for {len(lay.lines)} lines")
    out_sl, out_shop = bytearray(sl), bytearray(shop)
    for i, m in lay.maps.items():
        lines = [(ln, im) for ln, im in zip(lay.lines, images) if ln.map == i]
        for ln, im in lines:
            if im.shape != (16, ln.cols * 8):
                raise ShopMsgError(f"line image {im.shape}, box is 16x{ln.cols * 8}")
        o = cell_offset(lay, m.bg)
        bg = shop[o:o + 32]
        cells = [[_encode(im[y:y + 8, x:x + 8]) for y in (0, 8) for x in range(0, im.shape[1], 8)] for _, im in lines]
        pool = list(lines[0][0].pool)
        try:
            units, writes = shopmenu.assign(cells, pool, [m.bg] * len(lines), [bg] * len(lines))
        except shopmenu.ShopMenuError as ex:
            raise ShopMsgError(f"message map {i} needs more than {len(pool)} cells") from ex
        for (ln, _), row in zip(lines, units):
            for k, u in enumerate(row):
                dy, x = divmod(k, ln.cols)
                out_sl[_byte(lay, ln, dy, x)] = u - m.add
        for u, c in writes.items():
            o = cell_offset(lay, u)
            if o + 32 > len(out_shop):
                raise ShopMsgError(f"NBG2 cell {u:#x} lies past the end of SHOPDAT.BIN")
            out_shop[o:o + 32] = c
    return bytes(out_sl), bytes(out_shop)


def editable(lay: Layout, sl_size: int, shop_size: int) -> tuple[np.ndarray, np.ndarray]:
    """Masks of the SL.BIN and SHOPDAT.BIN bytes this module may change."""
    sl_mask, shop_mask = np.zeros(sl_size, bool), np.zeros(shop_size, bool)
    for ln in lay.lines:
        for dy in (0, 1):
            for x in range(ln.cols):
                sl_mask[_byte(lay, ln, dy, x)] = True
        for u in ln.pool:
            shop_mask[cell_offset(lay, u):cell_offset(lay, u) + 32] = True
    for u in (u for r in lay.button for u in r):
        shop_mask[cell_offset(lay, u):cell_offset(lay, u) + 32] = True
    return sl_mask, shop_mask


def kept(lay: Layout, first: bytes, sl_size: int, shop_size: int) -> tuple[np.ndarray, np.ndarray]:
    """Masks of the bytes the boxes rely on but this module leaves alone: the rest of maps
    60-62 and of the NBG2 cell pieces."""
    write_sl, write_shop = editable(lay, sl_size, shop_size)
    sl_mask, shop_mask = np.zeros(sl_size, bool), np.zeros(shop_size, bool)
    for i in MAPS + (GOLD_MAP,):
        w, h, _, src, _ = struct.unpack_from(">BBHIH", first, MAP_TABLE - FIRST_BASE + 12 * i)
        sl_mask[src - SL_BASE:src - SL_BASE + w * h] = True
    for _, o, size in lay.copies:
        shop_mask[o:o + size] = True
    return sl_mask & ~write_sl, shop_mask & ~write_shop


def check_targets(lay: Layout, sl: bytes) -> None:
    """Every text byte points at a cell of its map's pool or the box background."""
    for ln in lay.lines:
        allowed = set(ln.pool) | {lay.maps[ln.map].bg}
        for dy in (0, 1):
            for x in range(ln.cols):
                u = sl[_byte(lay, ln, dy, x)] + lay.maps[ln.map].add
                if u not in allowed:
                    raise ShopMsgError(f"protected: map {ln.map} row {ln.row + dy} column {ln.col + x} -> cell {u:#x}")


def check_protected(lay: Layout, sl0: bytes, shop0: bytes, sl1: bytes, shop1: bytes) -> None:
    """Fail unless only text bytes and pool cells differ and the text bytes point at allowed cells."""
    if len(sl0) != len(sl1) or len(shop0) != len(shop1):
        raise ShopMsgError("protected: file size changed")
    sl_mask, shop_mask = editable(lay, len(sl0), len(shop0))
    for name, a, b, mask in (("SL.BIN", sl0, sl1, sl_mask), ("SHOPDAT.BIN", shop0, shop1, shop_mask)):
        stray = np.flatnonzero((np.frombuffer(a, np.uint8) != np.frombuffer(b, np.uint8)) & ~mask)
        if len(stray):
            raise ShopMsgError(f"protected: {name} byte {int(stray[0]):#x} changed")
    check_targets(lay, sl1)


def load_texts(path) -> list[str]:
    """Korean lines in LINES order, then the START button text, from shop_msg.json."""
    entries = json.loads(Path(path).read_text(encoding="utf-8"))["entries"]
    texts = [e["ko"].strip() for e in entries]
    if len(texts) != len(LINES) + 1 or not all(texts):
        raise ShopMsgError(f"{path}: need {len(LINES) + 1} non-empty entries (lines, then the button)")
    if [e["id"] for e in entries] != list(IDS):
        raise ShopMsgError(f"{path}: entry ids must be {list(IDS)} in that order")
    return texts


def build_files(first: bytes, sl: bytes, shop: bytes, texts: list[str], base_sl: bytes = None) -> tuple[bytes, bytes]:
    """SL.BIN and SHOPDAT.BIN with the box lines drawn; the protected bytes are checked.
    The layout is read from base_sl (the original SL.BIN) when given."""
    lay = Layout.read(first, sl if base_sl is None else base_sl)
    images = [render_line(t, ln.cols * 8, ln.x, ln.align, lay.colours) for t, ln in zip(texts, lay.lines)]
    out_sl, out_shop = apply(lay, sl, shop, images)
    button = render_button(texts[len(LINES)], decode_button(lay, out_sl, shop))
    out_shop = bytearray(out_shop)
    _put_button(lay, out_sl, out_shop, button)
    out_shop = bytes(out_shop)
    check_protected(lay, sl, shop, out_sl, out_shop)
    return out_sl, out_shop
