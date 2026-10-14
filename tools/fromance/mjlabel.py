"""Mahjong 「ツモ」 label redrawn as 「쯔모」 (D-18).

While the player draws, NBG0 shows a 3x2-cell label at the lower right (16-colour cells,
units 0x08B-0x090, palette 2). The cells live in 1ST.BIN itself: the mahjong setup (0x0600EE38)
copies 1ST.BIN 0x0604B198 + 0x900 to VDP2 through its copy list (entry 0x0601732C), and the
label's six cells follow one another from 0x0604B318 (offset 0x47318), top row first.
The frame (colours 2, 3, 4) stays; the inside (18x12 px on colour 15) gets the word in
colour 5 with a one-pixel shadow to its left in colour 6, as the original.
"""
import json
from functools import lru_cache
from pathlib import Path

import numpy as np
from fontTools.ttLib import TTCollection
from PIL import Image, ImageDraw, ImageFont

CELLS = 0x47318                  # 1ST.BIN offset of the six label cells
W, H = 24, 16
FACE_X, FACE_Y, FACE_W, FACE_H = 3, 2, 18, 12
BG, INK, SHADOW = 15, 5, 6
COLOURS = (2, 3, 4, 5, 6, 15)
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc"
FONT_INDEX = 1
FONT_PX = 12
SUPER = 4
ID = "mj.label.tsumo"


class MjLabelError(ValueError):
    pass


def read_label(first: bytes) -> np.ndarray:
    """The 24x16 label as colour indices."""
    img = np.zeros((H, W), np.uint8)
    for k in range(6):
        r, c = divmod(k, 3)
        b = np.frombuffer(first[CELLS + 32 * k:CELLS + 32 * k + 32], np.uint8)
        img[r * 8:r * 8 + 8, c * 8:c * 8 + 8] = np.stack([b >> 4, b & 15], 1).reshape(8, 8)
    return img


def _write(first: bytes, img: np.ndarray) -> bytes:
    out = bytearray(first)
    for k in range(6):
        r, c = divmod(k, 3)
        cell = img[r * 8:r * 8 + 8, c * 8:c * 8 + 8]
        out[CELLS + 32 * k:CELLS + 32 * k + 32] = bytes((cell[:, 0::2] << 4 | cell[:, 1::2]).astype(np.uint8).flatten())
    return bytes(out)


@lru_cache(maxsize=None)
def _font():
    return ImageFont.truetype(FONT, FONT_PX * SUPER, index=FONT_INDEX), frozenset(TTCollection(FONT).fonts[FONT_INDEX].getBestCmap())


def render_face(text: str) -> np.ndarray:
    """FACE_H x FACE_W inside of the label: the word at full height, narrowed to the width,
    in INK with its shadow one pixel to the left."""
    font, cmap = _font()
    if any(c != " " and ord(c) not in cmap for c in text):
        raise MjLabelError(f"no glyph in {text!r}")
    l, t, r, b = font.getbbox(text)
    if (b - t) / SUPER > FACE_H or (r - l) / SUPER > 1.5 * FACE_W:
        raise MjLabelError(f"{text!r} does not fit the {FACE_W}x{FACE_H} label")
    wide = int(np.ceil((r - l) / SUPER)) + 1          # drawn at full height, then narrowed to the label
    im = Image.new("L", (wide * SUPER, FACE_H * SUPER), 0)
    ImageDraw.Draw(im).text((SUPER - l, (FACE_H * SUPER - (b - t)) / 2 - t), text, font=font, fill=255)
    ink = np.asarray(im.resize((FACE_W - 1, FACE_H), Image.LANCZOS)) > 127
    ink = np.pad(ink, ((0, 0), (1, 0)))                 # room for the shadow on the left
    out = np.full((FACE_H, FACE_W), BG, np.uint8)
    shadow = np.zeros_like(ink)
    shadow[:, :-1] = ink[:, 1:]
    out[shadow & ~ink] = SHADOW
    out[ink] = INK
    return out


def editable(size: int) -> np.ndarray:
    """Mask of the 1ST.BIN bytes this module may change (the six label cells)."""
    mask = np.zeros(size, bool)
    mask[CELLS:CELLS + 6 * 32] = True
    return mask


def check_protected(before: bytes, after: bytes) -> None:
    """Only the label cells differ, the frame is untouched and only the label's colours are used."""
    if len(before) != len(after):
        raise MjLabelError("protected: 1ST.BIN size changed")
    diff = np.frombuffer(before, np.uint8) != np.frombuffer(after, np.uint8)
    stray = np.flatnonzero(diff & ~editable(len(before)))
    if len(stray):
        raise MjLabelError(f"protected: 1ST.BIN byte {int(stray[0]):#x} changed")
    old, new = read_label(before), read_label(after)
    frame = np.ones(old.shape, bool)
    frame[FACE_Y:FACE_Y + FACE_H, FACE_X:FACE_X + FACE_W] = False
    if (old[frame] != new[frame]).any() or not set(np.unique(new).tolist()) <= set(COLOURS):
        raise MjLabelError("protected: label frame or colours changed")


def load_text(path) -> str:
    entries = json.loads(Path(path).read_text(encoding="utf-8"))["entries"]
    if [e.get("id") for e in entries] != [ID] or not isinstance(entries[0].get("ko"), str):
        raise MjLabelError(f"{path}: need one entry with id {ID} and a Korean word")
    return entries[0]["ko"].strip()


def build_file(first: bytes, text: str) -> bytes:
    """1ST.BIN with the label's inside redrawn; the protected bytes are checked."""
    old = read_label(first)
    if not set(np.unique(old).tolist()) <= set(COLOURS):
        raise MjLabelError("the tsumo label cells hold unexpected colours")
    img = old.copy()
    img[FACE_Y:FACE_Y + FACE_H, FACE_X:FACE_X + FACE_W] = render_face(text)
    out = _write(first, img)
    check_protected(first, out)
    return out
