import os
import pathlib

import numpy as np
import pytest

from fromance import iso9660, kisebtn as kb
from fromance.disc import SourceDisc

ROOT = pathlib.Path(__file__).resolve().parents[1]
CUE = pathlib.Path(os.environ.get("FROMANCE_SOURCE_CUE", ROOT / "orig/Idol Mahjong - Final Romance R (Japan).cue"))
TEXTS = ROOT / "translation/graphics/kise_buttons.json"


def test_label_is_one_colour_on_the_face():
    face = np.full((24, 56), kb.FACE, np.uint8)
    img = kb.draw_label(face, "확대", (28, 11))
    assert set(np.unique(img)) <= {kb.FACE, kb.TEXT} and (img == kb.TEXT).any()
    ys, xs = np.nonzero(img == kb.TEXT)
    assert abs((xs.min() + xs.max()) / 2 - 28) <= 1.5
    with pytest.raises(kb.KiseError, match="fit"):
        kb.draw_label(face, "아주아주긴단추이름", (28, 11))


def test_texts_load_in_order(tmp_path):
    assert kb.load_texts(TEXTS) == {"end": "종료", "zoom": "확대", "unzoom": "축소", "next": "다음", "back": "이전",
                                    "clear": "지우기"}
    bad = tmp_path / "bad.json"
    bad.write_text('{"entries": [{"id": "kise.end", "ko": "x"}]}', encoding="utf-8")
    with pytest.raises(kb.KiseError, match="id"):
        kb.load_texts(bad)


source = pytest.mark.skipif(not CUE.exists(), reason="원본 디스크 없음")


@pytest.fixture(scope="module")
def files():
    d = SourceDisc(CUE)
    try:
        e = iso9660.list_files(d.user)
        return {n: d.read_file(e[n].lba, e[n].size) for n in ("1ST.BIN", "KISEDAT.BIN")}
    finally:
        d.close()


@source
def test_layout_is_eleven_button_maps(files):
    lay = kb.Layout.read(files["1ST.BIN"], files["KISEDAT.BIN"])
    assert sorted(lay.maps) == [118, 119, 120, 121, 122, 123, 124, 126, 127, 128, 129]
    assert lay.label(118) == "end" and lay.label(126) == "back" and lay.label(128) == "clear"
    for i in lay.maps:
        assert set(np.unique(kb.decode_map(lay, files["KISEDAT.BIN"], i)).tolist()) <= set(kb.COLOURS)


@source
def test_korean_buttons_change_only_their_bytes(files):
    first, kd = files["1ST.BIN"], files["KISEDAT.BIN"]
    texts = kb.load_texts(TEXTS)
    out = kb.build_file(first, kd, texts)
    lay = kb.Layout.read(first, kd)
    diff = np.frombuffer(out, np.uint8) != np.frombuffer(kd, np.uint8)
    assert diff.any() and not (diff & ~kb.editable(lay, kd)).any()
    for i in kb.NORMAL:
        old, new = kb.decode_map(lay, kd, i), kb.decode_map(lay, out, i)
        frame = ~kb.label_mask(old)               # everything but the label keeps its colour or becomes text
        assert not np.array_equal(old, new) and ((new[frame] == old[frame]) | (new[frame] == kb.TEXT)).all()
        ys, xs = np.nonzero(old == kb.TEXT)          # no pixel of the old label (its colour-3 edge too) is left
        box = new[ys.min() - 1:ys.max() + 2, xs.min() - 1:xs.max() + 2]
        assert set(np.unique(box).tolist()) <= {kb.FACE, kb.TEXT}, i
    for pressed, normal in kb.PRESSED.items():   # D-25: the pressed state shows the normal button
        assert np.array_equal(kb.decode_map(lay, out, pressed), kb.decode_map(lay, out, normal))
    bad = bytearray(out); bad[lay.map_off(125)] ^= 1
    with pytest.raises(kb.KiseError, match="byte"):
        kb.check_protected(lay, kd, bytes(bad))


@source
def test_only_label_cells_may_change(files):
    first, kd = files["1ST.BIN"], files["KISEDAT.BIN"]
    lay = kb.Layout.read(first, kd)
    out = kb.build_file(first, kd, kb.load_texts(TEXTS))
    pool, shown = kb._shared_units(lay, kd)
    in_pieces = {u for u0, _, sz in lay.pieces for u in range(u0, u0 + sz // 32)}
    written = {u for u in in_pieces if kd[kb.cell_offset(lay, u):][:32] != out[kb.cell_offset(lay, u):][:32]}
    assert written and written <= pool and not written & shown
    for u in (min(in_pieces - pool - shown), min(shown & in_pieces)):   # a cell no map shows, a frame cell
        bad = bytearray(out); bad[kb.cell_offset(lay, u)] ^= 0x11
        with pytest.raises(kb.KiseError, match="protected"):
            kb.check_protected(lay, kd, bytes(bad))
