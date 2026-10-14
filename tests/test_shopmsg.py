import os
import pathlib

import numpy as np
import pytest

from fromance import iso9660, shopmsg as sg
from fromance.disc import SourceDisc

ROOT = pathlib.Path(__file__).resolve().parents[1]
CUE = pathlib.Path(os.environ.get("FROMANCE_SOURCE_CUE", ROOT / "orig/Idol Mahjong - Final Romance R (Japan).cue"))
TEXTS = ROOT / "translation/graphics/shop_msg.json"
GREYS = {6: (9, 9, 9), 9: (30, 30, 30), 10: (27, 27, 27), 11: (23, 23, 23), 12: (19, 19, 19), 13: (10, 10, 10)}


def test_lines_render_left_or_right_aligned_in_their_box():
    left = sg.render_line("어서 오세요!", 26 * 8, 2, "left", GREYS)
    right = sg.render_line("아이템 사기 :", 18 * 8, 142, "right", GREYS)
    assert left.shape == (16, 208) and right.shape == (16, 144)
    assert set(np.unique(left)) | set(np.unique(right)) <= set(GREYS)
    assert (left[:, :2] == sg.BG).all() and (right[:, 142:] == sg.BG).all() and (right[:, 136:142] != sg.BG).any()
    with pytest.raises(sg.ShopMsgError, match="wide"):
        sg.render_line("아주 아주 길고 긴 안내 문장이에요", 18 * 8, 142, "right", GREYS)
    with pytest.raises(sg.ShopMsgError, match="glyph"):
        sg.render_line("\U0001F600", 64, 2, "left", GREYS)


def test_texts_come_from_the_translation_file(tmp_path):
    texts = sg.load_texts(TEXTS)
    assert len(texts) == len(sg.LINES) + 1 and texts[-1] == "시작"
    bad = tmp_path / "bad.json"
    bad.write_text('{"entries": [{"id": "shop.welcome.1", "ja": "x", "ko": "a"}]}', encoding="utf-8")
    with pytest.raises(sg.ShopMsgError, match="6"):
        sg.load_texts(bad)
    import json
    d = json.loads(TEXTS.read_text(encoding="utf-8"))
    d["entries"][0], d["entries"][1] = d["entries"][1], d["entries"][0]
    bad.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(sg.ShopMsgError, match="id"):
        sg.load_texts(bad)


def test_button_text_must_fit_the_face():
    base = np.full((16, 32), sg.BUTTON_FACE_COLOUR, np.uint8)
    with pytest.raises(sg.ShopMsgError, match="fit"):
        sg.render_button("시작하기", base)


source = pytest.mark.skipif(not CUE.exists(), reason="원본 디스크 없음")


@pytest.fixture(scope="module")
def files():
    d = SourceDisc(CUE)
    try:
        entries = iso9660.list_files(d.user)
        return {n: d.read_file(entries[n].lba, entries[n].size) for n in ("1ST.BIN", "SL.BIN", "SHOPDAT.BIN")}
    finally:
        d.close()


@source
def test_layout_matches_the_shop_boxes(files):
    lay = sg.Layout.read(files["1ST.BIN"], files["SL.BIN"])
    assert [ln.cols for ln in lay.lines] == [26, 18, 18, 26, 26]
    for ln in lay.lines:
        assert ln.pool and all(lay.maps[ln.map].add <= u < lay.maps[ln.map].add + 256 for u in ln.pool)
    assert set(lay.colours) == set(GREYS)
    for m in lay.maps.values():                 # the background cell is plain box colour
        o = sg.cell_offset(lay, m.bg)
        assert set(files["SHOPDAT.BIN"][o:o + 32]) == {sg.BG << 4 | sg.BG}


@source
def test_original_lines_round_trip(files):
    lay = sg.Layout.read(files["1ST.BIN"], files["SL.BIN"])
    images = sg.decode_lines(lay, files["SL.BIN"], files["SHOPDAT.BIN"])
    sl, shop = sg.apply(lay, files["SL.BIN"], files["SHOPDAT.BIN"], images)
    assert all(np.array_equal(a, b) for a, b in zip(sg.decode_lines(lay, sl, shop), images))
    sg.check_protected(lay, files["SL.BIN"], files["SHOPDAT.BIN"], sl, shop)


@source
def test_korean_lines_are_drawn_and_strays_refused(files):
    lay = sg.Layout.read(files["1ST.BIN"], files["SL.BIN"])
    sl, shop = sg.build_files(files["1ST.BIN"], files["SL.BIN"], files["SHOPDAT.BIN"], sg.load_texts(TEXTS))
    want = [sg.render_line(t, ln.cols * 8, ln.x, ln.align, lay.colours) for t, ln in zip(sg.load_texts(TEXTS), lay.lines)]
    old = sg.decode_button(lay, files["SL.BIN"], files["SHOPDAT.BIN"])
    new = sg.decode_button(lay, sl, shop)
    x0, y0, x1, y1 = sg.BUTTON_FACE
    frame = np.ones(old.shape, bool); frame[y0:y1, x0:x1] = False
    assert (new[frame] == old[frame]).all() and not (new == old).all()       # only the face is redrawn
    face = new[y0:y1, x0:x1]
    assert set(np.unique(face)) <= {sg.BUTTON_INK, sg.BUTTON_FACE_COLOUR, sg.BUTTON_EDGE, sg.BUTTON_LIGHT}
    ink = np.argwhere(face == sg.BUTTON_INK)
    assert abs((ink[:, 1].min() + ink[:, 1].max()) / 2 - (x1 - x0 - 1) / 2) <= 1.5    # centred
    assert all(np.array_equal(a, b) for a, b in zip(sg.decode_lines(lay, sl, shop), want))
    button = lay.maps[61].off + 4 * 28 + 19                     # the C button cell of the welcome box
    bg_cell = sg.cell_offset(lay, lay.maps[61].bg)
    for bad_sl, bad_shop in ((sl[:button] + bytes([sl[button] ^ 1]) + sl[button + 1:], shop),
                             (sl, shop[:bg_cell] + bytes([shop[bg_cell] ^ 1]) + shop[bg_cell + 1:]),
                             (sl, shop[:0x100] + bytes([shop[0x100] ^ 1]) + shop[0x101:])):
        with pytest.raises(sg.ShopMsgError, match="protected"):
            sg.check_protected(lay, files["SL.BIN"], files["SHOPDAT.BIN"], bad_sl, bad_shop)


@source
def test_message_failures_are_reported_as_message_errors(files):
    lay = sg.Layout.read(files["1ST.BIN"], files["SL.BIN"])
    noisy = [np.random.default_rng(k).choice(list(GREYS), (16, ln.cols * 8)).astype(np.uint8)
             for k, ln in enumerate(lay.lines)]
    with pytest.raises(sg.ShopMsgError, match="cells"):
        sg.apply(lay, files["SL.BIN"], files["SHOPDAT.BIN"], noisy)
    sl, _ = sg.build_files(files["1ST.BIN"], files["SL.BIN"], files["SHOPDAT.BIN"], sg.load_texts(TEXTS))
    ln = lay.lines[0]
    frame = sl[lay.maps[61].off]                          # top-left frame cell of the welcome box
    bad = bytearray(sl); bad[sg._byte(lay, ln, 0, 3)] = frame
    with pytest.raises(sg.ShopMsgError, match="protected"):
        sg.check_targets(lay, bytes(bad))
    with pytest.raises(sg.ShopMsgError, match="copied"):
        sg.cell_offset(lay, 0x3F0)
