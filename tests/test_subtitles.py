import json
import pathlib
import struct

import numpy as np
import pytest

from fromance import subtitles as st

ROOT = pathlib.Path(__file__).resolve().parents[1]
SUB_BIN = (ROOT / "assets/subtitle/SUB.BIN").read_bytes()


def _parse(pack: bytes):
    """Mirror of tools/subtitle/sub.c: directory, per-movie cues, LUT, textures."""
    magic, n, _, total = struct.unpack_from(">IHHI", pack)
    assert magic == st.MAGIC and total == len(pack)
    movies = {}
    for i in range(n):
        name, off, cnt, _ = struct.unpack_from(">4sIHH", pack, 12 + 12 * i)
        assert off % 4 == 0
        movies[name.decode()] = [struct.unpack_from(">iiHHhh", pack, off + 16 * k) for k in range(cnt)]
    return movies


def test_render_rows_are_bounded_and_four_colour():
    px = st.render_text("やっといらしたわね")
    assert px.ndim == 2 and px.shape[0] == st.ROW_H
    assert px.shape[1] % 16 == 0 and px.shape[1] <= st.MAX_W
    assert set(np.unique(px)) <= {0, 1, 2, 3}
    assert px[0].max() == 0 and px[-1].max() == 0       # outline stays inside the box
    assert (px == 3).any() and (px == 1).any()


def test_long_text_wraps_to_two_rows_and_too_long_is_refused():
    one = st.render_text("あ" * 6)
    two = st.render_text("あ" * 22)
    assert two.shape[0] == 2 * st.ROW_H > one.shape[0]
    with pytest.raises(st.SubtitleError, match="rows"):
        st.render_text("あ" * 60)


def test_missing_glyph_is_refused():
    with pytest.raises(st.SubtitleError, match="glyph"):
        st.render_text("\U0001F600")


def test_pack_round_trips_the_layout_sub_c_reads():
    movies = {"AS01": [(1.3, 2.6, "やっといらしたわね"), (2.6, 4.4, "私は仙道アスカ")], "RI02": [(4.0, 5.8, "あ")]}
    pack = st.build_pack(movies)
    got = _parse(pack)
    assert list(got) == ["AS01", "RI02"] and len(pack) % 4 == 0
    assert [(a, b) for a, b, *_ in got["AS01"]] == [(780, 1560), (1560, 2640)]
    for cues in got.values():
        for _, _, srca, size, x, y in cues:
            w, h = (size >> 8) * 8, size & 0xFF
            off = srca * 8 - st.PACK_VRAM
            assert off % 32 == 0 and 0 < off and off + w * h // 2 <= len(pack)
            assert x == -w // 2 and y + h <= st.BOTTOM_Y and -176 <= x and x + w <= 176
    i = st.lut_offset(pack)
    assert struct.unpack_from(">4H", pack, i) == (0, 0x8000, 0xC210, 0xFFFF) and i % 32 == 0
    assert st.PACK_VRAM + len(pack) <= st.VRAM_LIMIT


def test_build_refuses_overlap_too_many_cues_and_oversized_pack():
    with pytest.raises(st.SubtitleError, match="overlap"):
        st.build_pack({"AS01": [(1.0, 3.0, "あ"), (2.0, 4.0, "い")]})
    with pytest.raises(st.SubtitleError, match="cues"):
        st.build_pack({"AS01": [(i, i + 0.5, "あ") for i in range(st.MAX_CUES + 1)]})
    long = "あ" * 20
    with pytest.raises(st.SubtitleError, match="VRAM"):
        st.build_pack({f"M{i:03d}": [(k, k + 0.5, long) for k in range(st.MAX_CUES)] for i in range(10)})


def test_pack_sizes_are_filled_after_the_marker():
    sizes = [123456, 0, 7, 8, 9]
    out = st.fill_pack_sizes(SUB_BIN, sizes)
    i = out.index(st.SIZE_MARKER) + len(st.SIZE_MARKER)
    assert list(struct.unpack_from(f">{st.ROWS}I", out, i)) == sizes
    assert out[:i] == SUB_BIN[:i] and out[i + 4 * st.ROWS:] == SUB_BIN[i + 4 * st.ROWS:]
    with pytest.raises(st.SubtitleError, match="size"):
        st.fill_pack_sizes(SUB_BIN, [st.VRAM_LIMIT, 0, 0, 0, 0])
    with pytest.raises(st.SubtitleError, match="size"):
        st.fill_pack_sizes(SUB_BIN, [1, 2])
    with pytest.raises(st.SubtitleError, match="filled"):
        st.fill_pack_sizes(out, sizes)
    old = SUB_BIN[:SUB_BIN.index(st.SIZE_MARKER) + len(st.SIZE_MARKER) + 4]     # one-size SUB.BIN
    with pytest.raises(st.SubtitleError, match="rebuild"):
        st.fill_pack_sizes(old, sizes)


def _fake_table(b: bytearray, rows: list[list[str]]) -> None:
    """Movie table of 0x0600F964 (row x 6 x {name pointer, flag}); names stored after it."""
    at = st.MOVIE_TABLE + 48 * 7
    for r, names in enumerate(rows):
        for k, name in enumerate(names):
            struct.pack_into(">II", b, st.MOVIE_TABLE - st.FIRST_BASE + 48 * r + 8 * k, at, 0)
            b[at - st.FIRST_BASE:at - st.FIRST_BASE + len(name) + 1] = name.encode() + b"\0"
            at += 16


def test_movie_rows_come_from_the_game_table():
    b = bytearray(_fake_1st())
    _fake_table(b, [["RIOP.CPK", "RI01.CPK", "DUMMY"], ["MI01.CPK"], ["KE01.CPK"], ["RY05.CPK"], ["AS04.CPK"]])
    assert st.movie_rows(bytes(b)) == {"RIOP": (0, 0), "RI01": (0, 1), "MI01": (1, 0), "KE01": (2, 0),
                                       "RY05": (3, 0), "AS04": (4, 0)}


def test_movie_rows_refuse_a_broken_table():
    b = bytearray(_fake_1st())
    _fake_table(b, [["RI01.CPK"], ["RI01.CPK"]])
    with pytest.raises(st.SubtitleError, match="twice"):
        st.movie_rows(bytes(b))
    b = bytearray(_fake_1st())
    struct.pack_into(">II", b, st.MOVIE_TABLE - st.FIRST_BASE, st.FIRST_BASE + st.FIRST_SIZE - 4, 0)
    b[-4:] = b"RI01"
    with pytest.raises(st.SubtitleError, match="end"):
        st.movie_rows(bytes(b))


def test_packs_are_split_by_table_row():
    rows = {"RI01": (0, 1), "MI02": (1, 2), "AS01": (4, 1), "ASOP": (4, 0)}
    packs = st.build_packs({"AS01": [(1.0, 2.0, "あ")], "RI01": [(1.0, 2.0, "い"), (3.0, 4.0, "う")]}, rows)
    assert list(packs) == [st.pack_name(0), st.pack_name(4)]
    assert st.pack_name(4) == "ZSUB4.DAT"
    assert list(_parse(packs["ZSUB0.DAT"])) == ["RI01"] and len(_parse(packs["ZSUB0.DAT"])["RI01"]) == 2
    assert list(_parse(packs["ZSUB4.DAT"])) == ["AS01"]
    assert st.pack_sizes(packs) == [len(packs["ZSUB0.DAT"]), 0, 0, 0, len(packs["ZSUB4.DAT"])]
    with pytest.raises(st.SubtitleError, match="table"):
        st.build_packs({"XX01": [(1.0, 2.0, "あ")]}, rows)
    # the demo sequence (kind 1) plays only the openings and loads no pack
    with pytest.raises(st.SubtitleError, match="opening"):
        st.build_packs({"ASOP": [(1.0, 2.0, "あ")]}, rows)
    assert st.build_packs({}, rows) == {} and st.pack_sizes({}) == [0] * st.ROWS


def _define(src: str, name: str) -> str:
    assert f"#define {name} " in src
    return src.split(f"#define {name} ")[1].split("\n")[0].strip()


def test_sub_c_constants_match_the_builder():
    src = (ROOT / "tools/subtitle/sub.c").read_text()
    for name, value in (("PACK_VRAM", st.PACK_VRAM), ("VRAM_LIMIT", st.VRAM_LIMIT), ("MAX_CUES", st.MAX_CUES),
                        ("MAGIC", st.MAGIC), ("ROWS", st.ROWS)):
        assert int(_define(src, name).split()[0], 0) == value
    assert f'"{st.pack_name(0)}"' in src and st.pack_name(0).encode() in SUB_BIN
    # sub.c writes the row digit at pack_name[4]
    assert st.ROWS <= 10 and all(st.pack_name(r)[4] == str(r) and len(st.pack_name(r)) == 9 for r in range(st.ROWS))
    # sub.c reads the same mode/character bytes as the movie sequence 0x0600F360,
    # through the cache-through alias (0x2xxxxxxx) so a stale cache line cannot be read
    for name, (value, _) in st.SEQUENCE_VARS.items():
        assert f"{value | 0x20000000:#010x}" in _define(src, name).lower()


def _fake_1st() -> bytes:
    b = bytearray(st.FIRST_SIZE)
    for lit, orig, _ in st.HOOKS:
        struct.pack_into(">I", b, lit - st.FIRST_BASE, orig)
    for value, lits in st.SEQUENCE_VARS.values():
        for lit in lits:
            struct.pack_into(">I", b, lit - st.FIRST_BASE, value)
    return bytes(b)


def test_patch_first_hooks_three_literals_and_appends_the_code():
    out = st.patch_first(_fake_1st(), SUB_BIN)
    assert len(out) == st.FIRST_SIZE + len(SUB_BIN) <= st.FIRST_SIZE + st.FIRST_SLACK
    assert [struct.unpack_from(">I", out, lit - st.FIRST_BASE)[0] for lit, _, _ in st.HOOKS] == \
        [st.SUB_BASE + e for _, _, e in st.HOOKS]
    assert out[st.FIRST_SIZE:] == SUB_BIN


def test_patch_first_refuses_unexpected_bytes_and_oversized_code():
    bad = bytearray(_fake_1st()); bad[st.HOOKS[1][0] - st.FIRST_BASE] ^= 1
    with pytest.raises(st.SubtitleError, match="expected"):
        st.patch_first(bytes(bad), SUB_BIN)
    with pytest.raises(st.SubtitleError, match="spare"):
        st.patch_first(_fake_1st(), bytes(st.FIRST_SLACK + 4))
    for value, lits in st.SEQUENCE_VARS.values():
        for lit in lits:
            bad = bytearray(_fake_1st()); bad[lit - st.FIRST_BASE + 3] ^= 1
            with pytest.raises(st.SubtitleError, match="expected"):
                st.patch_first(bytes(bad), SUB_BIN)
    assert st.SEQUENCE_VARS["CHAR"][1] == (0x0600F444, 0x0600F484)       # kinds 0 and 2


def test_shipped_code_entry_points_match_crt():
    # each entry is "mov.l @(disp,pc),r0" or the frame stub "sts.l pr,@-r15"
    for _, _, e in st.HOOKS:
        assert SUB_BIN[e] in (0xD0, 0x4F)
    assert SUB_BIN[st.ENTRY_FRAME:st.ENTRY_FRAME + 2] == bytes.fromhex("4f22")
    assert st.SIZE_MARKER in SUB_BIN


def test_voice_files_build_in_test_mode():
    movies = st.voice_movies(ROOT / "translation/voice", text="ja")
    assert sum(len(c) for c in movies.values()) == 64
    assert set(movies) == {f"{c}{n:02d}" for c in ("AS", "KE", "MI", "RI", "RY") for n in (1, 2, 3, 4)} | {"RY05"}
    st.build_pack(movies)
