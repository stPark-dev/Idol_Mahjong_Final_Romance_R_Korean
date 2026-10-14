"""Shop owned-item mark of item 1: the 16x16 「交」 drawn next to the price after buying the
exchange sticker, replaced by a ↔ arrow icon (D-16).

The mark is an 8bpp VDP1 sprite; SL.BIN (loaded verbatim at 0x06060000) lists it as record 1
of the shop sprite table at 0x060A9A40 ({u16 id, u16 mode, u32 source}), source SHOPDAT.BIN
offset 0x80 (loaded at 0x20200000). It uses six colours: background 157, face 140 with a
lighter top 139, and 141 -> 155 -> 156 for the darkening lower edge and drop shadow.
"""
import struct

import numpy as np

SL_BASE = 0x06060000
SPRITE_TABLE = 0x060A9A40
MARK_RECORD = 1
RECORDS = 23                     # 0x06018180 loads records 0-22 (0x0600FC50(table, 23))
MARK_ID, MARK_MODE = 0x8D, 2     # record 1: id 0x8d, 8bpp
SHOP_LOAD = 0x20200000
SIZE = 16
LIGHT, FILL, SHADE, SHADOW, DEEP, BG = 139, 140, 141, 155, 156, 157
COLOURS = (LIGHT, FILL, SHADE, SHADOW, DEEP, BG)


class ShopMarkError(ValueError):
    pass


def mark_offset(sl: bytes, shop_size: int) -> int:
    """SHOPDAT.BIN offset of the item 1 mark, from the SL.BIN sprite table."""
    if len(sl) < SPRITE_TABLE - SL_BASE + 8 * RECORDS:
        raise ShopMarkError("SL.BIN ends inside the sprite table")
    rec = [struct.unpack_from(">HHI", sl, SPRITE_TABLE - SL_BASE + 8 * k) for k in range(RECORDS)]
    sid, mode, src = rec[MARK_RECORD]
    if (sid, mode) != (MARK_ID, MARK_MODE):
        raise ShopMarkError(f"sprite record {MARK_RECORD} is id {sid:#x} mode {mode}, expected the 8bpp mark")
    off = src - SHOP_LOAD
    if not 0 <= off <= shop_size - SIZE * SIZE:
        raise ShopMarkError(f"owned mark at {src:#x} is outside SHOPDAT.BIN")
    for k, (_, _, s) in enumerate(rec):
        if k != MARK_RECORD and off < s - SHOP_LOAD < off + SIZE * SIZE:
            raise ShopMarkError(f"sprite record {k} at {s:#x} overlaps the owned mark")
    return off


def arrow_icon() -> np.ndarray:
    """16x16 ↔: a 2 px shaft (rows 7-8) between two arrow heads (rows 3-12), top edge lit,
    lower edge shaded and a drop shadow down-right, as on the original mark."""
    body = np.zeros((SIZE, SIZE), bool)
    body[7:9, 4:12] = True
    for d in range(5):                       # heads: tip at columns 1 and 14
        body[7 - d:9 + d, 1 + d] = True
        body[7 - d:9 + d, 14 - d] = True
    icon = np.full((SIZE, SIZE), BG, np.uint8)
    icon[body] = FILL
    above = np.zeros_like(body); above[1:] = body[:-1]
    below = np.zeros_like(body); below[:-1] = body[1:]
    icon[body & ~above] = LIGHT
    icon[body & ~below] = SHADE
    drop = np.zeros_like(body); drop[1:, 1:] = body[:-1, :-1]
    drop2 = np.zeros_like(body); drop2[2:, 2:] = body[:-2, :-2]
    icon[drop2 & ~body & ~drop] = DEEP
    icon[drop & ~body] = SHADOW
    return icon


def editable(off: int, shop_size: int) -> np.ndarray:
    """Mask of the SHOPDAT.BIN bytes this module may change (the 256 mark pixels)."""
    mask = np.zeros(shop_size, bool)
    mask[off:off + SIZE * SIZE] = True
    return mask


def kept(sl_size: int, shop_size: int) -> tuple[np.ndarray, np.ndarray]:
    """Masks of the bytes the mark relies on: the sprite table records the shop loads."""
    sl_mask = np.zeros(sl_size, bool)
    o = SPRITE_TABLE - SL_BASE
    sl_mask[o:o + 8 * RECORDS] = True
    return sl_mask, np.zeros(shop_size, bool)


def check_colours(off: int, shop: bytes) -> None:
    """Every mark pixel is one of the mark's six colours."""
    px = np.frombuffer(shop[off:off + SIZE * SIZE], np.uint8)
    if not np.isin(px, COLOURS).all():
        raise ShopMarkError(f"protected: owned mark pixel outside its colours at {off:#x}")


def build_file(sl: bytes, shop: bytes) -> bytes:
    """SHOPDAT.BIN with the item 1 mark replaced by the arrow icon."""
    off = mark_offset(sl, len(shop))
    if not np.isin(np.frombuffer(shop[off:off + SIZE * SIZE], np.uint8), COLOURS).all():
        raise ShopMarkError(f"owned mark at {off:#x} has unexpected colours")
    out = shop[:off] + arrow_icon().tobytes() + shop[off + SIZE * SIZE:]
    check_colours(off, out)
    return out
