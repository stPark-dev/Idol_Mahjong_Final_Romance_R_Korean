"""Profile (attract demo) screens: the five names and the six-line bodies, redrawn in Korean.

Body: NBG2, 256-colour cells (character base VDP2 0x60000). Each person's text box is a 16-bit
word map of map table entry 103-107 (18x19 cells, word + 0x14, data in SL.BIN loaded verbatim
at 0x06060000); its cells come from PROFDAT.BIN (loaded at 0x00200000) through the copy list
at SL.BIN 0x06060650 (one {source, size} piece per person, written from cell unit 0x14,
VDP2 0x60280; unit 0x14 is the empty cell). The text is white (230) with soft edges (231, 232)
on colour 0. The game types the map out cell by cell, so the redrawn map types the Korean.

Name: an 8bpp VDP1 picture of 144x32 per person in PROFDAT.BIN from 0x33900 (sprite records
at SL.BIN 0x060A9CE8, ids 0xE1-0xE5), coloured by colour entry 80 + person at VDP1's
256-colour bank 0x200: a lit face over a dark outline and drop shadow.
"""
import json
import struct
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from fontTools.ttLib import TTCollection
from PIL import Image, ImageDraw, ImageFont

from . import mjdeclare, shopmenu

FIRST_BASE = 0x06004000
SL_BASE = 0x06060000
LOAD = 0x20200000
MAP_TABLE = 0x06015124
BODY_MAPS = (103, 104, 105, 106, 107)
BODY_W, BODY_H, BODY_ADD = 18, 19, 0x14
PIECE_LIST = 0x06060650
EMPTY_UNIT = 0x14
NAME_RECORDS = 0x060A9CE8
NAME_ID, NAME_W, NAME_H = 0xE1, 144, 32
NAME_COLOURS = 80
COLOUR_TABLE = 0x0601576C
PEOPLE = ("ri", "mi", "ke", "ry", "as")
TEXT_INK_RAMP = (230, 231, 232)      # bright to dark; 0 is transparent
LINES, LINE_H, INDENT = 9, 16, 16
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc"
NAME_FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc"
FONT_INDEX = 1
FONT_PX = 15
SUPER = 4
EMPTY = bytes(64)


class ProfileError(ValueError):
    pass


@dataclass(frozen=True)
class Person:
    map_off: int                 # SL.BIN offset of the 18x19 word map
    piece: tuple                 # (PROFDAT offset, size) of the body cells
    name_off: int
    name_colours: tuple          # colours of the original name


@dataclass(frozen=True)
class Layout:
    people: tuple
    name_face: tuple
    name_side: tuple

    @staticmethod
    def read(first: bytes, sl: bytes, pd: bytes) -> "Layout":
        people, faces, sides = [], [], []
        for k, entry in enumerate(BODY_MAPS):
            w, h, add, src, stride = struct.unpack_from(">BBHIH", first, MAP_TABLE - FIRST_BASE + 12 * entry)
            off = src - SL_BASE
            if (w, h, add, stride) != (BODY_W, BODY_H, BODY_ADD, BODY_W) or not 0 <= off <= len(sl) - 2 * w * h:
                raise ProfileError(f"profile map {entry} is {w}x{h}+{add:#x} at {src:#x}")
            psrc, psize = struct.unpack_from(">II", sl, PIECE_LIST - SL_BASE + 8 * k)
            poff = psrc - LOAD
            if psize % 64 or not 0 <= poff <= len(pd) - psize or pd[poff:poff + 64] != EMPTY:
                raise ProfileError(f"profile {k}: body cells {psrc:#x}+{psize:#x}")
            sid, mode, nsrc = struct.unpack_from(">HHI", sl, NAME_RECORDS - SL_BASE + 8 * k)
            noff = nsrc - LOAD
            if (sid, mode) != (NAME_ID + k, 2) or not 0 <= noff <= len(pd) - NAME_W * NAME_H:
                raise ProfileError(f"profile {k}: name record {sid:#x} mode {mode} at {nsrc:#x}")
            lum = mjdeclare._palette(first, sl, NAME_COLOURS + k)
            px = np.frombuffer(pd[noff:noff + NAME_W * NAME_H], np.uint8)
            face, side = mjdeclare._ramps(px, lum)
            colours = tuple(sorted(set(px.tolist())))
            people.append(Person(off, (poff, psize), noff, colours))
            faces.append(face)
            sides.append(side)
        spans = sorted([(p.piece[0], p.piece[0] + p.piece[1]) for p in people]
                       + [(p.name_off, p.name_off + NAME_W * NAME_H) for p in people])
        if any(b0 < a1 for (_, a1), (b0, _) in zip(spans, spans[1:])):
            raise ProfileError("profile body cells and names overlap in PROFDAT.BIN")
        return Layout(tuple(people), tuple(faces), tuple(sides))


def _cell_off(p: Person, unit: int) -> int:
    poff, psize = p.piece
    o = (unit - EMPTY_UNIT) * 0x20
    if unit % 2 or not 0 <= o <= psize - 64:
        raise ProfileError(f"body cell unit {unit:#x} is outside the person's cells")
    return poff + o


def _word(p: Person, y: int, x: int) -> int:
    return p.map_off + 2 * (y * BODY_W + x)


def decode_body(lay: Layout, k: int, sl: bytes, pd: bytes) -> np.ndarray:
    """The text box of person k as the game would draw it (colour indices, 0 transparent)."""
    p = lay.people[k]
    img = np.zeros((BODY_H * 8, BODY_W * 8), np.uint8)
    for y in range(BODY_H):
        for x in range(BODY_W):
            u = struct.unpack_from(">H", sl, _word(p, y, x))[0] + BODY_ADD
            o = _cell_off(p, u)
            img[y * 8:y * 8 + 8, x * 8:x * 8 + 8] = np.frombuffer(pd[o:o + 64], np.uint8).reshape(8, 8)
    return img


@lru_cache(maxsize=None)
def _font(path: str, px: int):
    return ImageFont.truetype(path, px * SUPER, index=FONT_INDEX)


@lru_cache(maxsize=None)
def _cmap(path: str):
    return frozenset(TTCollection(path).fonts[FONT_INDEX].getBestCmap())


def _width(text: str) -> float:
    return _font(FONT, FONT_PX).getlength(text) / SUPER


def layout_body(body: list[str]) -> list[tuple[int, str]]:
    """(x, text) lines: the first five fields as given, the quote wrapped at spaces, its later
    lines indented as on the original; at most LINES lines."""
    if len(body) != 6:
        raise ProfileError(f"a profile body has 6 fields, got {len(body)}")
    width = BODY_W * 8 - 2
    lines = []
    for field in body[:5]:
        if _width(field) > width:
            raise ProfileError(f"{field!r} is wider than the {width} px text box")
        lines.append((0, field))
    words, cur, x = body[5].split(), "", 0
    for wd in words:
        trial = f"{cur} {wd}" if cur else wd
        if _width(trial) <= width - x:
            cur = trial
            continue
        if not cur:
            raise ProfileError(f"{wd!r} is wider than a quote line")
        lines.append((x, cur))
        cur, x = wd, INDENT
    if cur:
        lines.append((x, cur))
    if len(lines) > LINES:
        raise ProfileError(f"profile body needs {len(lines)} lines, the box has {LINES}")
    return lines


def render_body(lines: list[tuple[int, str]]) -> np.ndarray:
    """144x152 (w x h) text box: each line at 16 px pitch, white with soft edges on transparent."""
    w, h = BODY_W * 8, BODY_H * 8
    if any(c != " " and ord(c) not in _cmap(FONT) for _, t in lines for c in t):
        raise ProfileError("no glyph for a character of the body")
    im = Image.new("L", (w * SUPER, h * SUPER), 0)
    draw = ImageDraw.Draw(im)
    for i, (x, text) in enumerate(lines):
        draw.text(((x + 1) * SUPER, (i * LINE_H + 13) * SUPER), text, font=_font(FONT, FONT_PX), fill=255, anchor="ls")
    a = np.asarray(im.resize((w, h), Image.LANCZOS), dtype=np.float32) / 255
    out = np.zeros((h, w), np.uint8)
    out[a > 0.2] = TEXT_INK_RAMP[2]
    out[a > 0.45] = TEXT_INK_RAMP[1]
    out[a > 0.7] = TEXT_INK_RAMP[0]
    return out


def apply_bodies(lay: Layout, sl: bytes, pd: bytes, images: list[np.ndarray]) -> tuple[bytes, bytes]:
    """SL.BIN and PROFDAT.BIN with each person's text box replaced by the given image."""
    out_sl, out_pd = bytearray(sl), bytearray(pd)
    for p, img in zip(lay.people, images):
        if img.shape != (BODY_H * 8, BODY_W * 8):
            raise ProfileError(f"body image {img.shape}")
        cells = [bytes(img[y:y + 8, x:x + 8].flatten()) for y in range(0, BODY_H * 8, 8) for x in range(0, BODY_W * 8, 8)]
        pool = list(range(EMPTY_UNIT + 2, EMPTY_UNIT + p.piece[1] // 0x20, 2))
        try:
            (units,), writes = shopmenu.assign([cells], pool, [EMPTY_UNIT], [EMPTY])
        except shopmenu.ShopMenuError as ex:
            raise ProfileError(f"a profile body needs more than {len(pool)} cells") from ex
        for i, u in enumerate(units):
            y, x = divmod(i, BODY_W)
            struct.pack_into(">H", out_sl, _word(p, y, x), u - BODY_ADD)
        for u, c in writes.items():
            out_pd[_cell_off(p, u):_cell_off(p, u) + 64] = c
    return bytes(out_sl), bytes(out_pd)


def read_name(lay: Layout, k: int, pd: bytes) -> np.ndarray:
    o = lay.people[k].name_off
    return np.frombuffer(pd[o:o + NAME_W * NAME_H], np.uint8).reshape(NAME_H, NAME_W).copy()


def render_name(text: str, face: tuple, side: tuple) -> np.ndarray:
    """144x32 name on colour 0: a face lit from the top in the name's bright colours, a one-pixel
    outline and a drop shadow down-right in its dark colours."""
    if any(c != " " and ord(c) not in _cmap(NAME_FONT) for c in text):
        raise ProfileError(f"no glyph in {text!r}")
    for px in range(28, 9, -1):
        l, t, r, b = _font(NAME_FONT, px).getbbox(text)
        if (r - l) / SUPER + 6 <= NAME_W and (b - t) / SUPER + 6 <= NAME_H:
            break
    else:
        raise ProfileError(f"name {text!r} does not fit {NAME_W}x{NAME_H}")
    im = Image.new("L", (NAME_W * SUPER, NAME_H * SUPER), 0)
    ImageDraw.Draw(im).text((2 * SUPER - l, (NAME_H * SUPER - (b - t)) / 2 - t - SUPER), text, font=_font(NAME_FONT, px), fill=255)
    mask = np.asarray(im.resize((NAME_W, NAME_H), Image.LANCZOS)) > 127
    grown = mask.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            grown |= np.roll(np.roll(mask, dy, 0), dx, 1)
    shadow = np.zeros_like(grown)
    shadow[2:, 2:] = grown[:-2, :-2]
    out = np.zeros((NAME_H, NAME_W), np.uint8)
    out[shadow & ~grown] = side[min(1, len(side) - 1)]
    out[grown & ~mask] = side[-1]
    ys = np.flatnonzero(mask.any(1))
    for y in range(ys.min(), ys.max() + 1):
        out[y, mask[y]] = face[min(len(face) - 1, (y - ys.min()) * len(face) // (ys.max() - ys.min() + 1))]
    return out


def editable(lay: Layout, sl_size: int, pd_size: int) -> tuple[np.ndarray, np.ndarray]:
    """Masks of the SL.BIN and PROFDAT.BIN bytes this module may change."""
    sl_mask, pd_mask = np.zeros(sl_size, bool), np.zeros(pd_size, bool)
    for p in lay.people:
        sl_mask[p.map_off:p.map_off + 2 * BODY_W * BODY_H] = True
        pd_mask[p.piece[0] + 64:p.piece[0] + p.piece[1]] = True        # all but the empty cell
        pd_mask[p.name_off:p.name_off + NAME_W * NAME_H] = True
    return sl_mask, pd_mask


def kept_sl(sl_size: int) -> np.ndarray:
    """Mask of the SL.BIN bytes the profiles rely on: the body piece list and the name records."""
    mask = np.zeros(sl_size, bool)
    mask[PIECE_LIST - SL_BASE:PIECE_LIST - SL_BASE + 8 * len(PEOPLE)] = True
    mask[NAME_RECORDS - SL_BASE:NAME_RECORDS - SL_BASE + 8 * len(PEOPLE)] = True
    return mask


def check_protected(lay: Layout, sl0: bytes, pd0: bytes, sl1: bytes, pd1: bytes) -> None:
    """Only body maps, body cells and names differ, the maps stay inside each person's cells,
    and bodies and names use their own colours."""
    if len(sl0) != len(sl1) or len(pd0) != len(pd1):
        raise ProfileError("protected: file size changed")
    sl_mask, pd_mask = editable(lay, len(sl0), len(pd0))
    for name, a, b, m in (("SL.BIN", sl0, sl1, sl_mask), ("PROFDAT.BIN", pd0, pd1, pd_mask)):
        stray = np.flatnonzero((np.frombuffer(a, np.uint8) != np.frombuffer(b, np.uint8)) & ~m)
        if len(stray):
            raise ProfileError(f"protected: {name} byte {int(stray[0]):#x} changed")
    for k, p in enumerate(lay.people):
        body = decode_body(lay, k, sl1, pd1)                    # also checks every word's cell range
        if not set(np.unique(body).tolist()) <= {0, *TEXT_INK_RAMP}:
            raise ProfileError(f"protected: profile {k} body uses a colour that is not text")
        if not set(np.unique(read_name(lay, k, pd1)).tolist()) <= set(p.name_colours):
            raise ProfileError(f"protected: profile {k} name uses a colour the name did not use")


def load_texts(path) -> tuple[list[str], list[list[str]]]:
    """(names, bodies) for the five people from translation/graphics/profile.json."""
    try:
        entries = json.loads(Path(path).read_text(encoding="utf-8"))["entries"]
    except (OSError, ValueError, KeyError, TypeError) as ex:
        raise ProfileError(f"{path}: {ex}") from ex
    ids = [f"profile.{p}.{part}" for p in PEOPLE for part in ("name", "body")]
    if [e.get("id") if isinstance(e, dict) else None for e in entries] != ids:
        raise ProfileError(f"{path}: entry ids must be {ids} in that order")
    if not all(isinstance(entries[2 * k].get("ko"), str) and isinstance(entries[2 * k + 1].get("ko"), list)
               for k in range(len(PEOPLE))):
        raise ProfileError(f"{path}: every person needs a name string and six body lines")
    names = [entries[2 * k]["ko"].strip() for k in range(len(PEOPLE))]
    bodies = [[str(line).strip() for line in entries[2 * k + 1]["ko"]] for k in range(len(PEOPLE))]
    if not all(names) or not all(len(b) == 6 and all(b) for b in bodies):
        raise ProfileError(f"{path}: every person needs a name and six body lines")
    return names, bodies


def build_files(first: bytes, sl: bytes, pd: bytes, names: list[str], bodies: list[list[str]]) -> tuple[bytes, bytes]:
    """SL.BIN and PROFDAT.BIN with the five profiles redrawn; the protected bytes are checked."""
    lay = Layout.read(first, sl, pd)
    out_sl, out_pd = apply_bodies(lay, sl, pd, [render_body(layout_body(b)) for b in bodies])
    out_pd = bytearray(out_pd)
    for k, text in enumerate(names):
        o = lay.people[k].name_off
        out_pd[o:o + NAME_W * NAME_H] = render_name(text, lay.name_face[k], lay.name_side[k]).tobytes()
    out_pd = bytes(out_pd)
    check_protected(lay, sl, pd, out_sl, out_pd)
    return out_sl, out_pd
