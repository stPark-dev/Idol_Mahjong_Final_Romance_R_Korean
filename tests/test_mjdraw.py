import os
import pathlib

import numpy as np
import pytest

from fromance import iso9660, mjdraw as mw
from fromance.disc import SourceDisc

ROOT = pathlib.Path(__file__).resolve().parents[1]
CUE = pathlib.Path(os.environ.get("FROMANCE_SOURCE_CUE", ROOT / "orig/Idol Mahjong - Final Romance R (Japan).cue"))
TEXTS = ROOT / "translation/graphics/mj_draw.json"


def test_texts_load_in_id_order(tmp_path):
    assert mw.load_texts(TEXTS) == ["유국", "텐파이", "노텐"]
    bad = tmp_path / "bad.json"
    bad.write_text('{"entries": [{"id": "mj.draw.noten", "ko": "노텐"}]}', encoding="utf-8")
    with pytest.raises(mw.MjDrawError, match="id"):
        mw.load_texts(bad)
    bad.write_text('{"entries": [{"id": "mj.draw.ryuukyoku"}, {"id": "mj.draw.tenpai"}, {"id": "mj.draw.noten"}]}',
                   encoding="utf-8")
    with pytest.raises(mw.MjDrawError, match="ko"):
        mw.load_texts(bad)


source = pytest.mark.skipif(not CUE.exists(), reason="원본 디스크 없음")


@pytest.fixture(scope="module")
def first():
    d = SourceDisc(CUE)
    try:
        e = iso9660.list_files(d.user)["1ST.BIN"]
        return d.read_file(e.lba, e.size)
    finally:
        d.close()


@source
def test_layout_is_the_five_draw_maps(first):
    lay = mw.Layout.read(first)
    assert [(m.w, m.h) for m in lay.maps] == [(12, 6), (18, 6), (18, 6), (16, 5), (16, 5)]
    assert [m.word for m in lay.maps] == [0, 1, 2, 1, 2]
    pools = [set(m.pool) for m in lay.maps]
    assert all(not (a & b) for i, a in enumerate(pools) for b in pools[i + 1:])
    for m in lay.maps:
        assert all(m.add <= u <= m.add + 255 and u % 2 == 0 for u in m.pool)


@source
def test_original_maps_round_trip(first):
    lay = mw.Layout.read(first)
    images = mw.decode_maps(lay, first)
    out = mw.apply(lay, first, images)
    assert all(np.array_equal(a, b) for a, b in zip(mw.decode_maps(lay, out), images))
    mw.check_protected(lay, first, out)


@source
def test_korean_draw_words_are_drawn_and_strays_refused(first):
    lay = mw.Layout.read(first)
    out = mw.build_file(first, mw.load_texts(TEXTS))
    for img, m in zip(mw.decode_maps(lay, out), lay.maps):
        assert set(np.unique(img)) <= {0, *m.face, *m.side} and (img != 0).any()
        assert set(m.face) | set(m.side) <= m.colours
    mw.check_protected(lay, first, out)
    bad = bytearray(out); bad[lay.maps[0].src - 1] ^= 1
    with pytest.raises(mw.MjDrawError, match="byte"):
        mw.check_protected(lay, first, bytes(bad))
    used = {m.entry: sorted({b + m.add for b in out[m.src:m.src + m.w * m.h]} - {m.empty}) for m in lay.maps}
    m1, m2, shared = next((a, b, u) for a in lay.maps for b in lay.maps if a is not b
                          for u in used[a.entry] if b.add <= u <= b.add + 255)
    used1 = used[m1.entry]
    bad = bytearray(out); bad[m2.src] = shared - m2.add                       # map 2 -> a cell of map 1
    with pytest.raises(mw.MjDrawError, match="serves"):
        mw.check_protected(lay, first, bytes(bad))
    pool = {v for m in lay.maps for v in m.pool}
    reach = [(m, u) for m in lay.maps for u in range(m.add, m.add + 256, 2) if u not in pool]
    if reach:                         # a map byte that reaches a cell outside the draw cells
        m, u = reach[0]
        bad = bytearray(out); bad[m.src] = u - m.add
        with pytest.raises(mw.MjDrawError, match="outside"):
            mw.check_protected(lay, first, bytes(bad))
    unit = used1[0]
    colour = next(c for c in range(1, 256) if c not in m1.colours)
    bad = bytearray(out); bad[mw.cell_offset(lay, unit) + 9] = colour
    with pytest.raises(mw.MjDrawError, match="colour"):
        mw.check_protected(lay, first, bytes(bad))


@source
def test_a_picture_with_too_many_distinct_cells_is_refused(first):
    lay = mw.Layout.read(first)
    rng = np.random.default_rng(1)
    noisy = [rng.choice(sorted(m.colours), (m.h * 8, m.w * 8)).astype(np.uint8) for m in lay.maps]
    with pytest.raises(mw.MjDrawError, match="needs more than"):
        mw.apply(lay, first, noisy)
