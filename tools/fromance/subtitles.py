"""Movie subtitles: the subtitle packs ZSUB0-4.DAT read by SUB.BIN, and the 1ST.BIN hooks.

One pack per row of the game's movie table (one character each); a movie sequence only plays
movies of one row, so SUB.BIN loads just that row's pack.
Pack layout read by tools/subtitle/sub.c (big-endian, every field 4-byte aligned); the whole
file is loaded into VDP1 VRAM at PACK_VRAM, so srca/LUT addresses are absolute VRAM units:
  u32 MAGIC, u16 nmovies, u16 lut_srca, u32 total size,
  nmovies x {char movie[4], u32 cue offset, u16 ncues, u16 0},
  cues {s32 start, s32 end (1/600 s movie clock), u16 srca, u16 size, s16 x, s16 y},
  16 x u16 colour lookup table (32-byte aligned), textures (each 32-byte aligned).
"""
import json
import struct
from functools import lru_cache
from pathlib import Path

import numpy as np
from fontTools.ttLib import TTCollection
from PIL import Image, ImageDraw, ImageFont

FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc"
FONT_INDEX = 1                 # Noto Sans CJK KR (also covers kana/kanji for the test disc)
FONT_PX = 20
ROW_H = 24
MAX_W = 320                    # texture width limit (movie is 320 px wide)
TEXT_W = 304
MAX_ROWS = 2
BOTTOM_Y = 108                 # movie spans y -112..111 around the VDP1 local origin
SUPER = 4

MAGIC = 0x46525331             # "FRS1"
ROWS = 5                       # character rows of the movie table
PACK_NAME = "ZSUB{}.DAT"       # sorts after every original file, so GFS file ids stay put
PACK_VRAM = 0x40000            # VDP1 VRAM offset; movies use 0x10000-0x33000
VRAM_LIMIT = 0x7F000           # SGL keeps data at 0x7FF00
MAX_CUES = 24
TICKS = 600                    # CPK clock units per second
LUT = [0x0000, 0x8000, 0xC210, 0xFFFF] + [0x8000] * 12   # clear, black outline, grey, white

FIRST_BASE = 0x06004000
FIRST_SIZE = 344184
FIRST_SLACK = 1928
SUB_BASE = FIRST_BASE + FIRST_SIZE                       # 0x06058078
ENTRY_PLAY, ENTRY_FRAME, ENTRY_PREPARE = 0x00, 0x08, 0x18  # tools/subtitle/crt.S
HOOKS = (                                                # (literal, original value, SUB.BIN entry)
    (0x0600F504, 0x0600F50C, ENTRY_PLAY),                # play-movie call in 0x0600F4AC
    (0x0600F5E4, 0x06004052, ENTRY_FRAME),               # slSynch call in the movie loop 0x0600F50C
    (0x0600F3E0, 0x0600F604, ENTRY_PREPARE),             # stream init of the movie sequence 0x0600F360
)
SEQUENCE_VARS = {                                        # byte address, its literals in 0x0600F360..0x0600F4AC
    "MODE": (0x060D57BC, (0x0600F3EC,)),                 # sequence kind
    "WIN_STEP": (0x060D75DE, (0x0600F440,)),             # kind 0: movie after a win
    "CHAR": (0x060D75E0, (0x0600F444, 0x0600F484)),      # kinds 0, 2: table row
    "ALBUM_CHAR": (0x060D00A7, (0x0600F4A4,)),           # kind 3: table row + 1
}
MOVIE_TABLE = 0x0600F964                                 # rows x 6 x {char *name, u32 flag}
SIZE_MARKER = b"FRPACKSZ"


class SubtitleError(ValueError):
    pass


@lru_cache(maxsize=None)
def _cmap(path: str) -> frozenset:
    return frozenset(TTCollection(path).fonts[FONT_INDEX].getBestCmap())


@lru_cache(maxsize=None)
def _fonts(path: str):
    return ImageFont.truetype(path, FONT_PX, index=FONT_INDEX), ImageFont.truetype(path, FONT_PX * SUPER, index=FONT_INDEX)


def _wrap(text: str, font) -> list[str]:
    """One row if it fits; otherwise two rows, cut after a space or punctuation if possible
    (else between any two characters), as evenly as possible."""
    fits = lambda r: font.getlength(r) + 2 <= TEXT_W
    if fits(text):
        return [text]
    rows = lambda i: [text[:i].rstrip(), text[i:].lstrip()]
    ok = [i for i in range(1, len(text)) if all(fits(r) and r for r in rows(i))]
    if not ok:
        raise SubtitleError(f"text needs more than {MAX_ROWS} rows: {text}")
    soft = [i for i in ok if text[i - 1] in " 、。,!?！？…"]
    best = min(soft or ok, key=lambda i: abs(font.getlength(rows(i)[0]) - font.getlength(rows(i)[1])))
    return rows(best)


def _render_row(row: str, small, big) -> np.ndarray:
    w = int(np.ceil(small.getlength(row))) + 4
    w = (w + 15) // 16 * 16
    im = Image.new("L", (w * SUPER, ROW_H * SUPER), 0)
    ImageDraw.Draw(im).text((2 * SUPER, 19 * SUPER), row, font=big, fill=255, anchor="ls")
    a = np.asarray(im.resize((w, ROW_H), Image.LANCZOS), dtype=np.float32) / 255
    lv = np.where(a > 0.55, 3, np.where(a > 0.2, 2, 0)).astype(np.uint8)
    ink = lv > 0
    grown = ink.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            grown[max(dy, 0):ROW_H + min(dy, 0), max(dx, 0):w + min(dx, 0)] |= \
                ink[max(-dy, 0):ROW_H + min(-dy, 0), max(-dx, 0):w + min(-dx, 0)]
    lv[grown & ~ink] = 1
    return lv


def render_text(text: str, font_path: str = FONT) -> np.ndarray:
    """Rows x width array of 0 clear / 1 outline / 2 grey / 3 white, width a multiple of 16."""
    missing = sorted({c for c in text if c != " " and ord(c) not in _cmap(font_path)})
    if missing:
        raise SubtitleError(f"no glyph for {missing!r} in {font_path}")
    small, big = _fonts(font_path)
    rows = [_render_row(r, small, big) for r in _wrap(text, small)]
    w = max(r.shape[1] for r in rows)
    if w > MAX_W:
        raise SubtitleError(f"line wider than {MAX_W} px: {text}")
    out = np.zeros((ROW_H * len(rows), w), np.uint8)
    for i, r in enumerate(rows):
        x = (w - r.shape[1]) // 2 // 2 * 2
        out[i * ROW_H:(i + 1) * ROW_H, x:x + r.shape[1]] = r
    return out


def _cue_ticks(movie: str, cues) -> list[tuple[int, int, str]]:
    if len(cues) > MAX_CUES:
        raise SubtitleError(f"{movie}: {len(cues)} cues, at most {MAX_CUES}")
    ticks = sorted((round(a * TICKS), round(b * TICKS), t) for a, b, t in cues)
    for (a, b, _) in ticks:
        if not 0 <= a < b:
            raise SubtitleError(f"{movie}: bad cue time {a}-{b}")
    for (_, b0, _), (a1, _, _) in zip(ticks, ticks[1:]):
        if a1 < b0:
            raise SubtitleError(f"{movie}: cues overlap at {a1 / TICKS:.2f} s")
    return ticks


def build_pack(movies: dict[str, list[tuple[float, float, str]]], font_path: str = FONT) -> bytes:
    names = sorted(movies)
    if any(len(m) != 4 or not m.isascii() for m in names):
        raise SubtitleError("movie names must be 4 ASCII characters")
    timed = {m: _cue_ticks(m, movies[m]) for m in names}
    head = 12 + 12 * len(names)
    cue_bytes = 16 * sum(len(c) for c in timed.values())
    lut_at = (head + cue_bytes + 31) // 32 * 32
    tex = bytearray()
    directory, table = bytearray(), bytearray()
    for m in names:
        directory += m.encode() + struct.pack(">IHH", head + len(table), len(timed[m]), 0)
        for a, b, text in timed[m]:
            px = render_text(text, font_path)
            h, w = px.shape
            srca = (PACK_VRAM + lut_at + 32 + len(tex)) // 8
            tex += bytes((px[:, 0::2] << 4 | px[:, 1::2]).astype(np.uint8).flatten())
            tex += bytes(-len(tex) % 32)
            if PACK_VRAM + lut_at + 32 + len(tex) > VRAM_LIMIT:
                raise SubtitleError(f"subtitle textures overrun VDP1 VRAM at {m}")
            table += struct.pack(">iiHHhh", a, b, srca, (w // 8) << 8 | h, -w // 2, BOTTOM_Y - h)
    body = bytes(directory) + bytes(table)
    body += bytes(lut_at - 12 - len(body)) + struct.pack(">16H", *LUT) + bytes(tex)
    total = 12 + len(body)
    if PACK_VRAM + total > VRAM_LIMIT:
        raise SubtitleError(f"subtitle pack is {total} bytes, VDP1 VRAM has {VRAM_LIMIT - PACK_VRAM}")
    return struct.pack(">IHHI", MAGIC, len(names), (PACK_VRAM + lut_at) // 8, total) + body


def pack_name(row: int) -> str:
    return PACK_NAME.format(row)


def movie_rows(first: bytes) -> dict[str, tuple[int, int]]:
    """{movie stem: (table row, index in the row)} from the movie table of the original 1ST.BIN."""
    out = {}
    for r in range(ROWS):
        for k in range(6):
            p = struct.unpack_from(">I", first, MOVIE_TABLE - FIRST_BASE + 48 * r + 8 * k)[0]
            if not FIRST_BASE <= p < FIRST_BASE + len(first):
                continue
            o = p - FIRST_BASE
            end = first.find(b"\0", o)
            if end < 0:
                raise SubtitleError(f"movie table row {r}: name at {p:#x} runs past the end of 1ST.BIN")
            name = first[o:end].decode("latin-1")
            if name.endswith(".CPK"):
                if name[:-4] in out:
                    raise SubtitleError(f"movie table: {name} listed twice")
                out[name[:-4]] = (r, k)
    return out


def build_packs(movies: dict[str, list[tuple[float, float, str]]], rows: dict[str, tuple[int, int]],
                font_path: str = FONT) -> dict[str, bytes]:
    """{pack name: pack} for every table row that has subtitled movies."""
    missing = sorted(set(movies) - set(rows))
    if missing:
        raise SubtitleError(f"movies not in the movie table: {missing}")
    openings = sorted(m for m in movies if rows[m][1] == 0)
    if openings:   # the demo sequence (kind 1) plays only these and SUB.BIN loads no pack for it
        raise SubtitleError(f"subtitles on opening movies are not supported: {openings}")
    return {pack_name(r): build_pack({m: c for m, c in movies.items() if rows[m][0] == r}, font_path)
            for r in range(ROWS) if any(rows[m][0] == r for m in movies)}


def pack_sizes(packs: dict[str, bytes]) -> list[int]:
    return [len(packs.get(pack_name(r), b"")) for r in range(ROWS)]


def lut_offset(pack: bytes) -> int:
    return struct.unpack_from(">H", pack, 6)[0] * 8 - PACK_VRAM


def voice_movies(voice_dir: Path, text: str = "ko") -> dict[str, list[tuple[float, float, str]]]:
    """{movie stem: [(start, end, text)]} from translation/voice/*.json; text is "ko" or "ja"."""
    out: dict[str, list] = {}
    for f in sorted(Path(voice_dir).glob("*.json")):
        for e in json.loads(f.read_text(encoding="utf-8"))["entries"]:
            t = e[text].strip()
            if not t:
                raise SubtitleError(f"{e['id']}: empty {text}")
            out.setdefault(e["movie"].split(".")[0], []).append((e["start"], e["end"], t))
    return {k: sorted(v) for k, v in sorted(out.items())}


def fill_pack_sizes(sub_bin: bytes, sizes: list[int]) -> bytes:
    """SUB.BIN with the pack size of every table row (0: no pack) after the marker."""
    if len(sizes) != ROWS or any(not 0 <= n <= VRAM_LIMIT - PACK_VRAM for n in sizes):
        raise SubtitleError(f"bad pack sizes {sizes}")
    i = sub_bin.index(SIZE_MARKER) + len(SIZE_MARKER)
    if len(sub_bin) < i + 4 * ROWS:
        raise SubtitleError("SUB.BIN has no room for the pack sizes; rebuild it (tools/subtitle/build.sh)")
    if sub_bin[i:i + 4 * ROWS] != bytes(4 * ROWS):
        raise SubtitleError("pack sizes already filled")
    return sub_bin[:i] + struct.pack(f">{ROWS}I", *sizes) + sub_bin[i + 4 * ROWS:]


def patch_first(first: bytes, sub_bin: bytes) -> bytes:
    """1ST.BIN with the movie-sequence literals pointing at SUB.BIN, which is appended."""
    if len(first) != FIRST_SIZE:
        raise SubtitleError(f"1ST.BIN is {len(first)} bytes, expected {FIRST_SIZE}")
    if len(sub_bin) > FIRST_SLACK:
        raise SubtitleError(f"SUB.BIN is {len(sub_bin)} bytes, the last sector has {FIRST_SLACK} spare")
    out = bytearray(first)
    for value, lits in SEQUENCE_VARS.values():
        for lit in lits:
            if struct.unpack_from(">I", out, lit - FIRST_BASE)[0] != value:
                raise SubtitleError(f"1ST.BIN {lit:#x}: expected literal {value:#x}")
    for lit, orig, entry in HOOKS:
        new = SUB_BASE + entry
        o = lit - FIRST_BASE
        if struct.unpack_from(">I", out, o)[0] != orig:
            raise SubtitleError(f"1ST.BIN {lit:#x}: expected literal {orig:#x}")
        struct.pack_into(">I", out, o, new)
    return bytes(out) + sub_bin
