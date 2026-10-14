import os
import pathlib

import numpy as np
import pytest

from fromance import iso9660, shopdesc as sd
from fromance.disc import SourceDisc

ROOT = pathlib.Path(__file__).resolve().parents[1]
CUE = pathlib.Path(os.environ.get("FROMANCE_SOURCE_CUE", ROOT / "orig/Idol Mahjong - Final Romance R (Japan).cue"))
TEXTS = ROOT / "translation/graphics/shop_desc.json"
GREYS = {5: (30, 30, 30), 6: (9, 9, 9), 9: (27, 27, 27), 10: (23, 23, 23), 11: (19, 19, 19), 12: (10, 10, 10)}


def test_panel_render_uses_only_panel_colours():
    img = sd.render_panel(["필요 없는 패를", "바꿀 수 있는", "스티커예요."], GREYS)
    assert img.shape == (sd.PANEL_H, sd.PANEL_W)
    assert set(np.unique(img)) <= set(GREYS) and (img == sd.INK).any()
    assert (img[:, :4] == sd.BG).all() and (img[:3] == sd.BG).all()
    with pytest.raises(sd.ShopDescError, match="wide"):
        sd.render_panel(["아주 아주 길고 긴 설명 문장이에요", "", ""], GREYS)
    with pytest.raises(sd.ShopDescError, match="lines"):
        sd.render_panel(["하나", "둘"], GREYS)
    with pytest.raises(sd.ShopDescError, match="glyph"):
        sd.render_panel(["\U0001F600", "", ""], GREYS)


def test_texts_come_from_the_translation_file(tmp_path):
    texts = sd.load_texts(TEXTS)
    assert len(texts) == sd.PICTURES and all(len(t) == 3 for t in texts)
    bad = tmp_path / "bad.json"
    bad.write_text('{"entries": [{"id": "x", "ja": ["a"], "ko": ["a", "b"]}]}', encoding="utf-8")
    with pytest.raises(sd.ShopDescError, match="7"):
        sd.load_texts(bad)
    import json
    d = json.loads(TEXTS.read_text(encoding="utf-8"))
    d["entries"].reverse()
    bad.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(sd.ShopDescError, match="id"):
        sd.load_texts(bad)


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
def test_pictures_and_panels_are_where_the_game_reads_them(files):
    offs = sd.picture_offsets(files["SL.BIN"], len(files["SHOPDAT.BIN"]))
    assert offs == [0x1580 + 0x4600 * k for k in range(7)]
    with pytest.raises(sd.ShopDescError, match="end"):
        sd.picture_offsets(files["SL.BIN"], 0x1F000)
    for o in offs:
        sd.check_panel(files["SHOPDAT.BIN"], o)


@source
def test_original_panels_round_trip_and_korean_changes_only_panels(files):
    shop = files["SHOPDAT.BIN"]
    offs = sd.picture_offsets(files["SL.BIN"], len(shop))
    same = sd.apply(shop, offs, [sd.read_panel(shop, o) for o in offs])
    assert same == shop
    texts = sd.load_texts(TEXTS)
    out = sd.build_file(files["1ST.BIN"], files["SL.BIN"], shop, texts)
    greys = sd.panel_colours(files["1ST.BIN"], files["SL.BIN"])
    assert [np.array_equal(sd.read_panel(out, o), sd.render_panel(t, greys)) for o, t in zip(offs, texts)] == [True] * 7
    sd.check_protected(offs, shop, out)
    for i in (offs[0], offs[0] + 224 * 40 + 50, offs[6] + 224 * 80, 0x100, offs[3] + 91):
        bad = bytearray(out); bad[i] ^= 1
        with pytest.raises(sd.ShopDescError, match="protected"):
            sd.check_protected(offs, shop, bytes(bad))
    bad = bytearray(out); bad[offs[2] + (sd.PANEL_Y + 10) * sd.PIC_W + sd.PANEL_X + 10] = 99
    with pytest.raises(sd.ShopDescError, match="not a panel colour"):
        sd.check_protected(offs, shop, bytes(bad))


@source
def test_panel_frame_must_not_touch_text_colours(files):
    offs = sd.picture_offsets(files["SL.BIN"], len(files["SHOPDAT.BIN"]))
    bad = bytearray(files["SHOPDAT.BIN"]); bad[offs[0] + (sd.PANEL_Y - 1) * sd.PIC_W + sd.PANEL_X + 3] = sd.BG
    with pytest.raises(sd.ShopDescError, match="wider"):
        sd.check_panel(bytes(bad), offs[0])
