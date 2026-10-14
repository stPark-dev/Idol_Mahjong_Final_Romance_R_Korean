import os
import pathlib

import numpy as np
import pytest

from fromance import iso9660, mjmenu as mm
from fromance.disc import SourceDisc

ROOT = pathlib.Path(__file__).resolve().parents[1]
CUE = pathlib.Path(os.environ.get("FROMANCE_SOURCE_CUE", ROOT / "orig/Idol Mahjong - Final Romance R (Japan).cue"))
TEXTS = ROOT / "translation/graphics/mj_call.json"


def test_label_is_face_with_a_lower_right_shadow():
    img = mm.render_label("퐁", 40, 16, face=3, shadow=4, bg=15)
    assert set(np.unique(img)) <= {3, 4, 15} and (img == 3).any() and (img == 4).any()
    face = img == 3
    shadow = img == 4
    lifted = np.zeros_like(shadow); lifted[:-1, :-1] = shadow[1:, 1:]
    assert (lifted <= face).all()                       # every shadow pixel sits below-right of face
    with pytest.raises(mm.MjMenuError, match="wide"):
        mm.render_label("리치리치리치", 20, 16, face=9, shadow=10, bg=15)
    font, _ = mm._font()
    ink = font.getbbox("깡")[2] / mm.SUPER
    with pytest.raises(mm.MjMenuError, match="wide"):              # measured by ink, not advance
        mm.render_label("깡", int(ink), 16, face=5, shadow=6, bg=15)


def test_texts_load_in_id_order(tmp_path):
    assert mm.load_texts(TEXTS) == ["퐁", "깡", "치", "론", "리치"]
    bad = tmp_path / "bad.json"
    bad.write_text('{"entries": [{"id": "mj.call.kan", "ko": "깡"}]}', encoding="utf-8")
    with pytest.raises(mm.MjMenuError, match="id"):
        mm.load_texts(bad)


source = pytest.mark.skipif(not CUE.exists(), reason="원본 디스크 없음")


@pytest.fixture(scope="module")
def files():
    d = SourceDisc(CUE)
    try:
        entries = iso9660.list_files(d.user)
        return {n: d.read_file(entries[n].lba, entries[n].size) for n in ("1ST.BIN", "SL.BIN")}
    finally:
        d.close()


@source
def test_menu_layout_matches_the_call_window(files):
    lay = mm.Layout.read(files["1ST.BIN"], files["SL.BIN"])
    assert (lay.off, lay.w, lay.h) == (0x1140, 104, 48)
    assert [(o.face, o.shadow) for o in lay.options] == [(3, 4), (5, 6), (7, 8), (1, 2), (9, 10)]
    assert [o.x for o in lay.options[:3]] == sorted(o.x for o in lay.options[:3])
    assert lay.options[0].x + lay.options[0].width < lay.options[1].x     # ポン ends before カン
    for o in lay.options:
        assert o.y + mm.LABEL_H <= mm.INTERIOR[3] and o.x + o.width <= mm.INTERIOR[2]


@source
def test_korean_menu_changes_only_the_labels(files):
    sl = mm.build_file(files["1ST.BIN"], files["SL.BIN"], mm.load_texts(TEXTS))
    lay = mm.Layout.read(files["1ST.BIN"], files["SL.BIN"])
    img = mm.read_image(sl, lay)
    old = mm.read_image(files["SL.BIN"], lay)
    x0, y0, x1, y1 = mm.INTERIOR
    outside = np.ones(img.shape, bool); outside[y0:y1, x0:x1] = False
    assert (img[outside] == old[outside]).all()
    for o in lay.options:                               # each label keeps its own two colours
        assert ((img == o.face) | (img == o.shadow)).any()
    diff = np.frombuffer(sl, np.uint8) != np.frombuffer(files["SL.BIN"], np.uint8)
    assert not (diff & ~mm.editable(lay, len(sl))).any()
    bad = bytearray(sl); bad[lay.off] ^= 0x11
    with pytest.raises(mm.MjMenuError, match="byte"):
        mm.check_protected(lay, files["SL.BIN"], bytes(bad))
    inner = lay.off + (20 * lay.w + 50) // 2                           # inside the frame
    bad = bytearray(sl); bad[inner] = 0xCC                             # colour 12 is the frame's
    with pytest.raises(mm.MjMenuError, match="option colour"):
        mm.check_protected(lay, files["SL.BIN"], bytes(bad))
