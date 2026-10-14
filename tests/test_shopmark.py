import os
import pathlib

import numpy as np
import pytest

from fromance import iso9660, shopmark as mk
from fromance.disc import SourceDisc

ROOT = pathlib.Path(__file__).resolve().parents[1]
CUE = pathlib.Path(os.environ.get("FROMANCE_SOURCE_CUE", ROOT / "orig/Idol Mahjong - Final Romance R (Japan).cue"))


def test_arrow_icon_is_a_symmetric_double_arrow_in_the_mark_colours():
    icon = mk.arrow_icon()
    assert icon.shape == (16, 16) and set(np.unique(icon)) <= set(mk.COLOURS)
    body = np.isin(icon, (mk.LIGHT, mk.FILL, mk.SHADE))
    assert (body == body[:, ::-1]).all()                 # both heads alike
    assert body[:, 1].any() and body[:, 14].any() and not body[:, 0].any() and not body[:, 15].any()
    assert body[7:9, 4:12].all()                         # 2 px shaft
    assert (icon[0] == mk.BG).all() and (icon[:, 0] == mk.BG).all()


source = pytest.mark.skipif(not CUE.exists(), reason="원본 디스크 없음")


@pytest.fixture(scope="module")
def files():
    d = SourceDisc(CUE)
    try:
        entries = iso9660.list_files(d.user)
        return {n: d.read_file(entries[n].lba, entries[n].size) for n in ("SL.BIN", "SHOPDAT.BIN")}
    finally:
        d.close()


@source
def test_mark_is_where_the_game_reads_it_and_only_it_changes(files):
    off = mk.mark_offset(files["SL.BIN"], len(files["SHOPDAT.BIN"]))
    assert off == 0x80
    assert set(files["SHOPDAT.BIN"][off:off + 256]) == set(mk.COLOURS)
    out = mk.build_file(files["SL.BIN"], files["SHOPDAT.BIN"])
    assert np.array_equal(np.frombuffer(out[off:off + 256], np.uint8).reshape(16, 16), mk.arrow_icon())
    mask = mk.editable(off, len(out))
    diff = np.frombuffer(out, np.uint8) != np.frombuffer(files["SHOPDAT.BIN"], np.uint8)
    assert diff.any() and not (diff & ~mask).any()
    bad = bytearray(out); bad[off + 3] = 0
    with pytest.raises(mk.ShopMarkError, match="colour"):
        mk.check_colours(off, bytes(bad))


@source
def test_mark_refuses_a_changed_table_or_picture(files):
    sl, shop = files["SL.BIN"], files["SHOPDAT.BIN"]
    rec = mk.SPRITE_TABLE - mk.SL_BASE + 8 * mk.MARK_RECORD
    wrong_mode = sl[:rec + 3] + bytes([3]) + sl[rec + 4:]
    with pytest.raises(mk.ShopMarkError, match="record"):
        mk.mark_offset(wrong_mode, len(shop))
    nxt = mk.SPRITE_TABLE - mk.SL_BASE + 8 * (mk.MARK_RECORD + 1) + 4
    overlap = sl[:nxt] + (mk.SHOP_LOAD + 0x100).to_bytes(4, "big") + sl[nxt + 4:]
    with pytest.raises(mk.ShopMarkError, match="overlaps"):
        mk.mark_offset(overlap, len(shop))
    with pytest.raises(mk.ShopMarkError, match="outside"):
        mk.mark_offset(sl, 0x100)
    bad = shop[:0x90] + bytes([0]) + shop[0x91:]
    with pytest.raises(mk.ShopMarkError, match="colours"):
        mk.build_file(sl, bad)
