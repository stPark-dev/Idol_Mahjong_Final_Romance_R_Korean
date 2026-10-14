"""Mahjong big declarations (カン ポン チー リーチ ロン ツモ) redrawn as 깡 퐁 치 리치 론 쯔모 (D-18, D-19).

The declaration routine 0x06014C90 takes a kind 0-5 and shows the frames of that kind:
1ST.BIN 0x06016308 holds (first record, count) per kind into the sprite table at SL.BIN
0x06060070 ({u16 id, u16 mode, u32 source}, sources inside SL.BIN loaded verbatim at
0x06060000), and 0x06016318 the colour entry (table 0x0601576C, colours in 1ST.BIN) put at VDP1's 256-colour bank.
Frame sizes come from the SGL texture table 0x06015AF4. Each frame is an 8bpp picture of
extruded 3D letters on colour 0 (transparent); the frames of a kind grow as the word flies in.

Every frame is redrawn whole: the Korean word fills the original lettering box, its face in
the frame's bright colours lit from the top, extruded to the lower right in its dark colours.
The perspective of some original frames is not reproduced.
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
TEX_TABLE = 0x06015AF4
COLOUR_TABLE = 0x0601576C
KIND_RECORDS = 0x06016308        # (first record, count) per kind
KIND_COLOURS = 0x06016318        # colour entry per kind
RECORD_TABLE = 0x06060070
KINDS = 6
IDS = ("mj.declare.kan", "mj.declare.pon", "mj.declare.chi", "mj.declare.riichi", "mj.declare.ron",
       "mj.declare.tsumo")
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc"
FONT_INDEX = 1
SUPER = 4
DEPTH = 0.1                      # extrusion depth as a share of the frame height


class MjDeclareError(ValueError):
    pass


@dataclass(frozen=True)
class Frame:
    kind: int
    off: int                     # SL.BIN offset of the 8bpp picture
    w: int
    h: int
    face: tuple                  # colours for the letter face, bright to dark
    side: tuple                  # colours for the extrusion, bright to dark


def _palette(first: bytes, sl: bytes, entry: int) -> np.ndarray:
    src, count = struct.unpack_from(">IH", first, COLOUR_TABLE - FIRST_BASE + 8 * entry)
    for data, base in ((first, FIRST_BASE), (sl, SL_BASE)):
        if 0 <= src - base <= len(data) - 2 * count:
            cols = struct.unpack_from(f">{count}H", data, src - base)
            break
    else:
        raise MjDeclareError(f"colour entry {entry} at {src:#x} is outside 1ST.BIN and SL.BIN")
    lum = [0.3 * (c & 31) + 0.59 * ((c >> 5) & 31) + 0.11 * ((c >> 10) & 31) for c in cols]
    return np.array(lum + [0.0] * (256 - count))


def _ramps(px: np.ndarray, lum: np.ndarray) -> tuple[tuple, tuple]:
    """Face and extrusion colours of a frame: among its brighter half and its darkest third of
    colours, those covering most of the pixels (rare highlight and glow colours left out),
    each bright to dark."""
    counts = np.bincount(px[px != 0], minlength=256)
    used = sorted((c for c in range(1, 256) if counts[c]), key=lambda c: -lum[c])
    n = len(used)

    def main(colours):
        colours = sorted(colours, key=lambda c: -counts[c])
        total, kept, acc = sum(counts[c] for c in colours), [], 0
        for c in colours:
            if total and acc >= 0.9 * total and len(kept) >= 3:
                break
            kept.append(c)
            acc += counts[c]
        return tuple(sorted(kept, key=lambda c: -lum[c]))

    return main(used[:n // 2]), main(used[n - max(2, n // 3):])


def read_frames(first: bytes, sl: bytes) -> list[list[Frame]]:
    """Frames of every kind with the colours they use, split into face and extrusion."""
    out = []
    for kind in range(KINDS):
        start, count = first[KIND_RECORDS - FIRST_BASE + 2 * kind], first[KIND_RECORDS - FIRST_BASE + 2 * kind + 1]
        lum = _palette(first, sl, first[KIND_COLOURS - FIRST_BASE + kind])
        frames = []
        for r in range(start, start + count):
            sid, mode, src = struct.unpack_from(">HHI", sl, RECORD_TABLE - SL_BASE + 8 * r)
            w, h = struct.unpack_from(">HH", first, TEX_TABLE - FIRST_BASE + 8 * sid)
            off = src - SL_BASE
            if mode != 2 or not 0 <= off <= len(sl) - w * h or not 16 <= w <= 512 or not 16 <= h <= 256:
                raise MjDeclareError(f"kind {kind} record {r}: id {sid:#x} mode {mode} {w}x{h} at {src:#x}")
            face, side = _ramps(np.frombuffer(sl[off:off + w * h], np.uint8), lum)
            if len(face) < 2 or len(side) < 2:
                raise MjDeclareError(f"kind {kind} record {r}: too few colours for face and extrusion")
            frames.append(Frame(kind, off, w, h, face, side))
        out.append(frames)
    return out


@lru_cache(maxsize=None)
def _font(px: int):
    return ImageFont.truetype(FONT, px * SUPER, index=FONT_INDEX)


@lru_cache(maxsize=None)
def _cmap():
    return frozenset(TTCollection(FONT).fonts[FONT_INDEX].getBestCmap())


def _face_mask(text: str, w: int, h: int) -> np.ndarray:
    """Largest rendering of text that fits a w x h box, centred, as a boolean mask."""
    best = None
    for px in range(8, 4 * h):
        l, t, r, b = _font(px).getbbox(text)
        if (r - l) / SUPER > w or (b - t) / SUPER > h:
            break
        best = (px, l, t, r, b)
    if best is None:
        raise MjDeclareError(f"{text!r} does not fit {w}x{h}")
    px, l, t, r, b = best
    im = Image.new("L", (w * SUPER, h * SUPER), 0)
    ImageDraw.Draw(im).text(((w * SUPER - (r - l)) / 2 - l, (h * SUPER - (b - t)) / 2 - t), text, font=_font(px), fill=255)
    return np.asarray(im.resize((w, h), Image.LANCZOS)) > 127


def render_banner(text: str, w: int, h: int, face: list, side: list) -> np.ndarray:
    """h x w picture on colour 0: the word's face lit from the top (face colours, bright to
    dark by row) over an extrusion to the lower right (side colours, near to far)."""
    if any(c != " " and ord(c) not in _cmap() for c in text):
        raise MjDeclareError(f"no glyph in {text!r}")
    if not text.strip():
        raise MjDeclareError("empty word")
    depth = max(2, round(h * DEPTH))
    box = _face_mask(text, w - depth - 2, h - depth - 2)
    mask = np.zeros((h, w), bool)
    mask[1:1 + box.shape[0], 1:1 + box.shape[1]] = box
    out = np.zeros((h, w), np.uint8)
    for s in range(depth, 0, -1):
        shifted = np.zeros_like(mask)
        shifted[s:, s:] = mask[:-s, :-s]
        out[shifted] = side[min(len(side) - 1, (s - 1) * len(side) // depth)]
    if not mask.any():
        raise MjDeclareError(f"{text!r} draws nothing (empty word)")
    ys = np.nonzero(mask.any(1))[0]
    top, bottom = ys.min(), ys.max()
    for y in range(top, bottom + 1):
        out[y, mask[y]] = face[min(len(face) - 1, (y - top) * len(face) // (bottom - top + 1))]
    return out


def editable(frames: list[list[Frame]], sl_size: int) -> np.ndarray:
    """Mask of the SL.BIN bytes this module may change (every declaration frame)."""
    mask = np.zeros(sl_size, bool)
    for fr in (f for kind in frames for f in kind):
        mask[fr.off:fr.off + fr.w * fr.h] = True
    return mask


def kept(first: bytes, sl_size: int) -> np.ndarray:
    """Mask of the SL.BIN bytes the frames rely on: their sprite records."""
    mask = np.zeros(sl_size, bool)
    for kind in range(KINDS):
        start, count = first[KIND_RECORDS - FIRST_BASE + 2 * kind], first[KIND_RECORDS - FIRST_BASE + 2 * kind + 1]
        o = RECORD_TABLE - SL_BASE + 8 * start
        mask[o:o + 8 * count] = True
    return mask


def check_protected(frames: list[list[Frame]], before: bytes, after: bytes) -> None:
    """Only frame bytes differ, and each frame uses its own colours."""
    if len(before) != len(after):
        raise MjDeclareError("protected: SL.BIN size changed")
    diff = np.frombuffer(before, np.uint8) != np.frombuffer(after, np.uint8)
    stray = np.flatnonzero(diff & ~editable(frames, len(before)))
    if len(stray):
        raise MjDeclareError(f"protected: SL.BIN byte {int(stray[0]):#x} changed")
    for fr in (f for kind in frames for f in kind):
        if not set(after[fr.off:fr.off + fr.w * fr.h]) <= {0, *fr.face, *fr.side}:
            raise MjDeclareError(f"protected: frame at {fr.off:#x} uses a colour of another frame")


def load_texts(path) -> list[str]:
    """Korean words in kind order from translation/graphics/mj_declare.json."""
    entries = json.loads(Path(path).read_text(encoding="utf-8"))["entries"]
    if [e.get("id") for e in entries] != list(IDS):
        raise MjDeclareError(f"{path}: entry ids must be {list(IDS)} in that order")
    texts = [e["ko"].strip() for e in entries]
    if not all(texts):
        raise MjDeclareError(f"{path}: empty word")
    return texts


def build_file(first: bytes, sl: bytes, texts: list[str], base_sl: bytes = None) -> bytes:
    """SL.BIN with every declaration frame redrawn; the protected bytes are checked. Frames
    and colours are read from base_sl (the original SL.BIN) when given."""
    frames = read_frames(first, sl if base_sl is None else base_sl)
    out = bytearray(sl)
    for kind, text in zip(frames, texts):
        for fr in kind:
            out[fr.off:fr.off + fr.w * fr.h] = render_banner(text, fr.w, fr.h, list(fr.face), list(fr.side)).tobytes()
    out = bytes(out)
    check_protected(frames, sl, out)
    return out
