"""Title logo アイドル麻雀 ファイナルロマンスR replaced with the supplied Korean logo (D-3).

The title screen is one NBG0 picture: 44x30 cells of 256 colours. The title setup (0x06012424)
reads MJDAT3.BIN to low work RAM and copies its first 0xA940 bytes to VDP2 0x40 (copy list
0x060174E4), loads palette 40 (1ST.BIN 0x0603F1B4, 256 RGB555 words, bit 15 set) to CRAM 0x800
and draws map 55 (1ST.BIN 0x060443A8, 44x30 words, pattern name = word + 0x4002, the cell at
MJDAT3.BIN offset word * 32). The rest of MJDAT3.BIN is other data and stays.

The original logo (with its shadow) is cleared to the background colour, the Korean logo is
fitted inside its box and the screen is quantised again to 255 colours (index 0 is unused, as
in the original). The copyright line is quantised with it and keeps its look.
"""
import struct

import numpy as np
from PIL import Image

FIRST_BASE = 0x06004000
MAP_OFF = 0x060443A8 - FIRST_BASE
PAL_OFF = 0x0603F1B4 - FIRST_BASE
W, H = 44, 30
CELLS_START, CELLS_END = 0x40, 0xA940          # cells used by the map (the first, blank one is left alone)
CELL = 64
LOGO_ROWS = 200                                 # the logo lies above this line, the copyright below


class TitleError(ValueError):
    pass


def to_rgb555(rgb: np.ndarray, rounded: bool = False) -> np.ndarray:
    rgb = np.asarray(rgb, np.uint16)
    rgb = np.minimum((rgb + 4) >> 3, 31) if rounded else rgb >> 3
    return rgb[..., 0] | rgb[..., 1] << 5 | rgb[..., 2] << 10


def from_rgb555(words: np.ndarray) -> np.ndarray:
    w = np.asarray(words, np.uint16)
    return (np.stack([w & 31, (w >> 5) & 31, (w >> 10) & 31], -1) << 3).astype(np.uint8)


def _indices(first: bytes, mj3: bytes) -> np.ndarray:
    idx = np.zeros((H * 8, W * 8), np.uint8)
    for k, w in enumerate(struct.unpack_from(f">{W * H}H", first, MAP_OFF)):
        o = w * 32
        if not CELLS_START <= o <= CELLS_END - CELL:
            raise TitleError(f"title map word {k} points at {o:#x}, outside the cells")
        y, x = divmod(k, W)
        idx[y * 8:y * 8 + 8, x * 8:x * 8 + 8] = np.frombuffer(mj3[o:o + CELL], np.uint8).reshape(8, 8)
    return idx


def decode(first: bytes, mj3: bytes) -> np.ndarray:
    """The title screen as 240x352 RGB."""
    pal = from_rgb555(struct.unpack_from(">256H", first, PAL_OFF))
    return pal[_indices(first, mj3)]


def logo_box(img: np.ndarray) -> tuple:
    """(x0, y0, x1, y1), end exclusive: everything above LOGO_ROWS that is not background."""
    ys, xs = np.nonzero((img[:LOGO_ROWS] != img[0, 0]).any(-1))
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def fit(logo: Image.Image, box: tuple) -> tuple:
    """(x, y, image): the logo's visible part scaled to fit the box, centred in it."""
    logo = logo.convert("RGBA")
    bbox = logo.getchannel("A").getbbox()
    if bbox is None:
        raise TitleError("the logo image is empty (fully transparent)")
    logo = logo.crop(bbox)
    x0, y0, x1, y1 = box
    s = min((x1 - x0) / logo.width, (y1 - y0) / logo.height)
    w, h = max(1, round(logo.width * s)), max(1, round(logo.height * s))
    return x0 + (x1 - x0 - w) // 2, y0 + (y1 - y0 - h) // 2, logo.resize((w, h), Image.LANCZOS)


def build_files(first: bytes, mj3: bytes, logo: Image.Image) -> tuple[bytes, bytes]:
    """(1ST.BIN, MJDAT3.BIN) with the Korean logo; the protected bytes are checked."""
    old = decode(first, mj3)
    box = logo_box(old)
    x0, y0, x1, y1 = box
    canvas = old.copy()
    canvas[y0:y1, x0:x1] = old[0, 0]
    x, y, img = fit(logo, box)
    screen = Image.fromarray(canvas).convert("RGBA")
    screen.alpha_composite(img, (x, y))
    rgb5 = Image.fromarray(np.asarray(screen.convert("RGB")) & 0xF8)    # quantise what RGB555 can show
    q = rgb5.quantize(255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    idx = np.asarray(q, np.uint8) + 1
    pal = np.zeros((256, 3), np.uint8)
    qp = np.array(q.getpalette()[:255 * 3], np.uint8).reshape(-1, 3)
    pal[1:1 + len(qp)] = qp
    cells, words = {}, []
    for cy in range(H):
        for cx in range(W):
            c = idx[cy * 8:cy * 8 + 8, cx * 8:cx * 8 + 8].tobytes()
            if c not in cells:
                cells[c] = CELLS_START + CELL * len(cells)
            words.append(cells[c] // 32)
    room = (CELLS_END - CELLS_START) // CELL
    if len(cells) > room:
        raise TitleError(f"the title needs {len(cells)} cells, room for {room}")
    out_mj = bytearray(mj3)
    for c, o in cells.items():
        out_mj[o:o + CELL] = c
    out_first = bytearray(first)
    struct.pack_into(f">{W * H}H", out_first, MAP_OFF, *words)
    old_pal = struct.unpack_from(">256H", first, PAL_OFF)
    new_pal = [old_pal[0]] + [0x8000 | int(v) for v in to_rgb555(pal[1:], rounded=True)]
    struct.pack_into(">256H", out_first, PAL_OFF, *new_pal)
    out_first, out_mj = bytes(out_first), bytes(out_mj)
    check_protected(first, mj3, out_first, out_mj)
    return out_first, out_mj


def editable_first(size: int) -> np.ndarray:
    """Mask of the 1ST.BIN bytes this module may change: map 55 and palette 40."""
    mask = np.zeros(size, bool)
    mask[MAP_OFF:MAP_OFF + 2 * W * H] = True
    mask[PAL_OFF + 2:PAL_OFF + 512] = True
    return mask


def check_protected(first0: bytes, mj30: bytes, first: bytes, mj3: bytes) -> None:
    """Only the title map, palette (not colour 0) and cells differ; every map word is a cell."""
    if len(first0) != len(first) or len(mj30) != len(mj3):
        raise TitleError("protected: 1ST.BIN or MJDAT3.BIN size changed")
    diff = np.frombuffer(first0, np.uint8) != np.frombuffer(first, np.uint8)
    stray = np.flatnonzero(diff & ~editable_first(len(first0)))
    if len(stray):
        raise TitleError(f"protected: 1ST.BIN byte {int(stray[0]):#x} changed")
    if mj30[:CELLS_START] != mj3[:CELLS_START] or mj30[CELLS_END:] != mj3[CELLS_END:]:
        raise TitleError("protected: MJDAT3.BIN outside the title cells changed")
    if any(w >> 15 == 0 for w in struct.unpack_from(">256H", first, PAL_OFF)):
        raise TitleError("protected: title palette word without bit 15")
    _indices(first, mj3)
