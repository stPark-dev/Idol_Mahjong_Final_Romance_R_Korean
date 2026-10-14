import os
import pathlib

import numpy as np
import pytest

from fromance import iso9660, mjdeclare as md
from fromance.disc import SourceDisc

ROOT = pathlib.Path(__file__).resolve().parents[1]
CUE = pathlib.Path(os.environ.get("FROMANCE_SOURCE_CUE", ROOT / "orig/Idol Mahjong - Final Romance R (Japan).cue"))
TEXTS = ROOT / "translation/graphics/mj_declare.json"


def test_banner_is_a_lit_face_over_a_darker_extrusion():
    face = [200, 190, 180, 170]          # bright to dark
    side = [60, 50, 40]
    img = md.render_banner("깡", 96, 64, face, side)
    assert img.shape == (64, 96) and set(np.unique(img)) <= {0, *face, *side}
    ys, xs = np.nonzero(img)
    assert xs.min() > 0 and ys.min() > 0 and xs.max() < 95 and ys.max() < 63
    is_face, is_side = np.isin(img, face), np.isin(img, side)
    assert is_face.sum() > is_side.sum() > 0
    fy, fx = np.nonzero(is_face); sy, sx = np.nonzero(is_side)
    assert sx.mean() > fx.mean() and sy.mean() > fy.mean()          # extruded to the lower right
    top, bottom = img[fy.min():fy.min() + 4], img[fy.max() - 3:fy.max() + 1]
    assert np.isin(top, face[:2]).sum() > np.isin(bottom, face[:2]).sum()   # lit from the top


def test_an_empty_word_is_a_declaration_error():
    with pytest.raises(md.MjDeclareError, match="empty"):
        md.render_banner(" ", 96, 64, [200, 190, 180], [60, 50])


def test_texts_load_in_kind_order(tmp_path):
    assert md.load_texts(TEXTS) == ["깡", "퐁", "치", "리치", "론", "쯔모"]
    bad = tmp_path / "bad.json"
    bad.write_text('{"entries": [{"id": "mj.declare.pon", "ko": "퐁"}]}', encoding="utf-8")
    with pytest.raises(md.MjDeclareError, match="id"):
        md.load_texts(bad)


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
def test_frames_are_the_declaration_sprites(files):
    frames = md.read_frames(files["1ST.BIN"], files["SL.BIN"])
    assert [len(f) for f in frames] == [3, 3, 3, 2, 3, 3]
    assert (frames[3][0].off, frames[3][0].w, frames[3][0].h) == (0x42240, 168, 64)
    for kind in frames:
        for fr in kind:
            assert fr.face and fr.side and not set(fr.face) & set(fr.side)


@source
def test_korean_banners_replace_only_the_frames(files):
    sl = md.build_file(files["1ST.BIN"], files["SL.BIN"], md.load_texts(TEXTS))
    frames = md.read_frames(files["1ST.BIN"], files["SL.BIN"])
    mask = md.editable(frames, len(sl))
    diff = np.frombuffer(sl, np.uint8) != np.frombuffer(files["SL.BIN"], np.uint8)
    assert diff.any() and not (diff & ~mask).any()
    for kind in frames:
        for fr in kind:
            img = np.frombuffer(sl[fr.off:fr.off + fr.w * fr.h], np.uint8)
            assert set(np.unique(img)) <= {0, *fr.face, *fr.side}
    lowest = min(f.off for k in frames for f in k)
    assert not mask[lowest - 1]
    bad = bytearray(sl); bad[lowest - 1] ^= 1                         # just before every frame
    with pytest.raises(md.MjDeclareError, match="byte"):
        md.check_protected(frames, files["SL.BIN"], bytes(bad))
    fr = frames[0][0]
    foreign = next(c for c in range(1, 256) if c not in {*fr.face, *fr.side})
    bad = bytearray(sl); bad[fr.off + fr.w * (fr.h // 2) + fr.w // 2] = foreign
    with pytest.raises(md.MjDeclareError, match="colour"):
        md.check_protected(frames, files["SL.BIN"], bytes(bad))
