"""Mahjong exhaustive draw words: 流局 / テンパイ / ノーテン redrawn as 유국 / 텐파이 / 노텐 (D-18, D-19).

Declaration kind 6 of 0x06014C8C shows them on NBG2 (256-colour cells, character base VDP2
0x60000): it copies the cells from 1ST.BIN itself through the copy list at 0x06017494
(5 x {source, size}, written contiguously from VDP2 0x60040) and loads colour entry
[0x06016318 + 6] at CRAM colour 0x600. The words are byte maps 1-5 of the map table 0x06015124
(cell unit = byte + add, an 8bpp cell is two units): 1 流局, 2/3 big テンパイ/ノーテン,
4/5 small テンパイ/ノーテン, map data also inside 1ST.BIN.

Each map is redrawn whole in the style of the big declarations (mjdeclare.render_banner, the
map's own colours) and cut into cells; equal cells share a unit and empty cells keep the map's
empty cell. Maps 0 and 6 reuse some of these unit numbers, but they are 16-colour maps of
other screens drawn from a different cell load (odd units), never shown with kind 6.
The words' cells form one pool: a map takes its own units first, then free units of
the other maps that its bytes can reach (add .. add + 255); no unit serves two maps.
"""
import json
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import mjdeclare, shopmenu

FIRST_BASE = 0x06004000
MAP_TABLE = 0x06015124
MAPS = (1, 2, 3, 4, 5)
WORDS = (0, 1, 2, 1, 2)          # text index of each map: 유국, 텐파이, 노텐
CELL_LIST = 0x06017494
PIECES = 5
CELL_VRAM = 0x60040
CHAR_BASE = 0x60000
DRAW_KIND = 6
IDS = ("mj.draw.ryuukyoku", "mj.draw.tenpai", "mj.draw.noten")
EMPTY = bytes(64)


class MjDrawError(ValueError):
    pass


@dataclass(frozen=True)
class Map:
    entry: int
    word: int
    src: int                     # 1ST.BIN offset of the byte map
    w: int
    h: int
    add: int
    pool: tuple                  # cell units the map uses (first unit of each 8bpp cell)
    empty: int                   # unit of the map's empty cell, or -1
    face: tuple
    side: tuple
    colours: frozenset           # every colour of the original word


@dataclass(frozen=True)
class Layout:
    pieces: tuple                # (VRAM start, 1ST.BIN offset, size)
    maps: tuple

    @staticmethod
    def read(first: bytes) -> "Layout":
        """The draw words' maps, cells and colours inside 1ST.BIN."""
        pieces, v = [], CELL_VRAM
        for k in range(PIECES):
            src, size = struct.unpack_from(">II", first, CELL_LIST - FIRST_BASE + 8 * k)
            o = src - FIRST_BASE
            if size % 64 or not 0 <= o <= len(first) - size:
                raise MjDrawError(f"draw cell piece {k}: {src:#x}+{size:#x} outside 1ST.BIN")
            pieces.append((v, o, size))
            v += size
        lay = Layout(tuple(pieces), ())
        entry = first[mjdeclare.KIND_COLOURS - FIRST_BASE + DRAW_KIND]
        lum = mjdeclare._palette(first, b"", entry)
        maps = []
        for i, word in zip(MAPS, WORDS):
            w, h, add, src, stride = struct.unpack_from(">BBHIH", first, MAP_TABLE - FIRST_BASE + 12 * i)
            o = src - FIRST_BASE
            if stride != w or not 0 <= o <= len(first) - w * h:
                raise MjDrawError(f"draw map {i}: {w}x{h}/{stride} at {src:#x}")
            units = [b + add for b in first[o:o + w * h]]
            pool = tuple(sorted(set(units)))
            if any(u % 2 for u in pool):
                raise MjDrawError(f"draw map {i}: odd cell unit")
            empty = next((u for u in pool if _cell(lay, first, u) == EMPTY), -1)
            img = _decode(lay, first, units, w, h)
            face, side = mjdeclare._ramps(img.flatten(), lum)
            maps.append(Map(i, word, o, w, h, add, pool, empty, face, side, frozenset(np.unique(img).tolist())))
        pools = [set(m.pool) for m in maps]
        if any(a & b for k, a in enumerate(pools) for b in pools[k + 1:]):
            raise MjDrawError("draw maps share cells")
        return Layout(tuple(pieces), tuple(maps))


def cell_offset(lay: Layout, unit: int) -> int:
    """1ST.BIN offset of an NBG2 cell unit of the draw words."""
    a = CHAR_BASE + unit * 0x20
    for v, o, size in lay.pieces:
        if v <= a and a + 64 <= v + size:
            return o + a - v
    raise MjDrawError(f"cell unit {unit:#x} is not in the draw cell pieces")


def _cell(lay: Layout, first: bytes, unit: int) -> bytes:
    o = cell_offset(lay, unit)
    return first[o:o + 64]


def _decode(lay: Layout, first: bytes, units: list[int], w: int, h: int) -> np.ndarray:
    img = np.zeros((h * 8, w * 8), np.uint8)
    for k, u in enumerate(units):
        y, x = divmod(k, w)
        img[y * 8:y * 8 + 8, x * 8:x * 8 + 8] = np.frombuffer(_cell(lay, first, u), np.uint8).reshape(8, 8)
    return img


def decode_maps(lay: Layout, first: bytes) -> list[np.ndarray]:
    """The five words as the game would draw them (colour index images)."""
    return [_decode(lay, first, [b + m.add for b in first[m.src:m.src + m.w * m.h]], m.w, m.h) for m in lay.maps]


def apply(lay: Layout, first: bytes, images: list[np.ndarray]) -> bytes:
    """1ST.BIN with the five maps replaced by the given images."""
    out = bytearray(first)
    taken: set[int] = set()
    spare = sorted(u for m in lay.maps for u in m.pool if u != m.empty)
    for m, img in zip(lay.maps, images):
        if img.shape != (m.h * 8, m.w * 8):
            raise MjDrawError(f"draw map {m.entry}: image {img.shape}, map is {m.h * 8}x{m.w * 8}")
        cells = [bytes(img[y:y + 8, x:x + 8].flatten()) for y in range(0, m.h * 8, 8) for x in range(0, m.w * 8, 8)]
        own = [u for u in m.pool if u != m.empty and u not in taken]
        borrowed = [u for u in spare if u not in taken and u not in own and m.add <= u <= m.add + 255]
        bg_unit, bg_cell = (m.empty, EMPTY) if m.empty >= 0 else (-1, None)
        try:
            (units,), writes = shopmenu.assign([cells], own + borrowed, [bg_unit], [bg_cell])
        except shopmenu.ShopMenuError as ex:
            raise MjDrawError(f"draw map {m.entry} needs more than {len(own + borrowed)} cells") from ex
        taken |= set(writes)
        for k, u in enumerate(units):
            out[m.src + k] = u - m.add
        for u, c in writes.items():
            o = cell_offset(lay, u)
            out[o:o + 64] = c
    return bytes(out)


def editable(lay: Layout, size: int) -> np.ndarray:
    """Mask of the 1ST.BIN bytes this module may change: the five maps and their cells."""
    mask = np.zeros(size, bool)
    for m in lay.maps:
        mask[m.src:m.src + m.w * m.h] = True
        for u in m.pool:
            mask[cell_offset(lay, u):cell_offset(lay, u) + 64] = True
    return mask


def check_protected(lay: Layout, before: bytes, after: bytes) -> None:
    """Only map bytes and their cells differ, every map byte points at its own cells, and each
    word uses its own colours."""
    if len(before) != len(after):
        raise MjDrawError("protected: 1ST.BIN size changed")
    diff = np.frombuffer(before, np.uint8) != np.frombuffer(after, np.uint8)
    stray = np.flatnonzero(diff & ~editable(lay, len(before)))
    if len(stray):
        raise MjDrawError(f"protected: 1ST.BIN byte {int(stray[0]):#x} changed")
    pool = {u for m in lay.maps for u in m.pool}
    owner: dict[int, int] = {}
    for m in lay.maps:
        for u in {b + m.add for b in after[m.src:m.src + m.w * m.h]}:
            if u not in pool:
                raise MjDrawError(f"protected: draw map {m.entry} points outside the draw cells")
            if u != m.empty and owner.setdefault(u, m.entry) != m.entry:
                raise MjDrawError(f"protected: cell {u:#x} serves draw maps {owner[u]} and {m.entry}")
    for m, img in zip(lay.maps, decode_maps(lay, after)):
        if not set(np.unique(img).tolist()) <= set(m.colours):
            raise MjDrawError(f"protected: draw map {m.entry} uses a colour it did not use")


def load_texts(path) -> list[str]:
    """Korean words (유국, 텐파이, 노텐) from translation/graphics/mj_draw.json."""
    try:
        entries = json.loads(Path(path).read_text(encoding="utf-8"))["entries"]
    except (OSError, ValueError, KeyError, TypeError) as ex:
        raise MjDrawError(f"{path}: {ex}") from ex
    if [e.get("id") if isinstance(e, dict) else None for e in entries] != list(IDS):
        raise MjDrawError(f"{path}: entry ids must be {list(IDS)} in that order")
    if not all(isinstance(e.get("ko"), str) for e in entries):
        raise MjDrawError(f"{path}: every entry needs a \"ko\" string")
    texts = [e["ko"].strip() for e in entries]
    if not all(texts):
        raise MjDrawError(f"{path}: empty word")
    return texts


def build_file(first: bytes, texts: list[str]) -> bytes:
    """1ST.BIN with the draw words redrawn; the protected bytes are checked."""
    lay = Layout.read(first)
    images = [mjdeclare.render_banner(texts[m.word], m.w * 8, m.h * 8, list(m.face), list(m.side)) for m in lay.maps]
    out = apply(lay, first, images)
    check_protected(lay, first, out)
    return out
