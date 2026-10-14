import os
import pathlib

import numpy as np
import pytest

from fromance import iso9660, mjlabel as ml
from fromance.disc import SourceDisc

ROOT = pathlib.Path(__file__).resolve().parents[1]
CUE = pathlib.Path(os.environ.get("FROMANCE_SOURCE_CUE", ROOT / "orig/Idol Mahjong - Final Romance R (Japan).cue"))
TEXTS = ROOT / "translation/graphics/mj_label.json"


def test_label_face_has_a_shadow_on_the_left():
    face = ml.render_face("쯔모")
    assert face.shape == (ml.FACE_H, ml.FACE_W) and set(np.unique(face)) <= {ml.BG, ml.INK, ml.SHADOW}
    ink, shadow = face == ml.INK, face == ml.SHADOW
    assert ink.any() and shadow.any()
    right = np.zeros_like(shadow); right[:, :-1] = ink[:, 1:]
    assert (shadow <= right).all()                          # each shadow pixel sits left of ink
    with pytest.raises(ml.MjLabelError, match="fit"):
        ml.render_face("쯔모쯔모쯔모")


def test_text_loads_by_id(tmp_path):
    assert ml.load_text(TEXTS) == "쯔모"
    bad = tmp_path / "bad.json"
    bad.write_text('{"entries": [{"id": "mj.label.ron", "ko": "론"}]}', encoding="utf-8")
    with pytest.raises(ml.MjLabelError, match="id"):
        ml.load_text(bad)


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
def test_label_cells_are_the_tsumo_box_and_only_its_face_changes(first):
    old = ml.read_label(first)
    assert old.shape == (16, 24) and set(np.unique(old)) <= set(ml.COLOURS)
    out = ml.build_file(first, ml.load_text(TEXTS))
    new = ml.read_label(out)
    y0, x0 = ml.FACE_Y, ml.FACE_X
    frame = np.ones(old.shape, bool); frame[y0:y0 + ml.FACE_H, x0:x0 + ml.FACE_W] = False
    assert (new[frame] == old[frame]).all() and not (new == old).all()
    diff = np.frombuffer(out, np.uint8) != np.frombuffer(first, np.uint8)
    assert not (diff & ~ml.editable(len(first))).any()
    bad = bytearray(out); bad[ml.CELLS - 1] ^= 1
    with pytest.raises(ml.MjLabelError, match="byte"):
        ml.check_protected(first, bytes(bad))
