import os
import pathlib

import numpy as np
import pytest

from fromance import iso9660, shopmenu as sm
from fromance.disc import SourceDisc

ROOT = pathlib.Path(__file__).resolve().parents[1]
CUE = pathlib.Path(os.environ.get("FROMANCE_SOURCE_CUE", ROOT / "orig/Idol Mahjong - Final Romance R (Japan).cue"))
INK = sm.Ramp(ink=9, near=12, far=11, bg=10)


def test_render_uses_only_the_ramp_and_fits_the_box():
    img = sm.render_text("패 교환", 12 * 8, INK)
    assert img.shape == (16, 96)
    assert set(np.unique(img)) <= {9, 10, 11, 12} and (img == 9).any()
    assert (img[:, :1] == 10).all() and (img[:, -8:] == 10).all()      # left margin, unused right side
    with pytest.raises(sm.ShopMenuError, match="wide"):
        sm.render_text("패 교환 스티커 아주 긴 이름", 12 * 8, INK)
    with pytest.raises(sm.ShopMenuError, match="glyph"):
        sm.render_text("\U0001F600", 96, INK)


def test_cells_are_cut_row_major():
    img = np.arange(16 * 24, dtype=np.uint8).reshape(16, 24)
    cells = sm.cut_cells(img)
    assert len(cells) == 6 and cells[0] == bytes(img[:8, :8].flatten()) and cells[4] == bytes(img[8:, 8:16].flatten())


def test_assign_dedupes_reuses_background_and_refuses_overflow():
    bg = bytes([10]) * 64
    a, b = bytes([9]) * 64, bytes([11]) * 64
    units, writes = sm.assign([[a, bg, a, b]], pool=[100, 102, 104], bg_units=[50], bg_cells=[bg])
    assert units == [[100, 50, 100, 102]] and writes == {100: a, 102: b}
    with pytest.raises(sm.ShopMenuError, match="cells"):
        sm.assign([[a, b, bytes([12]) * 64, bytes([13]) * 64]], pool=[100, 102, 104], bg_units=[50], bg_cells=[bg])


def test_menu_texts_come_from_the_translation_file(tmp_path):
    texts = sm.load_texts(ROOT / "translation/graphics/shop_menu.json")
    assert len(texts) == 5 and all(texts)
    bad = tmp_path / "bad.json"
    bad.write_text('{"entries": [{"id": "shop.item.1", "ja": "x", "ko": ""}]}', encoding="utf-8")
    with pytest.raises(sm.ShopMenuError, match="5"):
        sm.load_texts(bad)
    import json
    d = json.loads((ROOT / "translation/graphics/shop_menu.json").read_text(encoding="utf-8"))
    d["entries"].reverse()
    bad.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(sm.ShopMenuError, match="id"):
        sm.load_texts(bad)


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
def test_layout_matches_the_shop_screen(files):
    lay = sm.Layout.read(files["1ST.BIN"], files["SL.BIN"])
    assert [it.cells for it in lay.items] == [19, 19, 14, 18, 18]
    assert len(lay.pool) == 144 and not set(lay.pool) & lay.protected
    assert [it.ramp.near - 160 - 16 * k for k, it in enumerate(lay.items)] == [12, 11, 12, 11, 12]


@source
def test_original_items_round_trip(files):
    lay = sm.Layout.read(files["1ST.BIN"], files["SL.BIN"])
    images = sm.decode_items(lay, files["SL.BIN"], files["SHOPDAT.BIN"])
    assert [im.shape for im in images] == [(16, it.cells * 8) for it in lay.items]
    sl, shop = sm.apply(lay, files["SL.BIN"], files["SHOPDAT.BIN"], images)
    assert len(sl) == len(files["SL.BIN"]) and len(shop) == len(files["SHOPDAT.BIN"])
    assert [np.array_equal(a, b) for a, b in zip(sm.decode_items(lay, sl, shop), images)] == [True] * 5
    sm.check_protected(lay, files["SL.BIN"], files["SHOPDAT.BIN"], sl, shop)


@source
def test_korean_items_are_drawn_and_nothing_else_moves(files):
    lay = sm.Layout.read(files["1ST.BIN"], files["SL.BIN"])
    texts = sm.load_texts(ROOT / "translation/graphics/shop_menu.json")
    images = [sm.render_text(t, it.cells * 8, it.ramp) for t, it in zip(texts, lay.items)]
    sl, shop = sm.apply(lay, files["SL.BIN"], files["SHOPDAT.BIN"], images)
    assert all(np.array_equal(a, b) for a, b in zip(sm.decode_items(lay, sl, shop), images))
    sm.check_protected(lay, files["SL.BIN"], files["SHOPDAT.BIN"], sl, shop)


def _tampered(lay, sl, shop):
    """(name, SL.BIN, SHOPDAT.BIN) with one change the protected check must refuse."""
    bg = lay.items[0].bg_units[0]
    item3_gap = lay.map_off + 2 * (lay.items[2].row * lay.stride + sm.TEXT_X0 + lay.items[2].cells)
    price = lay.map_off + 2 * (lay.items[0].row * lay.stride + 23)
    text0 = lay.map_off + 2 * (lay.items[0].row * lay.stride + sm.TEXT_X0)
    flip = lambda b, i: bytes(b[:i]) + bytes([b[i] ^ 1]) + bytes(b[i + 1:])
    point = lambda b, i, u: bytes(b[:i]) + (u - lay.add).to_bytes(2, "big") + bytes(b[i + 2:])
    return [
        ("background cell", sl, flip(shop, sm.cell_offset(bg) + 5)),
        ("frame cell second unit", sl, flip(shop, sm.cell_offset(4) + 40)),
        ("outside the cell block", sl, flip(shop, 0x100)),
        ("price word", flip(sl, price + 1), shop),
        ("item 3 column 18", flip(sl, item3_gap + 1), shop),
        ("text word to a frame cell", point(sl, text0, 4), shop),
    ]


@source
def test_protected_check_refuses_every_kind_of_stray_change(files):
    lay = sm.Layout.read(files["1ST.BIN"], files["SL.BIN"])
    texts = sm.load_texts(ROOT / "translation/graphics/shop_menu.json")
    sl, shop = sm.build_files(files["1ST.BIN"], files["SL.BIN"], files["SHOPDAT.BIN"], texts)
    for name, bad_sl, bad_shop in _tampered(lay, sl, shop):
        with pytest.raises(sm.ShopMenuError, match="protected"):
            sm.check_protected(lay, files["SL.BIN"], files["SHOPDAT.BIN"], bad_sl, bad_shop)
            pytest.fail(name)


@source
def test_pool_cells_are_whole_and_free(files):
    lay = sm.Layout.read(files["1ST.BIN"], files["SL.BIN"])
    assert all(u % 2 == 0 and u not in lay.protected and u + 1 not in lay.protected for u in lay.pool)
