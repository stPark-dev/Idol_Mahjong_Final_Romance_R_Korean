import os
import pathlib

import numpy as np
import pytest

from fromance import iso9660, mjyaku as my
from fromance.disc import SourceDisc

ROOT = pathlib.Path(__file__).resolve().parents[1]
CUE = pathlib.Path(os.environ.get("FROMANCE_SOURCE_CUE", ROOT / "orig/Idol Mahjong - Final Romance R (Japan).cue"))
YAKU = ROOT / "translation/graphics/mj_yaku.json"
SCORE = ROOT / "translation/graphics/mj_score.json"
COLOURS = {1: (28, 29, 24), 11: (2, 2, 2), 12: (9, 8, 7), 13: (16, 15, 13), 14: (23, 22, 19)}


def test_text_shrinks_to_fit_and_uses_only_the_given_colours():
    wide = my.render_text("또이또이", 48, 16, COLOURS, bg=1)
    assert wide.shape == (16, 48) and set(np.unique(wide)) <= set(COLOURS) and (wide != 1).any()
    assert (wide[:, -1] == 1).all() and (wide[:, 0] == 1).all()
    left = my.render_text("핑후", 64, 16, COLOURS, bg=1, align="left")
    cols = np.flatnonzero((left != 1).any(0))
    assert cols.min() <= 2 and cols.max() < 40                              # starts at the left edge
    with pytest.raises(my.MjYakuError, match="fit"):
        my.render_text("아주아주아주아주긴역이름", 32, 16, COLOURS, bg=1)


def test_texts_load_by_slot(tmp_path):
    yaku = my.load_yaku(YAKU)
    assert len(yaku) == 50 and yaku[21] == "멘젠쯔모" and 19 not in yaku
    assert my.load_score(SCORE) == {"han": "판", "fu": "부", "ten": "점"}
    bad = tmp_path / "bad.json"
    bad.write_text('{"entries": [{"id": "mj.yaku.99", "ko": "x"}]}', encoding="utf-8")
    with pytest.raises(my.MjYakuError, match="slot"):
        my.load_yaku(bad)
    bad.write_text('{"entries": [{"id": "mj.yaku.01", "ko": "a"}, {"id": "mj.yaku.01", "ko": "b"}]}', encoding="utf-8")
    with pytest.raises(my.MjYakuError, match="twice"):
        my.load_yaku(bad)
    bad.write_text('{"entries": [{"id": "xx.score.han", "ko": "판"}, {"id": "mj.score.fu", "ko": "부"},'
                   ' {"id": "mj.score.ten", "ko": "점"}]}', encoding="utf-8")
    with pytest.raises(my.MjYakuError, match="han"):
        my.load_score(bad)


source = pytest.mark.skipif(not CUE.exists(), reason="원본 디스크 없음")


@pytest.fixture(scope="module")
def files():
    d = SourceDisc(CUE)
    try:
        e = iso9660.list_files(d.user)
        return {n: d.read_file(e[n].lba, e[n].size) for n in ("1ST.BIN", "MJDAT2.BIN")}
    finally:
        d.close()


@source
def test_layout_is_the_yaku_table_and_score_glyphs(files):
    lay = my.Layout.read(files["1ST.BIN"], files["MJDAT2.BIN"])
    assert len(lay.yaku) == 50 and lay.yaku[21] == (0x264, 12)
    assert lay.glyph("han") == 0x104 and lay.glyph("fu") == 0x108 and lay.glyph("ten") == 0x10C
    assert set(lay.colours) >= {lay.bg} and len(lay.colours) >= 4
    # text colours come from the score glyphs: greys only, not the brown accents of two names
    glyph_colours = set()
    for g in [*my.GLYPHS.values(), *[my.DIGITS + 4 * k for k in range(10)]]:
        glyph_colours |= set(np.unique(my.read_glyph(lay, files["MJDAT2.BIN"], g)).tolist())
    assert set(lay.colours) == glyph_colours


@source
def test_korean_result_text_changes_only_its_cells(files):
    first, mj2 = files["1ST.BIN"], files["MJDAT2.BIN"]
    lay = my.Layout.read(first, mj2)
    out = my.build_file(first, mj2, my.load_yaku(YAKU), my.load_score(SCORE))
    assert len(out) == len(mj2)
    diff = np.frombuffer(out, np.uint8) != np.frombuffer(mj2, np.uint8)
    assert diff.any() and not (diff & ~my.editable(lay, len(mj2))).any()
    one = my.read_glyph(lay, out, 0x138)                     # kanji 一 now shows the digit 1
    assert np.array_equal(one, my.read_glyph(lay, mj2, 0x114))
    img = my.read_yaku(lay, out, 21)
    assert img.shape == (16, 96) and set(np.unique(img)) <= set(lay.colours)
    for s in lay.yaku:
        assert set(np.unique(my.read_yaku(lay, out, s)).tolist()) <= set(lay.colours)
    for g in [*my.GLYPHS.values(), my.KANJI + 36]:          # 판 부 점 and 「10」: drawn, new, in panel colours
        new, old = my.read_glyph(lay, out, g), my.read_glyph(lay, mj2, g)
        assert (new != lay.bg).any() and not np.array_equal(new, old)
        assert set(np.unique(new).tolist()) <= set(lay.colours)
    bad = bytearray(out); o = my.cell_offset(lay, my.GLYPHS["han"]); bad[o] = (bad[o] & 0x0F) | 0x40
    with pytest.raises(my.MjYakuError, match="colour"):
        my.check_protected(lay, mj2, bytes(bad))
    bad = bytearray(out); bad[my.cell_offset(lay, 0x2)] ^= 0x11            # a frame cell
    with pytest.raises(my.MjYakuError, match="byte"):
        my.check_protected(lay, mj2, bytes(bad))
